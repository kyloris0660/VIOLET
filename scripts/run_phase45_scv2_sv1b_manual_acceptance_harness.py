#!/usr/bin/env python3
"""Build and serve the private SCV2-SV1B 40-case manual acceptance harness."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts import run_phase45_scv2_ml1_multilingual_alias_source_metadata_closure as ml1  # noqa: E402
from scripts import run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure as sv1b  # noqa: E402


HARNESS_VERSION = "sv1b_manual_acceptance_harness_v1"
DEFAULT_PORT = 8031
CATEGORY_COUNTS = {
    "pixiv_metadata": 12,
    "creator_clustering": 8,
    "shared_name_cannot_link": 6,
    "ai_tag_localization": 8,
    "search_and_negative": 6,
}


class ManualAcceptanceHarnessError(RuntimeError):
    pass


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def _safe_media_label(media_hash: str) -> str:
    return sv1b.sha256_payload({"media": str(media_hash)})


def _case(
    case_id: str,
    category: str,
    *,
    media_hash: str,
    title: str,
    expected_behavior: str,
    actual_result: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "title": title,
        "safe_media_label": _safe_media_label(media_hash),
        "expected_behavior": expected_behavior,
        "actual_result": dict(actual_result),
        "provenance": dict(provenance),
    }


def _fallback_media_hash(session: Any) -> str:
    value = session.execute(text("""
        SELECT hash FROM blombooru_media
        WHERE hash IS NOT NULL AND (mime_type LIKE 'image/%' OR CAST(file_type AS text)='image')
        ORDER BY hash LIMIT 1
    """)).scalar()
    if not value:
        raise ManualAcceptanceHarnessError("manual_acceptance_image_media_missing")
    return str(value)


def _pixiv_metadata_cases(session: Any) -> list[dict[str, Any]]:
    rows = list(session.execute(text("""
        SELECT r.id,r.provider_record_key,r.source_work_id,r.source_page_index,
               r.artist_id,r.artist_name,r.title,r.status,r.retrieved_at,r.provenance,
               m.hash,
               (SELECT COUNT(*) FROM blombooru_source_tag_observations o
                WHERE o.source_metadata_record_id=r.id AND o.status IN ('observed','active','accepted')) AS tag_count
        FROM blombooru_source_metadata_records r
        JOIN blombooru_media m ON m.id=r.media_id
        WHERE r.provider='pixiv'
          AND r.status IN ('metadata_complete','observed','active','accepted')
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
          AND r.source_work_id IS NOT NULL AND r.source_page_index IS NOT NULL
        ORDER BY r.provider_record_key,m.hash LIMIT 12
    """)).mappings())
    cases = []
    for index, row in enumerate(rows, 1):
        provenance = row.get("provenance") or {}
        cases.append(_case(
            f"A{index:02d}",
            "pixiv_metadata",
            media_hash=str(row["hash"]),
            title=f"Pixiv work/page metadata #{index}",
            expected_behavior="The displayed work, page, creator, title, and provider tags belong to this exact media page.",
            actual_result={
                "work_id": row.get("source_work_id"),
                "page_index": row.get("source_page_index"),
                "creator_stable_id": row.get("artist_id"),
                "creator_display_name": row.get("artist_name"),
                "work_title": row.get("title"),
                "provider_tag_count": int(row.get("tag_count") or 0),
                "record_status": row.get("status"),
            },
            provenance={
                "provider": "pixiv",
                "metadata_status": row.get("status"),
                "retrieved_at": row.get("retrieved_at"),
                "parser_version": provenance.get("parser_version"),
                "policy_version": provenance.get("policy_version"),
            },
        ))
    return cases


def _creator_clustering_cases(session: Any) -> list[dict[str, Any]]:
    rows = list(session.execute(text("""
        SELECT c.id,c.concept_key,c.primary_display_name,c.evidence_summary_json,
               COUNT(DISTINCT a.id) FILTER (WHERE a.status='active') AS alias_count,
               ARRAY_AGG(DISTINCT a.alias_value ORDER BY a.alias_value)
                 FILTER (WHERE a.status='active') AS aliases,
               COUNT(DISTINCT e.media_id) FILTER
                 (WHERE e.status='active' AND e.evidence_type='trusted_creator_media_support') AS support_count,
               MIN(m.hash) FILTER
                 (WHERE e.status='active' AND e.evidence_type='trusted_creator_media_support'
                  AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')) AS media_hash
        FROM blombooru_source_concepts c
        JOIN blombooru_source_concept_aliases a ON a.concept_id=c.id
        LEFT JOIN blombooru_source_concept_evidence e ON e.concept_id=c.id
        LEFT JOIN blombooru_media m ON m.id=e.media_id
        WHERE c.status='active' AND c.concept_type_hint IN ('artist','creator','person')
        GROUP BY c.id,c.concept_key,c.primary_display_name
        HAVING COUNT(DISTINCT a.id) FILTER (WHERE a.status='active')>=2
           AND COUNT(DISTINCT e.media_id) FILTER
               (WHERE e.status='active' AND e.evidence_type='trusted_creator_media_support')>=1
           AND COUNT(DISTINCT e.media_id) FILTER
               (WHERE e.status='active' AND e.evidence_type='trusted_creator_media_support'
                AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image'))>=1
        ORDER BY c.concept_key LIMIT 8
    """)).mappings())
    cases = []
    for index, row in enumerate(rows, 1):
        summary = row.get("evidence_summary_json") or {}
        cases.append(_case(
            f"B{index:02d}",
            "creator_clustering",
            media_hash=str(row["media_hash"]),
            title=f"Creator cluster #{index}",
            expected_behavior="Trusted names/accounts form a star around one stable creator anchor without merging another stable creator.",
            actual_result={
                "primary_display_name": row.get("primary_display_name"),
                "aliases": list(row.get("aliases") or ()),
                "alias_count": int(row.get("alias_count") or 0),
                "media_support_count": int(row.get("support_count") or 0),
                "concept_ref": sv1b.sha256_payload(str(row.get("concept_key") or "")),
            },
            provenance={
                "stable_identity_ref": sv1b.sha256_payload(
                    str(summary.get("stable_identity_fingerprint") or row.get("concept_key") or "")
                ),
                "evidence_policy": summary.get("policy_version"),
                "source_layer_only": True,
            },
        ))
    return cases


def _shared_name_cases(session: Any) -> list[dict[str, Any]]:
    rows = list(session.execute(text("""
        SELECT i.search_key,COUNT(DISTINCT i.concept_id) AS concept_count,
               COUNT(DISTINCT f.pair_id) FILTER
                 (WHERE f.relation='cannot_link' AND f.status='blocked') AS cannot_pair_count,
               MIN(m.hash) AS media_hash
        FROM blombooru_source_concept_search_index i
        JOIN blombooru_source_concepts c ON c.id=i.concept_id AND c.status='active'
        LEFT JOIN blombooru_source_concept_evidence e
          ON e.concept_id=c.id AND e.status='active' AND e.media_id IS NOT NULL
        LEFT JOIN blombooru_media m ON m.id=e.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        LEFT JOIN blombooru_source_concept_fallback_search_index f
          ON f.alias_key=i.search_key
        WHERE i.status='active'
        GROUP BY i.search_key
        HAVING COUNT(DISTINCT i.concept_id)>1 AND MIN(m.hash) IS NOT NULL
        ORDER BY concept_count DESC,i.search_key LIMIT 6
    """)).mappings())
    cases = []
    for index, row in enumerate(rows, 1):
        actual_ids = ml1.runtime_and_terms(session, str(row["search_key"]))
        cases.append(_case(
            f"C{index:02d}",
            "shared_name_cannot_link",
            media_hash=str(row["media_hash"]),
            title=f"Shared-name/cannot-link #{index}",
            expected_behavior="A shared display name may return a supported result union, but cannot-linked creator concepts must remain separate identities.",
            actual_result={
                "shared_alias": row.get("search_key"),
                "separate_active_concept_count": int(row.get("concept_count") or 0),
                "cannot_link_pair_count": int(row.get("cannot_pair_count") or 0),
                "runtime_result_count": len(actual_ids),
                "identity_union_created": False,
            },
            provenance={
                "source": "active SourceConcept search index plus cannot-link overlay",
                "search_result_union_is_identity_union": False,
            },
        ))
    return cases


def _localization_cases(session: Any) -> list[dict[str, Any]]:
    rows = list(session.execute(text("""
        SELECT tr.canonical_name,tr.display_name,tr.source,tr.status,tr.category,
               MIN(m.hash) AS media_hash,COUNT(DISTINCT mt.media_id) AS media_count
        FROM blombooru_tag_translations tr
        JOIN blombooru_tags t ON t.name=tr.canonical_name
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        JOIN blombooru_media m ON m.id=mt.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        WHERE tr.language='zh-CN' AND tr.status='translated' AND COALESCE(tr.display_name,'')<>''
          AND NOT EXISTS (
            SELECT 1 FROM blombooru_tag_translations other
            WHERE other.language=tr.language AND other.status<>'rejected'
              AND other.display_name=tr.display_name
              AND other.canonical_name<>tr.canonical_name
          )
        GROUP BY tr.canonical_name,tr.display_name,tr.source,tr.status,tr.category
        ORDER BY tr.canonical_name LIMIT 8
    """)).mappings())
    cases = []
    for index, row in enumerate(rows, 1):
        actual_ids = ml1.runtime_and_terms(session, str(row["display_name"]))
        cases.append(_case(
            f"D{index:02d}",
            "ai_tag_localization",
            media_hash=str(row["media_hash"]),
            title=f"AI-tag Chinese localization #{index}",
            expected_behavior="The accepted Chinese label preserves the original canonical tag and returns every independently supported media row.",
            actual_result={
                "canonical_tag": row.get("canonical_name"),
                "chinese_display_name": row.get("display_name"),
                "translation_status": row.get("status"),
                "independent_media_count": int(row.get("media_count") or 0),
                "runtime_result_count": len(actual_ids),
            },
            provenance={
                "translation_source": row.get("source"),
                "tag_category": row.get("category"),
                "media_tag_source": "ai_wd",
            },
        ))
    return cases


def _search_cases(session: Any, output: Path) -> list[dict[str, Any]]:
    rows = sv1b.read_json(output / "primary-search-workload-and-results-private.json")
    desired = (
        ("creator_and_character", 2),
        ("creator_and_work_title", 1),
        ("provider_source_tag", 1),
        ("negative_query", 2),
    )
    selected = [
        row
        for category, count in desired
        for row in [item for item in rows if item.get("category") == category][:count]
    ]
    fallback_hash = _fallback_media_hash(session)
    cases = []
    for index, row in enumerate(selected, 1):
        terms = [str(value) for value in row.get("terms") or ()]
        actual_ids = ml1.runtime_and_terms(session, *terms)
        media_hash = fallback_hash
        if actual_ids:
            value = session.execute(text(
                "SELECT hash FROM blombooru_media WHERE id=ANY(:ids) "
                "AND (mime_type LIKE 'image/%' OR CAST(file_type AS text)='image') ORDER BY hash LIMIT 1"
            ), {"ids": sorted(actual_ids)}).scalar()
            if value:
                media_hash = str(value)
        cases.append(_case(
            f"E{index:02d}",
            "search_and_negative",
            media_hash=media_hash,
            title=f"Search/AND/negative #{index}",
            expected_behavior=(
                "The multi-term query equals the independent media-level intersection."
                if len(terms) > 1
                else "The negative query returns no unsupported media."
                if row.get("category") == "negative_query"
                else "The query returns the independently supported media set."
            ),
            actual_result={
                "query_terms": terms,
                "expected_result_count": int(row.get("expected_result_count") or 0),
                "actual_result_count": len(actual_ids),
                "and_leakage_count": int(row.get("and_leakage_count") or 0),
                "supported_query_missing_result_count": int(
                    row.get("supported_query_missing_result_count") or 0
                ),
            },
            provenance={
                "runtime_path": "parse_search_query + apply_endpoint_equivalent_text_search",
                "independent_expected_membership": True,
                "case_ref": row.get("case_ref"),
            },
        ))
    return cases


def _database_binding(database: str) -> dict[str, Any]:
    tables = (
        "blombooru_media",
        "blombooru_tags",
        "blombooru_media_tags",
        "blombooru_tag_translations",
        *sv1b.CORE_SOURCE_TABLES,
    )
    proof = sv1b.database_fingerprint(database, tables)
    return {
        "database_identity": database,
        "fingerprint": proof["fingerprint"],
        "media_count": proof["tables"]["blombooru_media"]["count"],
    }


def _proof_bindings(proofs: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    acquisition = proofs["acquisition-closure-and-package-proof.json"]
    localization = proofs["localization-baseline-proof.json"]
    primary_graph = proofs["primary-source-graph-derivation-proof.json"]
    replay_graph = proofs["replay-source-graph-derivation-proof.json"]
    graph_comparison = proofs["primary-replay-source-graph-comparison-proof.json"]
    primary_search = proofs["primary-search-validation-proof.json"]
    replay_search = proofs["replay-search-validation-proof.json"]
    search_comparison = proofs["primary-replay-search-comparison-proof.json"]
    return {
        "acquired_metadata_package_fingerprint": str(
            (acquisition.get("package") or {}).get(
                "acquired_metadata_package_fingerprint"
            )
            or ""
        ),
        "graph_fingerprint": sv1b.sha256_payload({
            "primary": primary_graph,
            "replay": replay_graph,
            "comparison": graph_comparison,
        }),
        "search_fingerprint": sv1b.sha256_payload({
            "primary": primary_search,
            "replay": replay_search,
            "comparison": search_comparison,
        }),
        "localization_fingerprint": sv1b.sha256_payload(localization),
    }


def build_harness(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    output = output.resolve()
    sv1b.validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    required_proofs = (
        "acquisition-closure-and-package-proof.json",
        "localization-baseline-proof.json",
        "primary-source-graph-derivation-proof.json",
        "replay-source-graph-derivation-proof.json",
        "primary-replay-source-graph-comparison-proof.json",
        "primary-search-validation-proof.json",
        "replay-search-validation-proof.json",
        "primary-replay-search-comparison-proof.json",
    )
    proofs = {name: sv1b.read_json(output / name) for name in required_proofs}
    failed = [name for name, proof in proofs.items() if proof.get("passed") is not True and name != "localization-baseline-proof.json"]
    if proofs["localization-baseline-proof.json"].get("localization_complete") is not True:
        failed.append("localization-baseline-proof.json")
    if failed:
        raise ManualAcceptanceHarnessError(f"manual_acceptance_required_proof_failed:{sorted(failed)}")

    primary_binding = _database_binding(primary_database)
    replay_binding = _database_binding(replay_database)
    if (
        primary_binding["media_count"] != sv1b.EXPECTED_MEDIA_COUNT
        or replay_binding["media_count"] != sv1b.EXPECTED_MEDIA_COUNT
    ):
        raise ManualAcceptanceHarnessError("manual_acceptance_database_membership_invalid")

    engine = sv1b.engine_for(primary_database)
    session = sessionmaker(bind=engine)()
    try:
        cases = [
            *_pixiv_metadata_cases(session),
            *_creator_clustering_cases(session),
            *_shared_name_cases(session),
            *_localization_cases(session),
            *_search_cases(session, output),
        ]
        session.rollback()
    finally:
        session.close()
        engine.dispose()
    counts = Counter(str(row["category"]) for row in cases)
    if len(cases) != 40 or dict(counts) != CATEGORY_COUNTS:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_case_composition_invalid:count={len(cases)}:categories={dict(counts)}"
        )
    case_ids = [str(row["case_id"]) for row in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ManualAcceptanceHarnessError("manual_acceptance_case_id_duplicate")

    manual_root = output / "manual-acceptance"
    manual_root.mkdir(parents=True, exist_ok=False)
    case_manifest_path = manual_root / "case-manifest-private.json"
    sv1b.write_json(case_manifest_path, cases)
    case_fingerprint = sv1b.sha256_payload(cases)
    bindings = {
        "git_head": _git_head(),
        "primary_database": primary_binding,
        "replay_database": replay_binding,
        "media_manifest_fingerprint": sv1b.ACCEPTED_MANIFEST_FINGERPRINT,
        **_proof_bindings(proofs),
        "acceptance_case_manifest_fingerprint": case_fingerprint,
    }
    bindings["binding_fingerprint"] = sv1b.sha256_payload(bindings)
    proof = {
        "harness_version": HARNESS_VERSION,
        "required": True,
        "status": "pending_user",
        "case_count": len(cases),
        "category_case_counts": dict(counts),
        "actual_backend_services_used": True,
        "accepted_storage_read_only": True,
        "result_private_and_uncommitted": True,
        "absolute_paths_exposed": False,
        "provider_urls_exposed": False,
        "localhost_url": f"http://127.0.0.1:{int(port)}",
        "acceptance_case_manifest_fingerprint": case_fingerprint,
        "bindings": bindings,
        "result_path_relative": "manual-acceptance/manual-acceptance-result.json",
        "manual_acceptance_required": True,
        "manual_acceptance_status": "pending_user",
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "passed": True,
    }
    sv1b.write_json(output / "manual-acceptance-harness-proof.json", proof)
    return proof


def _current_bindings(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    proof = sv1b.read_json(output / "manual-acceptance-harness-proof.json")
    cases = sv1b.read_json(output / "manual-acceptance/case-manifest-private.json")
    expected = proof["bindings"]
    proof_names = (
        "acquisition-closure-and-package-proof.json",
        "localization-baseline-proof.json",
        "primary-source-graph-derivation-proof.json",
        "replay-source-graph-derivation-proof.json",
        "primary-replay-source-graph-comparison-proof.json",
        "primary-search-validation-proof.json",
        "replay-search-validation-proof.json",
        "primary-replay-search-comparison-proof.json",
    )
    proofs = {name: sv1b.read_json(output / name) for name in proof_names}
    current = {
        "git_head": _git_head(),
        "primary_database": _database_binding(primary_database),
        "replay_database": _database_binding(replay_database),
        "media_manifest_fingerprint": sv1b.ACCEPTED_MANIFEST_FINGERPRINT,
        **_proof_bindings(proofs),
        "acceptance_case_manifest_fingerprint": sv1b.sha256_payload(cases),
    }
    current.pop("binding_fingerprint", None)
    current["binding_fingerprint"] = sv1b.sha256_payload(current)
    if current != expected:
        raise ManualAcceptanceHarnessError("manual_acceptance_binding_invalidated")
    return current


def normalize_submission(
    payload: Mapping[str, Any],
    case_ids: set[str],
) -> list[dict[str, str]]:
    submitted = payload.get("results")
    if not isinstance(submitted, list) or len(submitted) != len(case_ids):
        raise ManualAcceptanceHarnessError("results_invalid")
    by_id = {
        str(row.get("case_id")): row
        for row in submitted
        if isinstance(row, Mapping)
    }
    if set(by_id) != case_ids:
        raise ManualAcceptanceHarnessError("case_membership_mismatch")
    normalized = []
    for case_id in sorted(case_ids):
        row = by_id[case_id]
        decision = str(row.get("decision") or "pending").casefold()
        comment = str(row.get("comment") or "")[:2000]
        if decision not in {"pass", "fail", "pending"}:
            raise ManualAcceptanceHarnessError("decision_invalid")
        normalized.append({"case_id": case_id, "decision": decision, "comment": comment})
    return normalized


def create_app(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
):
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    output = output.resolve()
    bindings = _current_bindings(
        output, primary_database=primary_database, replay_database=replay_database
    )
    cases = sv1b.read_json(output / "manual-acceptance/case-manifest-private.json")
    case_ids = {str(row["case_id"]) for row in cases}
    allowed_labels = {str(row["safe_media_label"]) for row in cases}
    storage_root = sv1b.ACCEPTED_STORAGE.resolve()
    engine = sv1b.engine_for(primary_database)
    media_paths: dict[str, Path] = {}
    try:
        with engine.connect() as connection:
            rows = list(connection.execute(text(
                "SELECT hash,path,thumbnail_path FROM blombooru_media WHERE hash IS NOT NULL"
            )).mappings())
    finally:
        engine.dispose()
    for row in rows:
        label = _safe_media_label(str(row["hash"]))
        if label not in allowed_labels:
            continue
        for value in (row.get("thumbnail_path"), row.get("path")):
            if not value:
                continue
            path = Path(str(value)).resolve()
            if storage_root in path.parents and path.is_file():
                media_paths[label] = path
                break
    missing_media = sorted(allowed_labels - set(media_paths))
    if missing_media:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_media_membership_missing:{len(missing_media)}"
        )

    app = FastAPI(title="SCV2-SV1B Manual Acceptance", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    @app.get("/api/cases")
    def api_cases() -> dict[str, Any]:
        return {
            "harness_version": HARNESS_VERSION,
            "bindings": bindings,
            "cases": cases,
        }

    @app.get("/media/{safe_label}")
    def media(safe_label: str):
        path = media_paths.get(safe_label)
        if path is None:
            raise HTTPException(status_code=404, detail="media_not_found")
        return FileResponse(path)

    @app.post("/api/export")
    def export(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            normalized = normalize_submission(payload, case_ids)
        except ManualAcceptanceHarnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = {
            "harness_version": HARNESS_VERSION,
            "bindings": bindings,
            "acceptance_case_manifest_fingerprint": bindings[
                "acceptance_case_manifest_fingerprint"
            ],
            "per_case_result": normalized,
            "user_comments": str(payload.get("overall_comment") or "")[:5000],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "manual_acceptance_status": "submitted_user_review",
        }
        result_path = output / "manual-acceptance/manual-acceptance-result.json"
        sv1b.write_json(result_path, result)
        return {"saved": True, "relative_path": "manual-acceptance/manual-acceptance-result.json"}

    return app


_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCV2-SV1B 手工验收</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#111827;color:#e5e7eb}main{max-width:1180px;margin:auto;padding:24px}
.case{display:grid;grid-template-columns:260px 1fr;gap:18px;background:#1f2937;border:1px solid #374151;border-radius:12px;padding:16px;margin:16px 0}
img{width:260px;height:260px;object-fit:contain;background:#0b1020;border-radius:8px}.meta{white-space:pre-wrap;background:#111827;padding:10px;border-radius:8px;overflow:auto}
textarea{width:100%;min-height:70px;background:#111827;color:#e5e7eb;border:1px solid #4b5563}.controls{display:flex;gap:16px;margin:10px 0}
button{padding:10px 16px}.binding{font-family:monospace;word-break:break-all;font-size:12px}</style></head>
<body><main><h1>SCV2-SV1B 40-case 手工验收</h1><p>逐项选择 Pass / Fail / Pending，可填写 Comment。导出不会修改数据库。</p>
<div id="binding" class="binding"></div><div id="cases"></div><h2>总体备注</h2><textarea id="overall"></textarea><p><button id="export">导出结果</button> <span id="status"></span></p>
<script>
const state={}; const el=(name,text)=>{const n=document.createElement(name);if(text!==undefined)n.textContent=text;return n};
fetch('/api/cases').then(r=>r.json()).then(data=>{document.getElementById('binding').textContent='Binding: '+data.bindings.binding_fingerprint;
const root=document.getElementById('cases');data.cases.forEach(c=>{state[c.case_id]={case_id:c.case_id,decision:'pending',comment:''};const box=el('section');box.className='case';
const img=el('img');img.src='/media/'+encodeURIComponent(c.safe_media_label);img.alt='验收媒体 '+c.case_id;box.appendChild(img);const body=el('div');body.appendChild(el('h2',c.case_id+' · '+c.title));body.appendChild(el('h3','预期行为'));body.appendChild(el('p',c.expected_behavior));body.appendChild(el('h3','实际结果'));const actual=el('div',JSON.stringify(c.actual_result,null,2));actual.className='meta';body.appendChild(actual);body.appendChild(el('h3','证据来源'));const prov=el('div',JSON.stringify(c.provenance,null,2));prov.className='meta';body.appendChild(prov);const controls=el('div');controls.className='controls';['pass','fail','pending'].forEach(v=>{const label=el('label');const radio=el('input');radio.type='radio';radio.name='d_'+c.case_id;radio.value=v;if(v==='pending')radio.checked=true;radio.onchange=()=>state[c.case_id].decision=v;label.append(radio,document.createTextNode(' '+v.toUpperCase()));controls.appendChild(label)});body.appendChild(controls);const comment=el('textarea');comment.placeholder='Comment';comment.oninput=()=>state[c.case_id].comment=comment.value;body.appendChild(comment);box.appendChild(body);root.appendChild(box)})});
document.getElementById('export').onclick=()=>fetch('/api/export',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({results:Object.values(state),overall_comment:document.getElementById('overall').value})}).then(async r=>{const x=await r.json();if(!r.ok)throw Error(x.detail||'export failed');document.getElementById('status').textContent='已保存: '+x.relative_path}).catch(e=>document.getElementById('status').textContent='失败: '+e.message);
</script></main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--primary-db", required=True)
    parser.add_argument("--replay-db", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    if os.getenv("VIOLET_ENV") != "test":
        raise ManualAcceptanceHarnessError("manual_acceptance_requires_violet_env_test")
    if args.build:
        proof = build_harness(
            args.output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
            port=args.port,
        )
        print(json.dumps({
            "status": proof["status"],
            "case_count": proof["case_count"],
            "localhost_url": proof["localhost_url"],
            "private_values_exposed": False,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    import uvicorn

    app = create_app(
        args.output,
        primary_database=args.primary_db,
        replay_database=args.replay_db,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
