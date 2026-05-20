"""Read-only Phase 3.7 media API smoke validation.

This script verifies user-triggerable media browsing endpoints against the
Phase 3.5 Tier-1000 import scope. It performs no DB or file writes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SOURCE_LABEL = "violet:tier1000:phase3.5"


def _content_class_value(value: Any) -> str:
    if value is None:
        return "unclassified"
    return getattr(value, "value", str(value))


def _json_shape(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {"type": "dict", "keys": sorted(str(k) for k in payload.keys())[:20]}
    if isinstance(payload, list):
        return {"type": "list", "length": len(payload)}
    return {"type": type(payload).__name__}


def _safe_body_preview(response: requests.Response) -> str:
    text = response.text[:300]
    return text.replace("\r", " ").replace("\n", " ")


def _record_failure(
    failures: list[dict[str, Any]],
    *,
    endpoint: str,
    media_id: int | None = None,
    status_code: int | None = None,
    error: str | None = None,
    response: requests.Response | None = None,
) -> None:
    item: dict[str, Any] = {"endpoint": endpoint}
    if media_id is not None:
        item["media_id"] = media_id
    if status_code is not None:
        item["status_code"] = status_code
    if error:
        item["error"] = error
    if response is not None:
        item["body_preview"] = _safe_body_preview(response)
    failures.append(item)


def _get_json(
    session: requests.Session,
    url: str,
    *,
    endpoint: str,
    failures: list[dict[str, Any]],
    media_id: int | None = None,
    timeout: float = 20.0,
) -> tuple[bool, Any]:
    try:
        response = session.get(url, timeout=timeout)
    except Exception as exc:
        _record_failure(failures, endpoint=endpoint, media_id=media_id, error=str(exc))
        return False, None
    if response.status_code != 200:
        _record_failure(
            failures,
            endpoint=endpoint,
            media_id=media_id,
            status_code=response.status_code,
            response=response,
        )
        return False, None
    try:
        return True, response.json()
    except Exception as exc:
        _record_failure(
            failures,
            endpoint=endpoint,
            media_id=media_id,
            status_code=response.status_code,
            error=f"invalid_json: {exc}",
            response=response,
        )
        return False, None


def _get_stream_status(
    session: requests.Session,
    url: str,
    *,
    endpoint: str,
    failures: list[dict[str, Any]],
    media_id: int,
    timeout: float = 20.0,
) -> bool:
    try:
        with session.get(url, timeout=timeout, stream=True) as response:
            if response.status_code != 200:
                _record_failure(
                    failures,
                    endpoint=endpoint,
                    media_id=media_id,
                    status_code=response.status_code,
                    response=response,
                )
                return False
            return True
    except Exception as exc:
        _record_failure(failures, endpoint=endpoint, media_id=media_id, error=str(exc))
        return False


def _select_target_media(source_label: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from app import database
    from app.models import Media

    database.init_engine()
    if database.SessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")

    db = database.SessionLocal()
    try:
        rows = (
            db.query(Media.id, Media.content_class)
            .filter(Media.source == source_label)
            .order_by(Media.id.asc())
            .all()
        )
        media = [
            {"id": int(row.id), "content_class": _content_class_value(row.content_class)}
            for row in rows
        ]
    finally:
        db.close()

    distribution: dict[str, int] = {}
    for item in media:
        distribution[item["content_class"]] = distribution.get(item["content_class"], 0) + 1
    return media, distribution


def _sample_file_ids(media: list[dict[str, Any]]) -> list[int]:
    by_class: dict[str, list[int]] = {}
    for item in media:
        by_class.setdefault(item["content_class"], []).append(item["id"])

    sample: list[int] = []
    sample.extend(by_class.get("anime", [])[:10])
    unknown = by_class.get("unknown", [])
    sample.extend(unknown if len(unknown) <= 21 else unknown[:10])
    sample.extend(by_class.get("non_anime", []))
    sample.extend([item["id"] for item in media[-5:]])
    sample.extend([1703, 1695, 914, 1547, 1552])
    return sorted(set(i for i in sample if any(m["id"] == i for m in media)))


def _login_admin(session: requests.Session, base_url: str, username: str, password: str | None) -> dict[str, Any]:
    if not password:
        return {"attempted": False, "success": False, "reason": "no_password"}
    try:
        response = session.post(
            f"{base_url}/api/admin/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        return {"attempted": True, "success": response.status_code == 200, "status_code": response.status_code}
    except Exception as exc:
        return {"attempted": True, "success": False, "error": str(exc)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    media, distribution = _select_target_media(args.source_label)
    media_ids = [item["id"] for item in media]

    if args.expected_media_count is not None and len(media_ids) != args.expected_media_count:
        raise SystemExit(
            f"Expected {args.expected_media_count} target media for {args.source_label}, found {len(media_ids)}"
        )

    session = requests.Session()
    session.trust_env = False
    admin_login = _login_admin(session, base_url, args.admin_username, args.admin_password)

    start_time = time.time()
    report: dict[str, Any] = {
        "source_label": args.source_label,
        "target_media_count": len(media_ids),
        "content_class_distribution": distribution,
        "base_url_label": "local_validation_server",
        "started_at_epoch": start_time,
        "admin_login": admin_login,
        "metadata": {"checked": 0, "success": 0, "failed": 0, "failures": [], "representative_shape": None},
        "media_detail": {"checked": 0, "success": 0, "failed": 0, "failures": [], "representative_shape": None},
        "thumbnail": {"checked": 0, "success": 0, "failed": 0, "failures": []},
        "file_sample": {"checked": 0, "success": 0, "failed": 0, "failures": [], "sample_ids": []},
        "content_class_filters": {"checked": 0, "success": 0, "failed": 0, "failures": [], "items": []},
        "search_localization": {"checked": 0, "success": 0, "failed": 0, "failures": [], "items": []},
        "ai_review_tag_api": {"checked": 0, "success": 0, "failed": 0, "failures": [], "items": []},
    }

    for media_id in media_ids:
        section = report["metadata"]
        section["checked"] += 1
        ok, payload = _get_json(
            session,
            f"{base_url}/api/media/{media_id}/metadata",
            endpoint="metadata",
            media_id=media_id,
            failures=section["failures"],
            timeout=args.metadata_timeout,
        )
        if ok:
            section["success"] += 1
            if section["representative_shape"] is None:
                section["representative_shape"] = _json_shape(payload)
        else:
            section["failed"] += 1

    for media_id in media_ids:
        section = report["media_detail"]
        section["checked"] += 1
        ok, payload = _get_json(
            session,
            f"{base_url}/api/media/{media_id}",
            endpoint="media_detail",
            media_id=media_id,
            failures=section["failures"],
        )
        if ok:
            section["success"] += 1
            if section["representative_shape"] is None:
                section["representative_shape"] = _json_shape(payload)
        else:
            section["failed"] += 1

    for media_id in media_ids:
        section = report["thumbnail"]
        section["checked"] += 1
        if _get_stream_status(
            session,
            f"{base_url}/api/media/{media_id}/thumbnail",
            endpoint="thumbnail",
            media_id=media_id,
            failures=section["failures"],
        ):
            section["success"] += 1
        else:
            section["failed"] += 1

    sample_ids = _sample_file_ids(media)
    report["file_sample"]["sample_ids"] = sample_ids
    for media_id in sample_ids:
        section = report["file_sample"]
        section["checked"] += 1
        if _get_stream_status(
            session,
            f"{base_url}/api/media/{media_id}/file",
            endpoint="file",
            media_id=media_id,
            failures=section["failures"],
            timeout=args.file_timeout,
        ):
            section["success"] += 1
        else:
            section["failed"] += 1

    filter_values = ["anime", "unknown", "non_anime", "anime,unknown"]
    for content_class in filter_values:
        section = report["content_class_filters"]
        section["checked"] += 1
        url = f"{base_url}/api/media?{urlencode({'content_class': content_class, 'limit': 50})}"
        ok, payload = _get_json(session, url, endpoint=f"content_class:{content_class}", failures=section["failures"])
        if ok:
            section["success"] += 1
            section["items"].append({
                "content_class": content_class,
                "total": payload.get("total") if isinstance(payload, dict) else None,
                "items": len(payload.get("items", [])) if isinstance(payload, dict) else None,
            })
        else:
            section["failed"] += 1

    search_queries = [
        ("canonical:hetero", "hetero"),
        ("canonical:1girl", "1girl"),
        ("canonical:long_hair", "long_hair"),
        ("localized:zh_hetero", "\u5f02\u6027\u604b"),
    ]
    for query_label, query in search_queries:
        section = report["search_localization"]
        section["checked"] += 1
        url = f"{base_url}/api/search?{urlencode({'q': query, 'limit': 50})}"
        ok, payload = _get_json(session, url, endpoint=f"search:{query_label}", failures=section["failures"])
        if ok:
            section["success"] += 1
            section["items"].append({
                "query_label": query_label,
                "total": payload.get("total") if isinstance(payload, dict) else None,
                "items": len(payload.get("items", [])) if isinstance(payload, dict) else None,
            })
        else:
            section["failed"] += 1

    tag_endpoints = [
        ("ai_review", "/api/admin/ai-tags/review?limit=10"),
        ("localization_stats", "/api/admin/tag-localization/stats"),
        ("translations_batch", "/api/tags/translations/batch?names=hetero,1girl,long_hair"),
        ("tag_autocomplete", "/api/tags/autocomplete?q=hetero"),
    ]
    for label, path in tag_endpoints:
        section = report["ai_review_tag_api"]
        section["checked"] += 1
        ok, payload = _get_json(session, f"{base_url}{path}", endpoint=label, failures=section["failures"])
        if ok:
            section["success"] += 1
            item: dict[str, Any] = {"endpoint": label, "shape": _json_shape(payload)}
            if isinstance(payload, dict):
                if "total" in payload:
                    item["total"] = payload["total"]
                if "pending" in payload:
                    item["pending"] = payload["pending"]
            section["items"].append(item)
        else:
            section["failed"] += 1

    report["finished_at_epoch"] = time.time()
    report["duration_seconds"] = round(report["finished_at_epoch"] - start_time, 3)
    report["success"] = all(
        report[name]["failed"] == 0
        for name in [
            "metadata",
            "media_detail",
            "thumbnail",
            "file_sample",
            "content_class_filters",
            "search_localization",
            "ai_review_tag_api",
        ]
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--source-label", default=SOURCE_LABEL)
    parser.add_argument("--expected-media-count", type=int, default=995)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default=None)
    parser.add_argument("--metadata-timeout", type=float, default=30.0)
    parser.add_argument("--file-timeout", type=float, default=30.0)
    parser.add_argument("--report-json", type=Path, default=None)
    args = parser.parse_args()

    report = validate(args)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
