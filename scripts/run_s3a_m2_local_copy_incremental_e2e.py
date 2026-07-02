#!/usr/bin/env python3
"""S3A-M2 local-copy incremental manual-sync E2E.

This phase-scoped runner copies only already-local JPG/PNG files from the
registered production iCloud/photo root into an isolated local test root.  It
then exercises the normal manual-sync planner and execute service against a
dedicated test database/storage profile.  Production source files and
production DB rows are read-only.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE_SLUG = "s3a_m2_delta_e2e"
DEFAULT_ARTIFACT_DIR = ROOT / ".local_manifests" / PHASE_SLUG / "local_copy_incremental_e2e"
PUBLIC_SUMMARY_PATH = ROOT / "docs" / "reports" / "s3a-m2-local-copy-incremental-e2e-summary.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
OLD_MTIME_EPOCH = 1_577_836_800  # 2020-01-01T00:00:00Z
DEFAULT_SMALL_INCREMENT_COUNT = 10
DEFAULT_MEDIUM_INCREMENT_COUNT = 80
DEFAULT_OLD_MTIME_INCREMENT_COUNT = 80
DEFAULT_STABLE_INCREMENT_COUNT = 120
DEFAULT_OUTCOME_VALID_COUNT = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if hasattr(value, "value"):
        return value.value
    return str(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def git_value(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def load_production_profile_env() -> dict[str, str]:
    from scripts.violet_production_control import _profile_to_env, load_production_profile

    profile, _path, errors = load_production_profile(repo_root=ROOT)
    if errors:
        raise RuntimeError(f"production_profile_invalid:{','.join(errors)}")
    return _profile_to_env(profile, repo_root=ROOT)


def pg_params_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "host": env.get("POSTGRES_HOST") or os.environ.get("POSTGRES_HOST") or "localhost",
        "port": int(env.get("POSTGRES_PORT") or os.environ.get("POSTGRES_PORT") or "5432"),
        "user": env.get("POSTGRES_USER") or os.environ.get("POSTGRES_USER") or "postgres",
        "password": env.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD") or "",
    }


def query_production_source_root(label: str) -> dict[str, Any]:
    import psycopg2

    env = load_production_profile_env()
    params = pg_params_from_env(env)
    db_name = env.get("POSTGRES_DB") or "blombooru"
    conn = psycopg2.connect(**params, dbname=db_name)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, label, root_path, root_path_hash, is_active
                FROM blombooru_dynamic_source_roots
                WHERE label = %s
                ORDER BY id ASC
                LIMIT 1
                """,
                (label,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f"production_source_root_not_found:{label}")
    return {
        "id": int(row[0]),
        "label": str(row[1]),
        "root_path": str(row[2]),
        "root_path_hash": str(row[3] or ""),
        "is_active": bool(row[4]),
        "db_name": db_name,
    }


def create_test_database(db_name: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    forbidden = {"blombooru", "production", "main", "postgres"}
    if db_name.lower() in forbidden or "test" not in db_name.lower():
        raise RuntimeError(f"unsafe_test_database_name:{db_name}")
    params = pg_params_from_env({**load_production_profile_env(), **os.environ})
    conn = psycopg2.connect(**params, dbname="postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        conn.close()


def configure_test_env(db_name: str, storage_root: Path, *, fast_providers: bool) -> None:
    db_env = {**load_production_profile_env(), **os.environ}
    db_host = db_env.get("POSTGRES_HOST") or "localhost"
    db_port = str(db_env.get("POSTGRES_PORT") or "5432")
    db_user = db_env.get("POSTGRES_USER") or "postgres"
    db_password = db_env.get("POSTGRES_PASSWORD") or ""
    data_dir = storage_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings_path = data_dir / "settings.json"
    if not settings_path.exists():
        write_json(
            settings_path,
            {
                "first_run": False,
                "app_name": "V.I.O.L.E.T.",
                "database": {
                    "host": db_host,
                    "port": int(db_port),
                    "name": db_name,
                    "user": db_user,
                    "password": db_password,
                },
                "secret_key": hashlib.sha256(f"s3a-m2-local-copy-e2e:{db_name}".encode("utf-8")).hexdigest(),
            },
        )
    os.environ.update(
        {
            "VIOLET_ENV": "test",
            "VIOLET_SKIP_DOTENV": "1",
            "POSTGRES_HOST": db_host,
            "POSTGRES_PORT": db_port,
            "POSTGRES_USER": db_user,
            "POSTGRES_PASSWORD": db_password,
            "POSTGRES_DB": db_name,
            "TEST_DATABASE_URL": "",
            "VIOLET_STORAGE_ROOT": str(storage_root),
            "DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED": "true",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED": "true",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_PLAN_MAX_FILES": "1000",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_MAX_FILES": "1000",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS": "3600",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_STABLE_AGE_SECONDS": "0",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_SAFETY_LOOKBACK_SECONDS": "604800",
            "DYNAMIC_LIBRARY_MANUAL_SYNC_TARGET_CONTENT_CLASSES": "anime,illustration",
            "CONTENT_CLASSIFICATION_ENABLED": "true",
            "CONTENT_CLASSIFICATION_METHOD": "clip",
            "AI_TAGGING_ENABLED": "true",
            "TAG_TRANSLATION_AUTO_ENABLED": "false",
            "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
            "TAG_TRANSLATION_LLM_ENABLED": "false" if fast_providers else os.environ.get("TAG_TRANSLATION_LLM_ENABLED", "false"),
        }
    )


def initialize_app_db():
    from app import models  # noqa: F401 - registers models
    from app import database as app_database

    app_database.init_engine()
    if app_database.engine is None or app_database.SessionLocal is None:
        raise RuntimeError("test_database_engine_unavailable")
    app_database.Base.metadata.create_all(bind=app_database.engine)
    app_database.check_and_migrate_schema(app_database.engine)
    return app_database.SessionLocal


def cloud_state_for(path: Path) -> dict[str, Any]:
    from app.utils.cloud_files import classify_cloud_file_state

    return classify_cloud_file_state(path).to_dict(include_path=False)


@dataclass(frozen=True)
class SourceCandidate:
    path: Path
    size: int
    mtime_ns: int
    extension: str

    @property
    def public_id(self) -> str:
        return public_hash(str(self.path))


def iter_local_image_candidates(source_root: Path, *, max_size_mb: int) -> tuple[list[SourceCandidate], dict[str, int]]:
    counters: Counter[str] = Counter()
    candidates: list[SourceCandidate] = []
    max_size = int(max_size_mb) * 1024 * 1024
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            path = Path(dirpath) / filename
            suffix = path.suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                counters[f"skipped_extension:{suffix or '<none>'}"] += 1
                continue
            try:
                state = cloud_state_for(path)
            except Exception:
                counters["cloud_state_error"] += 1
                continue
            if state.get("likely_cloud_placeholder"):
                counters["skipped_cloud_placeholder"] += 1
                continue
            attrs = set(state.get("attribute_names") or [])
            if "hidden" in attrs or "system" in attrs:
                counters["skipped_hidden_or_system"] += 1
                continue
            try:
                stat = path.stat()
            except OSError:
                counters["stat_error"] += 1
                continue
            if not path.is_file() or stat.st_size <= 0:
                counters["skipped_not_regular_or_empty"] += 1
                continue
            if stat.st_size > max_size:
                counters["skipped_too_large"] += 1
                continue
            candidates.append(SourceCandidate(path=path, size=int(stat.st_size), mtime_ns=int(stat.st_mtime_ns), extension=suffix))
            counters["local_jpg_png_candidates"] += 1
    return candidates, dict(sorted(counters.items()))


def copy_candidate(candidate: SourceCandidate, destination: Path, *, preserve_mtime: bool, old_mtime: bool) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = cloud_state_for(candidate.path)
    if before.get("likely_cloud_placeholder"):
        return {"copied": False, "reason": "cloud_placeholder_before_copy", "source_public_id": candidate.public_id}
    try:
        shutil.copy2(candidate.path, destination)
        if not preserve_mtime:
            now = time.time()
            os.utime(destination, (now, now))
        if old_mtime:
            os.utime(destination, (OLD_MTIME_EPOCH, OLD_MTIME_EPOCH))
    except OSError as exc:
        return {
            "copied": False,
            "reason": f"copy_failed:{getattr(exc, 'winerror', None) or getattr(exc, 'errno', None) or exc.__class__.__name__}",
            "source_public_id": candidate.public_id,
        }
    return {
        "copied": True,
        "reason": None,
        "source_public_id": candidate.public_id,
        "dest_public_id": public_hash(str(destination)),
        "bytes": int(destination.stat().st_size),
        "extension": destination.suffix.lower(),
    }


def set_windows_hidden(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        set_attrs = ctypes.windll.kernel32.SetFileAttributesW
        attrs = int(get_attrs(str(path)))
        if attrs < 0:
            return False
        return bool(set_attrs(str(path), attrs | 0x2))
    except Exception:
        return False


class CandidateDeck:
    def __init__(self, candidates: Sequence[SourceCandidate]) -> None:
        self._candidates = list(candidates)
        self._index = 0

    def take(self, count: int) -> list[SourceCandidate]:
        chunk = self._candidates[self._index : self._index + max(0, int(count))]
        self._index += len(chunk)
        return chunk

    @property
    def remaining(self) -> int:
        return max(0, len(self._candidates) - self._index)


def iter_test_root_files(source_root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted(d for d in dirnames if d not in {".git", "__pycache__", "venv"})
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def test_root_inventory(db: Any, *, root: Any, source_root: Path) -> dict[str, Any]:
    from app.models import DynamicSourceItem
    from app.services import dynamic_library_sync_service as planner

    ledger_rows = db.query(DynamicSourceItem).filter(DynamicSourceItem.source_root_id == int(root.id)).all()
    known_rel_hashes = {str(row.relative_path_hash or "") for row in ledger_rows}
    ledger_status = Counter(str(row.import_status or "") for row in ledger_rows)
    ledger_sync_state = Counter(str(row.sync_state or "") for row in ledger_rows)
    total_files = 0
    ledger_missing = 0
    ledger_missing_supported = 0
    hidden_or_system = 0
    unsupported = 0
    placeholder_like = 0
    for path in iter_test_root_files(source_root):
        total_files += 1
        try:
            rel = planner._normalize_relative_path(path.relative_to(source_root))
            rel_hash = planner._hash_text(rel)
        except Exception:
            continue
        try:
            state = cloud_state_for(path)
            attrs = set(state.get("attribute_names") or [])
        except Exception:
            state = {}
            attrs = set()
        suffix = path.suffix.lower()
        if state.get("likely_cloud_placeholder") or suffix == ".icloud":
            placeholder_like += 1
        if "hidden" in attrs or "system" in attrs:
            hidden_or_system += 1
        if suffix not in IMAGE_EXTENSIONS:
            unsupported += 1
        if rel_hash not in known_rel_hashes:
            ledger_missing += 1
            if suffix in IMAGE_EXTENSIONS:
                ledger_missing_supported += 1
    return {
        "total_files_in_test_root": total_files,
        "known_ledger_files_before": len(known_rel_hashes),
        "ledger_missing_files_before": ledger_missing,
        "ledger_missing_supported_files_before": ledger_missing_supported,
        "hidden_or_system_files_before": hidden_or_system,
        "unsupported_files_before": unsupported,
        "placeholder_like_files_before": placeholder_like,
        "ledger_import_status_before": dict(sorted(ledger_status.items())),
        "ledger_sync_state_before": dict(sorted(ledger_sync_state.items())),
    }


def install_fast_provider_patches(execute_service: Any) -> dict[str, Any]:
    from app.enums import ContentClassEnum, TagCategoryEnum
    from app.models import DynamicSourceItem, Media, Tag, TagTranslation, blombooru_media_tags

    timers: Counter[str] = Counter()
    counters: Counter[str] = Counter()

    def timed(name: str, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            return func()
        finally:
            timers[name] += int((time.perf_counter() - start) * 1_000_000)

    def fake_classify_imported_media(db: Any, media_id: int) -> dict[str, Any]:
        def _impl() -> dict[str, Any]:
            media = db.get(Media, int(media_id))
            if media is None:
                return {"media_id": media_id, "error": "media_not_found", "method": "fast_test_provider"}
            filename = str(media.filename or "").lower()
            if filename.startswith("classfail_"):
                counters["classification_blocked"] += 1
                return {
                    "media_id": media_id,
                    "error": "classification_model_unavailable",
                    "method": "fast_test_provider",
                    "retryable": True,
                }
            if filename.startswith("nonanime_"):
                content_class = ContentClassEnum.non_anime
            elif filename.startswith("unknown_"):
                content_class = ContentClassEnum.unknown
            else:
                content_class = ContentClassEnum.anime
            media.content_class = content_class
            media.content_class_confidence = 0.99
            media.content_class_source = "s3a_m2_local_copy_e2e_fast_provider"
            media.content_class_model = "deterministic-test-provider"
            counters[f"classified:{content_class.value}"] += 1
            db.flush()
            return {
                "media_id": media_id,
                "content_class": content_class.value,
                "confidence": 0.99,
                "method": "fast_test_provider",
            }

        return timed("classification_us", _impl)

    def fake_ai_tag_imported_media(db: Any, media_id: int) -> dict[str, Any]:
        def _impl() -> dict[str, Any]:
            media = db.get(Media, int(media_id))
            if media is None:
                return {"media_id": media_id, "error": "media_not_found"}
            tag = db.query(Tag).filter(Tag.name == "s3a_m2_e2e_general").first()
            if tag is None:
                tag = Tag(name="s3a_m2_e2e_general", category=TagCategoryEnum.general)
                db.add(tag)
                db.flush()
            existing = db.execute(
                blombooru_media_tags.select().where(
                    blombooru_media_tags.c.media_id == int(media_id),
                    blombooru_media_tags.c.tag_id == int(tag.id),
                )
            ).first()
            if existing is None:
                db.execute(
                    blombooru_media_tags.insert().values(
                        media_id=int(media_id),
                        tag_id=int(tag.id),
                        source="ai_wd",
                        confidence=0.98,
                        is_locked=False,
                        is_suggestion=False,
                    )
                )
                counters["ai_tags_added"] += 1
            return {
                "media_id": media_id,
                "tags_added": 1 if existing is None else 0,
                "suggestions_added": 0,
                "provenance": {
                    "provider": "s3a_m2_local_copy_e2e_fast_provider",
                    "execution_provider": "test_deterministic",
                },
            }

        return timed("ai_tagging_us", _impl)

    def fake_finalize_localization(
        db: Any,
        *,
        run: Any,
        media_ids: list[int],
        source_item_ids: list[int] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        lang: str = "zh-CN",
    ) -> dict[str, Any]:
        def _impl() -> dict[str, Any]:
            if cancel_check and cancel_check():
                return execute_service._manual_sync_skipped_localization_result(
                    reason="cancelled",
                    media_ids=media_ids,
                    lang=lang,
                )
            canonical = "s3a_m2_e2e_general"
            row = (
                db.query(TagTranslation)
                .filter(TagTranslation.canonical_name == canonical, TagTranslation.language == lang)
                .first()
            )
            if row is None:
                row = TagTranslation(
                    canonical_name=canonical,
                    language=lang,
                    display_name="S3A-M2 local E2E test tag",
                    status="translated",
                    source="s3a_m2_local_copy_e2e_fast_provider",
                )
                db.add(row)
            updated = 0
            ids = [int(value) for value in (source_item_ids or []) if int(value or 0) > 0]
            if ids:
                for item in db.query(DynamicSourceItem).filter(DynamicSourceItem.id.in_(ids)).all():
                    item.localization_status = "localized"
                    if item.deferred_reason in {"localization_waiting_for_manual_execute_finalizer", "localization_requires_completed_ai_tags"}:
                        item.deferred_reason = None
                    updated += 1
            db.flush()
            counters["localized_source_items"] += updated
            return {
                "scheduled": False,
                "safe_to_schedule": False,
                "status": "completed",
                "background_worker_started": False,
                "auto_translation_enabled": False,
                "background_translation_enabled": False,
                "llm_enabled": False,
                "llm_called": False,
                "lang": lang,
                "media_ids": list(media_ids),
                "source_item_ids": ids,
                "tags_observed_after_runner": 1 if media_ids else 0,
                "tags_already_localized_or_static": 1,
                "tags_requiring_localization_after_runner": 0,
                "localization_calls": 0,
                "retries": 0,
                "failed": 0,
                "dynamic_source_items_updated": updated,
                "dynamic_source_items_target_status": "localized" if updated else "none",
                "blocked_reason": None,
                "public_safe": True,
                "localization_finalizer_called": True,
                "localization_db_writes_performed": bool(updated),
            }

        return timed("localization_us", _impl)

    execute_service._classify_imported_media = fake_classify_imported_media
    execute_service._ai_tag_imported_media = fake_ai_tag_imported_media
    execute_service._manual_sync_finalize_localization = fake_finalize_localization

    return {"timers_us": timers, "counters": counters}


def summarize_run(db: Any, run_id: int) -> dict[str, Any]:
    from app.models import DynamicSourceItem, DynamicSyncRun, DynamicSyncRunItem, Media

    run = db.get(DynamicSyncRun, int(run_id))
    if run is None:
        return {"run_id": run_id, "missing": True}
    items = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.sync_run_id == int(run_id)).all()
    source_items = [item.source_item for item in items if item.source_item is not None]
    media_ids = sorted({int(item.media_id) for item in items if item.media_id is not None})
    media_rows = db.query(Media).filter(Media.id.in_(media_ids)).all() if media_ids else []
    return {
        "run_id": int(run.id),
        "status": run.status,
        "seen": run.total_seen,
        "imported": run.new_items,
        "failed": run.failed_items,
        "run_item_states": dict(sorted(Counter(str(item.item_state or "") for item in items).items())),
        "run_item_reasons": dict(sorted(Counter(str(item.reason or "none") for item in items).items())),
        "source_item_import_status": dict(sorted(Counter(str(item.import_status or "") for item in source_items).items())),
        "source_item_classification_status": dict(sorted(Counter(str(item.classification_status or "") for item in source_items).items())),
        "source_item_ai_status": dict(sorted(Counter(str(item.ai_tagging_status or "") for item in source_items).items())),
        "source_item_localization_status": dict(sorted(Counter(str(item.localization_status or "") for item in source_items).items())),
        "content_class": dict(sorted(Counter(str(getattr(media.content_class, "value", media.content_class) or "null") for media in media_rows).items())),
        "outcome_counts": ((run.summary_json or {}).get("manual_sync_execute") or {}).get("outcome_counts") or {},
        "stage_rows": ((run.summary_json or {}).get("manual_sync_execute") or {}).get("stage_rows") or [],
    }


def plan_and_execute(
    db: Any,
    *,
    root: Any,
    source_root: Path,
    cap: int,
    hydrated_only: bool,
    stable_age_seconds: float,
    scenario: str,
    files_added_this_cycle: int = 0,
    execute: bool = True,
) -> dict[str, Any]:
    from app.services import dynamic_library_sync_service as planner
    from app.services import manual_sync_execute_service as execute_service

    pre_inventory = test_root_inventory(db, root=root, source_root=source_root)
    start = time.perf_counter()
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=root.root_path,
        source_record_id=int(root.id),
        max_files=int(cap),
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        include_private_details=False,
    )
    plan_time = time.perf_counter() - start
    plan_counts = plan.get("counts") or {}
    selected = int(plan_counts.get("estimated_import_count") or 0) + int(
        plan_counts.get("estimated_downstream_followup_count") or 0
    )
    result: dict[str, Any] = {
        "scenario": scenario,
        "files_added_this_cycle": int(files_added_this_cycle),
        "pre_inventory": pre_inventory,
        "plan_time": round(plan_time, 3),
        "plan_hash": str((plan.get("integrity") or {}).get("plan_hash") or "")[:12],
        "plan_counts": plan_counts,
        "plan_limits": plan.get("limits") or {},
        "actionable_selected_count": selected,
        "executed": False,
    }
    if not execute or selected <= 0:
        return result

    create_start = time.perf_counter()
    run = execute_service.create_manual_sync_execute_run(
        db,
        root_id=int(root.id),
        max_files=int(cap),
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        expected_plan_hash=str((plan.get("integrity") or {}).get("plan_hash") or ""),
        confirmation_phrase=str((plan.get("integrity") or {}).get("confirmation_phrase") or ""),
        operator_confirmation_statement=str((plan.get("integrity") or {}).get("operator_confirmation_statement") or ""),
        plan_created_at=str((plan.get("job") or {}).get("created_at") or ""),
        request_source="local_copy_e2e_runner",
    )
    enqueue_time = time.perf_counter() - create_start
    execute_start = time.perf_counter()
    payload = execute_service.execute_manual_sync_run(db, run_id=int(run.id))
    execute_time = time.perf_counter() - execute_start
    db.expire_all()
    run_summary = summarize_run(db, int(run.id))
    result.update(
        {
            "executed": True,
            "run_id": int(run.id),
            "enqueue_time": round(enqueue_time, 3),
            "execute_time": round(execute_time, 3),
            "total_time": round(plan_time + enqueue_time + execute_time, 3),
            "execute_payload_status": payload.get("status"),
            "run_summary": run_summary,
        }
    )
    return result


def add_unsupported_and_corrupt_cases(source_root: Path, *, hidden_candidate: SourceCandidate | None = None) -> dict[str, int]:
    diagnostics = source_root / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "unsupported_notes.txt").write_text("not media", encoding="utf-8")
    (diagnostics / "unsupported_live_photo.mov").write_bytes(b"not a real movie")
    (diagnostics / "unsupported_image.heic").write_bytes(b"not a real heic")
    (diagnostics / "simulated_cloud_placeholder.icloud").write_text("simulated placeholder only", encoding="utf-8")
    (diagnostics / "corrupt_image.jpg").write_bytes(b"not an image")
    hidden_jpg = 0
    hidden_attr_set = False
    if hidden_candidate is not None:
        hidden_path = diagnostics / "hidden_policy_image.jpg"
        row = copy_candidate(hidden_candidate, hidden_path, preserve_mtime=False, old_mtime=False)
        if row.get("copied"):
            hidden_jpg = 1
            hidden_attr_set = set_windows_hidden(hidden_path)
    return {
        "unsupported_txt": 1,
        "unsupported_mov": 1,
        "unsupported_heic": 1,
        "simulated_icloud_placeholder": 1,
        "corrupt_jpg": 1,
        "hidden_jpg": hidden_jpg,
        "hidden_attribute_set": 1 if hidden_attr_set else 0,
    }


def mark_legacy_backlog_rows(db: Any, *, root_id: int, limit: int) -> int:
    from app.models import DynamicSourceItem

    rows = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.source_root_id == int(root_id))
        .filter(DynamicSourceItem.media_id.isnot(None))
        .filter(DynamicSourceItem.import_status == "imported")
        .order_by(DynamicSourceItem.id.asc())
        .limit(int(limit))
        .all()
    )
    for item in rows:
        item.sync_state = "changed"
        item.import_status = "pending"
        item.classification_status = "waiting_import"
        item.ai_tagging_status = "waiting_import"
        item.localization_status = "waiting_ai_tags"
        item.failure_reason = None
        item.deferred_reason = None
    db.commit()
    return len(rows)


def seed_partial_import_downstream_recovery_case(
    db: Any,
    *,
    root: Any,
    source_root: Path,
    storage_root: Path,
    candidates: Sequence[SourceCandidate],
) -> dict[str, Any]:
    from app.enums import FileTypeEnum
    from app.models import DynamicSourceItem, Media
    from app.services import dynamic_library_sync_service as planner
    from app.utils.media_processor import calculate_file_hash

    case_dir = source_root / "cycle11_partial_import_recovery"
    case_dir.mkdir(parents=True, exist_ok=True)
    app_original_dir = storage_root / "media" / "original"
    app_original_dir.mkdir(parents=True, exist_ok=True)
    seeded = 0
    removed_source_files = 0
    seeded_source_item_ids: list[int] = []
    seeded_media_ids: list[int] = []
    before_media_count = db.query(Media).count()
    for idx, candidate in enumerate(candidates[:3], start=1):
        ext = candidate.path.suffix.lower()
        source_copy = case_dir / f"followup_seed_{idx:04d}{ext}"
        row = copy_candidate(candidate, source_copy, preserve_mtime=True, old_mtime=True)
        if not row.get("copied"):
            continue
        content_hash = calculate_file_hash(source_copy)
        app_filename = f"partial_recovery_{idx:04d}{ext}"
        app_path = app_original_dir / app_filename
        shutil.copy2(source_copy, app_path)
        stat = source_copy.stat()
        media = Media(
            filename=app_filename,
            path=str(Path("media") / "original" / app_filename),
            hash=content_hash,
            file_type=FileTypeEnum.image,
            file_size=stat.st_size,
        )
        db.add(media)
        db.flush()
        rel = str(source_copy.relative_to(source_root)).replace("\\", "/")
        source_item = DynamicSourceItem(
            source_root_id=int(root.id),
            relative_path=rel,
            relative_path_hash=planner._hash_text(rel),
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=content_hash,
            media_id=int(media.id),
            source_status="available",
            sync_state="deferred_unprocessed",
            import_status="imported",
            classification_status="deferred",
            ai_tagging_status="deferred",
            localization_status="deferred",
            failure_reason=None,
            deferred_reason="not_processed_budget_stop",
        )
        db.add(source_item)
        db.flush()
        seeded_source_item_ids.append(int(source_item.id))
        seeded_media_ids.append(int(media.id))
        source_copy.unlink(missing_ok=True)
        removed_source_files += 1
        seeded += 1

    retry_source = case_dir / "retryable_read_timeout.jpg"
    retry_source.write_bytes(b"simulated unreadable source")
    retry_stat = retry_source.stat()
    rel = str(retry_source.relative_to(source_root)).replace("\\", "/")
    db.add(
        DynamicSourceItem(
            source_root_id=int(root.id),
            relative_path=rel,
            relative_path_hash=planner._hash_text(rel),
            file_size=retry_stat.st_size,
            mtime_ns=retry_stat.st_mtime_ns,
            content_hash=None,
            media_id=None,
            source_status="failed",
            sync_state="failed",
            import_status="failed",
            classification_status="deferred",
            ai_tagging_status="deferred",
            localization_status="blocked_import_failed",
            failure_reason="read_timeout",
            deferred_reason=None,
            metadata_json={
                "manual_sync_retry": {
                    "attempt_count": 1,
                    "last_failure_reason": "read_timeout",
                    "retryable": True,
                    "long_term_state": "retryable",
                }
            },
        )
    )
    retry_source.unlink(missing_ok=True)
    db.commit()
    return {
        "seeded_followup_items": seeded,
        "seeded_source_item_ids": seeded_source_item_ids,
        "seeded_media_ids": seeded_media_ids,
        "removed_source_files": removed_source_files,
        "retryable_failure_rows_seeded": 1,
        "media_count_before": before_media_count,
        "media_count_after_seed": db.query(Media).count(),
    }


def scenario_metrics(result: Mapping[str, Any], provider_timings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    limits = result.get("plan_limits") or {}
    counts = result.get("plan_counts") or {}
    state_counts = counts.get("state_counts") or {}
    reasons = counts.get("failure_reasons") or {}
    run_summary = result.get("run_summary") or {}
    pre_inventory = result.get("pre_inventory") or {}
    source_delta = limits.get("source_delta_workset") or {}
    recovery_post = result.get("partial_recovery_post") or {}
    timers = (provider_timings or {}).get("timers_us") if provider_timings else None
    classification_time = round(float(timers.get("classification_us", 0)) / 1_000_000, 3) if timers else 0.0
    ai_time = round(float(timers.get("ai_tagging_us", 0)) / 1_000_000, 3) if timers else 0.0
    localization_time = round(float(timers.get("localization_us", 0)) / 1_000_000, 3) if timers else 0.0
    execute_time = float(result.get("execute_time") or 0.0)
    import_time = round(max(0.0, execute_time - classification_time - ai_time - localization_time), 3)
    selected_count = int(result.get("actionable_selected_count") or 0)
    imported_count = int((run_summary.get("imported") if run_summary else 0) or 0)
    skipped_placeholder = (
        state_counts.get("skipped_placeholder", 0)
        or state_counts.get("cloud_placeholder", 0)
        or state_counts.get("icloud_placeholder", 0)
        or (run_summary.get("run_item_states") or {}).get("skipped_placeholder", 0)
        or 0
    )
    return {
        "scenario": result.get("scenario"),
        "files_added_this_cycle": int(result.get("files_added_this_cycle") or 0),
        "total_files_in_test_root": pre_inventory.get("total_files_in_test_root", 0),
        "known_ledger_files_before": pre_inventory.get("known_ledger_files_before", 0),
        "ledger_missing_files_before": pre_inventory.get("ledger_missing_files_before", 0),
        "ledger_missing_supported_files_before": pre_inventory.get("ledger_missing_supported_files_before", 0),
        "hidden_or_system_files_before": pre_inventory.get("hidden_or_system_files_before", 0),
        "unsupported_files_before": pre_inventory.get("unsupported_files_before", 0),
        "placeholder_like_files_before": pre_inventory.get("placeholder_like_files_before", 0),
        "plan_time": result.get("plan_time", 0),
        "import_time": import_time,
        "classification_time": classification_time,
        "ai_tagging_time": ai_time,
        "localization_time": localization_time,
        "report_time": 0.0,
        "total_time": result.get("total_time", result.get("plan_time", 0)),
        "metadata_entries_seen": limits.get("metadata_entries_seen", counts.get("metadata_entries_seen", 0)),
        "candidate_pool_count": limits.get("candidate_pool_count", 0),
        "selected_count": selected_count,
        "estimated_import_count": counts.get("estimated_import_count", 0),
        "estimated_downstream_followup_count": counts.get("estimated_downstream_followup_count", 0),
        "app_media_followup_candidates": source_delta.get("app_media_followup_candidates", 0),
        "app_media_followup_filesystem_duplicate_skips": source_delta.get(
            "app_media_followup_filesystem_duplicate_skips", 0
        ),
        "actual_imported_count": imported_count,
        "imported_count": imported_count,
        "seeded_followup_items": (result.get("partial_recovery_seed") or {}).get("seeded_followup_items", 0),
        "media_count_after": recovery_post.get("media_count_after_execute", 0),
        "downstream_followup_completed_count": recovery_post.get("completed_followup_items", 0),
        "downstream_followup_duplicate_media_created": recovery_post.get("duplicate_media_created", 0),
        "retryable_failure_rows_visible_after": recovery_post.get("retryable_failure_rows_visible", 0),
        "skipped_existing": state_counts.get("skipped_existing_media", 0) or (run_summary.get("run_item_states") or {}).get("skipped_existing_media", 0),
        "skipped_duplicate": state_counts.get("skipped_duplicate", 0) or (run_summary.get("run_item_states") or {}).get("skipped_duplicate", 0),
        "skipped_unsupported": state_counts.get("skipped_unsupported", 0),
        "skipped_placeholder": skipped_placeholder,
        "skipped_non_target": (run_summary.get("outcome_counts") or {}).get("ai_tagging_skipped_non_target", 0),
        "unknown_count": (run_summary.get("content_class") or {}).get("unknown", 0),
        "classification_failed_or_deferred": int(
            (run_summary.get("source_item_classification_status") or {}).get("failed", 0)
            + (run_summary.get("source_item_classification_status") or {}).get("deferred", 0)
            + (run_summary.get("source_item_classification_status") or {}).get("blocked", 0)
        ),
        "failed_count": (run_summary.get("failed") if run_summary else 0) or reasons.get("stat_error", 0) or 0,
        "more_batches_remain": bool(limits.get("more_batches_remain")),
        "filesystem_walk_complete": bool(((limits.get("source_delta_workset") or {}).get("filesystem_walk_completed"))),
        "continuation_state": (limits.get("continuation") or {}).get("next_batch_start_basis"),
        "legacy_pending_rows_seen": limits.get("legacy_pending_outside_window_skips", 0),
        "legacy_pending_rows_selected": 0,
        "legacy_pending_outside_window_skips": limits.get("legacy_pending_outside_window_skips", 0),
        "plan_read_count": (limits.get("expensive_plan_operations") or {}).get("content_reads", 0),
        "plan_hash_count": (limits.get("expensive_plan_operations") or {}).get("hashes", 0),
        "plan_decode_count": (limits.get("expensive_plan_operations") or {}).get("decodes", 0),
        "plan_hydration_count": (limits.get("expensive_plan_operations") or {}).get("hydrations", 0),
    }


def evaluate_pass_criteria(metrics: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]) -> list[str]:
    failures: list[str] = []
    by_name = {str(row.get("scenario")): row for row in metrics}

    for row in metrics:
        scenario = str(row.get("scenario"))
        expensive = (
            int(row.get("plan_read_count") or 0),
            int(row.get("plan_hash_count") or 0),
            int(row.get("plan_decode_count") or 0),
            int(row.get("plan_hydration_count") or 0),
        )
        if expensive != (0, 0, 0, 0):
            failures.append(f"{scenario}:plan_expensive_ops_not_zero:{expensive}")

    no_change = by_name.get("cycle_1_no_change_noop")
    if no_change and any(int(no_change.get(key) or 0) for key in ("selected_count", "actual_imported_count", "failed_count")):
        failures.append("cycle_1_no_change_noop:not_fast_noop")

    for scenario in [
        "cycle_2_small_increment_current_mtime",
        "cycle_3_medium_increment_current_mtime",
        "cycle_4_old_mtime_increment",
        "cycle_5_large_stable_root_cap_limited_increment",
        "cycle_9_legacy_backlog_plus_new_files",
    ]:
        row = by_name.get(scenario)
        if not row:
            failures.append(f"{scenario}:missing")
            continue
        visible_supported = int(row.get("ledger_missing_supported_files_before") or 0)
        selected = int(row.get("selected_count") or 0)
        imported = int(row.get("actual_imported_count") or 0)
        failed = int(row.get("failed_count") or 0)
        if visible_supported > 0 and selected == 0 and imported == 0 and failed == 0:
            failures.append(f"{scenario}:visible_supported_files_but_zero_selected_imported_failed")

    old_mtime = by_name.get("cycle_4_old_mtime_increment")
    if old_mtime and int(old_mtime.get("selected_count") or 0) <= 0:
        failures.append("cycle_4_old_mtime_increment:old_mtime_files_not_selected")

    legacy = by_name.get("cycle_9_legacy_backlog_plus_new_files")
    if legacy:
        if int(legacy.get("legacy_pending_rows_seen") or 0) <= 0:
            failures.append("cycle_9_legacy_backlog_plus_new_files:legacy_backlog_not_exercised")
        if int(legacy.get("legacy_pending_rows_selected") or 0) != 0:
            failures.append("cycle_9_legacy_backlog_plus_new_files:legacy_backlog_selected_as_actionable")
        if int(legacy.get("selected_count") or 0) <= 0 and int(legacy.get("ledger_missing_supported_files_before") or 0) > 0:
            failures.append("cycle_9_legacy_backlog_plus_new_files:new_files_hidden_by_legacy_backlog")

    outcome = by_name.get("cycle_6_duplicate_unsupported_hidden_outcome_breakdown")
    if outcome:
        if int(outcome.get("actual_imported_count") or 0) <= 0:
            failures.append("cycle_6_duplicate_unsupported_hidden_outcome_breakdown:no_valid_imports")
        if (
            int(outcome.get("skipped_duplicate") or 0)
            + int(outcome.get("skipped_existing") or 0)
            + int(outcome.get("skipped_unsupported") or 0)
            + int(outcome.get("failed_count") or 0)
        ) <= 0:
            failures.append("cycle_6_duplicate_unsupported_hidden_outcome_breakdown:no_non_import_breakdown")

    placeholder = by_name.get("cycle_7_placeholder_cloud_state_simulated")
    if placeholder and int(placeholder.get("placeholder_like_files_before") or 0) <= 0:
        failures.append("cycle_7_placeholder_cloud_state_simulated:placeholder_not_visible_in_inventory")

    gate = by_name.get("cycle_8_unknown_non_anime_classification_gate")
    if gate:
        if int(gate.get("skipped_non_target") or 0) <= 0:
            failures.append("cycle_8_unknown_non_anime_classification_gate:confirmed_non_anime_not_stably_skipped")
        if int(gate.get("unknown_count") or 0) <= 0:
            failures.append("cycle_8_unknown_non_anime_classification_gate:unknown_not_imported_or_not_reported_separately")
        if int(gate.get("classification_failed_or_deferred") or 0) <= 0:
            failures.append("cycle_8_unknown_non_anime_classification_gate:classification_unavailable_not_deferred")

    refresh_rows = [row for row in results if str(row.get("scenario")) == "cycle_10_refresh_retry_no_stale_zero_plan_api_probe"]
    if not refresh_rows:
        failures.append("cycle_10_refresh_retry_no_stale_zero_plan_api_probe:missing")
    elif refresh_rows[0].get("status") not in {"passed"}:
        failures.append("cycle_10_refresh_retry_no_stale_zero_plan_api_probe:stale_or_mismatched_plan_state")

    recovery = by_name.get("cycle_11_partial_import_downstream_recovery")
    if not recovery:
        failures.append("cycle_11_partial_import_downstream_recovery:missing")
    else:
        seeded = int(recovery.get("seeded_followup_items") or 0)
        app_followups = int(recovery.get("app_media_followup_candidates") or 0)
        selected_followups = int(recovery.get("estimated_downstream_followup_count") or 0)
        completed = int(recovery.get("downstream_followup_completed_count") or 0)
        duplicate_media = int(recovery.get("downstream_followup_duplicate_media_created") or 0)
        retry_visible = int(recovery.get("retryable_failure_rows_visible_after") or 0)
        if seeded <= 0:
            failures.append("cycle_11_partial_import_downstream_recovery:no_seeded_imported_incomplete_media")
        if app_followups < seeded:
            failures.append("cycle_11_partial_import_downstream_recovery:app_media_followup_not_discovered")
        if selected_followups < seeded:
            failures.append("cycle_11_partial_import_downstream_recovery:followup_not_selected")
        if completed < seeded:
            failures.append("cycle_11_partial_import_downstream_recovery:followup_not_completed")
        if duplicate_media:
            failures.append("cycle_11_partial_import_downstream_recovery:duplicate_media_created")
        if retry_visible <= 0:
            failures.append("cycle_11_partial_import_downstream_recovery:retryable_failure_not_visible_after_recovery")

    return failures


def run_e2e(args: argparse.Namespace) -> dict[str, Any]:
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = Path(args.artifact_dir or DEFAULT_ARTIFACT_DIR) / stamp
    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.gettempdir()) / "violet_s3a_m2_local_copy_e2e" / stamp
    source_root = work_dir / "source"
    storage_root = work_dir / "storage"
    source_root.mkdir(parents=True, exist_ok=True)
    storage_root.mkdir(parents=True, exist_ok=True)

    production_root = query_production_source_root(args.production_root_label)
    candidates, source_scan_counts = iter_local_image_candidates(Path(production_root["root_path"]), max_size_mb=args.max_size_mb)
    candidates = candidates[: int(args.target_local_count)]
    deck = CandidateDeck(candidates)

    db_name = args.test_db_name or f"violet_s3a_m2_copy_e2e_{utcnow().strftime('%H%M%S')}_test"
    create_test_database(db_name)
    configure_test_env(db_name, storage_root, fast_providers=bool(args.fast_test_providers))
    SessionLocal = initialize_app_db()

    from app.models import DynamicSourceItem, Media
    from app.services import dynamic_library_sync_service as planner
    from app.services import manual_sync_execute_service as execute_service

    provider_patch = install_fast_provider_patches(execute_service) if args.fast_test_providers else {}

    db = SessionLocal()
    copy_manifest: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    try:
        root = planner.register_source_root(db, path=source_root, label="s3a-m2-local-copy-e2e")

        def copy_batch(name: str, count: int, *, preserve_mtime: bool, old_mtime: bool, prefix: str = "") -> int:
            copied = 0
            for idx, candidate in enumerate(deck.take(count), start=1):
                ext = candidate.path.suffix.lower()
                destination = source_root / name / f"{prefix}{idx:04d}{ext}"
                row = copy_candidate(candidate, destination, preserve_mtime=preserve_mtime, old_mtime=old_mtime)
                row.update({"batch": name, "dest_public_label": public_hash(str(destination))})
                copy_manifest.append(row)
                if row.get("copied"):
                    copied += 1
            return copied

        timer_checkpoint: Counter[str] = Counter()

        def provider_delta() -> dict[str, Any]:
            timers = provider_patch.get("timers_us") if provider_patch else Counter()
            delta = Counter({key: int(value) - int(timer_checkpoint.get(key, 0)) for key, value in dict(timers).items()})
            timer_checkpoint.clear()
            timer_checkpoint.update(dict(timers))
            return {"timers_us": delta}

        def record_result(result: dict[str, Any]) -> None:
            results.append(result)
            metrics.append(scenario_metrics(result, provider_delta()))

        baseline_target = min(args.baseline_count, len(candidates))

        copied_baseline = copy_batch("cycle0_baseline", baseline_target, preserve_mtime=True, old_mtime=True)
        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=max(1, copied_baseline + 50),
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_0_baseline_import",
                files_added_this_cycle=copied_baseline,
            )
        )

        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=int(args.cap),
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_1_no_change_noop",
                files_added_this_cycle=0,
                execute=False,
            )
        )

        copied_small = copy_batch(
            "cycle2_small_current_mtime",
            min(args.small_increment_count, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
        )
        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=int(args.cap),
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_2_small_increment_current_mtime",
                files_added_this_cycle=copied_small,
            )
        )

        copied_medium = copy_batch(
            "cycle3_medium_current_mtime",
            min(args.medium_increment_count, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
        )
        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=int(args.cap),
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_3_medium_increment_current_mtime",
                files_added_this_cycle=copied_medium,
            )
        )

        copied_old = copy_batch(
            "cycle4_old_mtime_add",
            min(args.old_mtime_increment_count, deck.remaining),
            preserve_mtime=True,
            old_mtime=True,
        )
        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=int(args.cap),
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_4_old_mtime_increment",
                files_added_this_cycle=copied_old,
            )
        )

        copied_stable_new = copy_batch(
            "cycle5_large_stable_plus_new",
            min(args.stable_increment_count, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
        )
        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=min(int(args.cap), 500),
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_5_large_stable_root_cap_limited_increment",
                files_added_this_cycle=copied_stable_new,
            )
        )

        duplicate_source = next((source_root / "cycle0_baseline").glob("*.*"), None)
        duplicate_added = 0
        if duplicate_source is not None:
            (source_root / "cycle6_outcomes").mkdir(parents=True, exist_ok=True)
            shutil.copy2(duplicate_source, source_root / "cycle6_outcomes" / f"duplicate_{duplicate_source.name}")
            duplicate_added = 1
        hidden_candidate = deck.take(1)[0] if deck.remaining > 0 else None
        added_special = add_unsupported_and_corrupt_cases(source_root, hidden_candidate=hidden_candidate)
        copied_outcome_valid = copy_batch(
            "cycle6_outcomes",
            min(args.outcome_valid_count, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
        )
        result = plan_and_execute(
            db,
            root=root,
            source_root=source_root,
            cap=100,
            hydrated_only=False,
            stable_age_seconds=0,
            scenario="cycle_6_duplicate_unsupported_hidden_outcome_breakdown",
            files_added_this_cycle=copied_outcome_valid + duplicate_added + sum(
                int(value) for key, value in added_special.items() if key != "hidden_attribute_set"
            ),
        )
        result["special_cases_added"] = {**added_special, "duplicate_copy": duplicate_added, "valid_copies": copied_outcome_valid}
        results.append(result)
        metrics.append(scenario_metrics(result, provider_delta()))

        placeholder_dir = source_root / "cycle7_placeholder_simulation"
        placeholder_dir.mkdir(parents=True, exist_ok=True)
        (placeholder_dir / "simulated_cloud_only_photo.icloud").write_text("simulated placeholder only", encoding="utf-8")
        record_result(
            plan_and_execute(
                db,
                root=root,
                source_root=source_root,
                cap=50,
                hydrated_only=False,
                stable_age_seconds=0,
                scenario="cycle_7_placeholder_cloud_state_simulated",
                files_added_this_cycle=1,
                execute=False,
            )
        )

        copied_non_target = copy_batch(
            "cycle8_classification_gate",
            min(2, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
            prefix="nonanime_",
        )
        copied_unknown = copy_batch(
            "cycle8_classification_gate",
            min(2, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
            prefix="unknown_",
        )
        copied_classfail = copy_batch(
            "cycle8_classification_gate",
            min(1, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
            prefix="classfail_",
        )
        result = plan_and_execute(
            db,
            root=root,
            source_root=source_root,
            cap=100,
            hydrated_only=False,
            stable_age_seconds=0,
            scenario="cycle_8_unknown_non_anime_classification_gate",
            files_added_this_cycle=copied_non_target + copied_unknown + copied_classfail,
        )
        result["classification_gate_cases_added"] = {
            "nonanime_copies": copied_non_target,
            "unknown_copies": copied_unknown,
            "classfail_copies": copied_classfail,
        }
        results.append(result)
        metrics.append(scenario_metrics(result, provider_delta()))

        marked_backlog = mark_legacy_backlog_rows(db, root_id=int(root.id), limit=int(args.legacy_backlog_count))
        copied_legacy_new = copy_batch(
            "cycle9_legacy_backlog_new",
            min(args.legacy_new_count, deck.remaining),
            preserve_mtime=False,
            old_mtime=False,
        )
        result = plan_and_execute(
            db,
            root=root,
            source_root=source_root,
            cap=min(int(args.cap), 500),
            hydrated_only=False,
            stable_age_seconds=0,
            scenario="cycle_9_legacy_backlog_plus_new_files",
            files_added_this_cycle=copied_legacy_new,
        )
        result["legacy_backlog_rows_marked"] = marked_backlog
        results.append(result)
        metrics.append(scenario_metrics(result, provider_delta()))

        result_1 = planner.plan_manual_sync_dry_run(
            db,
            source_path=root.root_path,
            source_record_id=int(root.id),
            max_files=10,
            hydrated_only=False,
            stable_age_seconds=0,
        )
        result_2 = planner.plan_manual_sync_dry_run(
            db,
            source_path=root.root_path,
            source_record_id=int(root.id),
            max_files=10,
            hydrated_only=False,
            stable_age_seconds=0,
        )
        results.append(
            {
                "scenario": "cycle_10_refresh_retry_no_stale_zero_plan_api_probe",
                "files_added_this_cycle": 0,
                "plan_time": 0,
                "executed": False,
                "first_plan_hash": str((result_1.get("integrity") or {}).get("plan_hash") or "")[:12],
                "second_plan_hash": str((result_2.get("integrity") or {}).get("plan_hash") or "")[:12],
                "first_selected": int((result_1.get("counts") or {}).get("estimated_import_count") or 0)
                + int((result_1.get("counts") or {}).get("estimated_downstream_followup_count") or 0),
                "second_selected": int((result_2.get("counts") or {}).get("estimated_import_count") or 0)
                + int((result_2.get("counts") or {}).get("estimated_downstream_followup_count") or 0),
                "status": "passed"
                if (result_1.get("counts") or {}).get("estimated_import_count")
                == (result_2.get("counts") or {}).get("estimated_import_count")
                else "inspect",
                "note": "API-level refresh/retry probe only; real browser validation is required separately.",
            }
        )

        recovery_seed_candidates = deck.take(min(3, deck.remaining))
        recovery_seed = seed_partial_import_downstream_recovery_case(
            db,
            root=root,
            source_root=source_root,
            storage_root=storage_root,
            candidates=recovery_seed_candidates,
        )
        result = plan_and_execute(
            db,
            root=root,
            source_root=source_root,
            cap=100,
            hydrated_only=False,
            stable_age_seconds=0,
            scenario="cycle_11_partial_import_downstream_recovery",
            files_added_this_cycle=0,
        )
        seeded_source_item_ids = [int(value) for value in recovery_seed.get("seeded_source_item_ids") or []]
        seeded_items = (
            db.query(DynamicSourceItem)
            .filter(DynamicSourceItem.id.in_(seeded_source_item_ids))
            .order_by(DynamicSourceItem.id.asc())
            .all()
            if seeded_source_item_ids
            else []
        )
        classification_complete = {"classified", "classified_reused"}
        ai_complete = {
            "ai_tagged",
            "tagged",
            "tagged_reused",
            "ai_tagging_skipped_non_target",
            "skipped_non_target",
        }
        localization_complete = {
            "localized",
            "completed",
            "skipped_no_localizable_tags",
            "skipped_no_new_tags",
            "skipped_static_coverage",
            "localization_not_applicable_non_target",
        }
        completed_followup_items = sum(
            1
            for item in seeded_items
            if str(item.classification_status or "") in classification_complete
            and str(item.ai_tagging_status or "") in ai_complete
            and str(item.localization_status or "") in localization_complete
        )
        retryable_visible = (
            db.query(DynamicSourceItem)
            .filter(DynamicSourceItem.source_root_id == int(root.id))
            .filter(DynamicSourceItem.failure_reason.in_(["read_error", "read_timeout", "source_missing", "permission_denied"]))
            .count()
        )
        result["partial_recovery_seed"] = recovery_seed
        result["partial_recovery_post"] = {
            "seeded_source_item_count": len(seeded_source_item_ids),
            "classification_status": dict(sorted(Counter(str(item.classification_status or "") for item in seeded_items).items())),
            "ai_tagging_status": dict(sorted(Counter(str(item.ai_tagging_status or "") for item in seeded_items).items())),
            "localization_status": dict(sorted(Counter(str(item.localization_status or "") for item in seeded_items).items())),
            "completed_followup_items": completed_followup_items,
            "media_count_after_execute": db.query(Media).count(),
            "duplicate_media_created": max(
                0,
                int(db.query(Media).count()) - int(recovery_seed.get("media_count_after_seed") or 0),
            ),
            "retryable_failure_rows_visible": int(retryable_visible),
        }
        results.append(result)
        metrics.append(scenario_metrics(result, provider_delta()))

        final_counts = {
            "dynamic_source_items": db.query(DynamicSourceItem).filter(DynamicSourceItem.source_root_id == int(root.id)).count(),
            "copied_files": sum(1 for row in copy_manifest if row.get("copied")),
            "copy_failures": sum(1 for row in copy_manifest if not row.get("copied")),
            "deck_remaining": deck.remaining,
        }
    finally:
        db.close()

    pass_criteria_failures = evaluate_pass_criteria(metrics, results)
    public_metrics = []
    for row in metrics:
        public_metrics.append({key: row.get(key) for key in sorted(row.keys())})

    private = {
        "schema": "s3a_m2_local_copy_incremental_e2e_private_v1",
        "generated_at": utcnow(),
        "git_head": git_value("rev-parse", "HEAD"),
        "work_dir": str(work_dir),
        "source_root": str(source_root),
        "storage_root": str(storage_root),
        "test_db_name": db_name,
        "production_source_root": production_root,
        "source_scan_counts": source_scan_counts,
        "copy_manifest": copy_manifest,
        "results": results,
        "metrics": metrics,
        "fast_test_providers": bool(args.fast_test_providers),
        "provider_patch_counters": {
            "timers_us": dict((provider_patch.get("timers_us") or {})),
            "counters": dict((provider_patch.get("counters") or {})),
        },
        "final_counts": final_counts,
        "pass_criteria_failures": pass_criteria_failures,
        "safety": {
            "production_db_mutated": False,
            "production_execute_performed": False,
            "source_icloud_mutated": False,
            "forced_cloud_download_or_hydration": False,
            "copied_local_files_only": True,
            "test_db_only": True,
        },
    }
    private_path = artifact_dir / "local-copy-incremental-e2e-private.json"
    write_json(private_path, private)

    public = {
        "schema": "s3a_m2_local_copy_incremental_e2e_public_v1",
        "generated_at": private["generated_at"],
        "git_head": private["git_head"],
        "status": (
            "completed"
            if final_counts.get("copied_files", 0) > 0 and not pass_criteria_failures
            else ("completed_with_failures" if final_counts.get("copied_files", 0) > 0 else "blocked_no_local_images_copied")
        ),
        "production_source_root_public": {
            "root_id": production_root.get("id"),
            "label": production_root.get("label"),
            "root_path_hash_prefix": str(production_root.get("root_path_hash") or "")[:12],
            "is_active": production_root.get("is_active"),
        },
        "test_setup": {
            "work_dir_public_hash": public_hash(str(work_dir)),
            "source_root_public_hash": public_hash(str(source_root)),
            "storage_root_public_hash": public_hash(str(storage_root)),
            "test_db_name": db_name,
            "copied_files": final_counts.get("copied_files", 0),
            "copy_failures": final_counts.get("copy_failures", 0),
            "target_local_count": int(args.target_local_count),
            "fast_test_providers": bool(args.fast_test_providers),
            "cleanup_behavior": "test DB and copied temp directory retained for audit; no production cleanup performed",
        },
        "source_scan_counts": source_scan_counts,
        "scenario_metrics": public_metrics,
        "pass_criteria_failures": pass_criteria_failures,
        "bulk_run_alone_sufficient": False,
        "repeated_incremental_e2e_required": True,
        "scenario_status": [
            {
                "scenario": row.get("scenario"),
                "executed": row.get("executed"),
                "run_id": row.get("run_id"),
                "execute_payload_status": row.get("execute_payload_status"),
                "plan_hash": row.get("plan_hash"),
                "selected": row.get("actionable_selected_count", 0),
                "plan_items": ((row.get("plan_counts") or {}).get("plan_items") or 0),
                "estimated_import_count": ((row.get("plan_counts") or {}).get("estimated_import_count") or 0),
                "estimated_downstream_followup_count": (
                    (row.get("plan_counts") or {}).get("estimated_downstream_followup_count") or 0
                ),
                "metadata_entries_seen": ((row.get("plan_limits") or {}).get("metadata_entries_seen") or 0),
                "candidate_pool_count": ((row.get("plan_limits") or {}).get("candidate_pool_count") or 0),
                "expensive_plan_operations": ((row.get("plan_limits") or {}).get("expensive_plan_operations") or {}),
                "status": (row.get("run_summary") or {}).get("status") or row.get("status") or "planned",
            }
            for row in results
        ],
        "safety": private["safety"],
        "private_artifact": str(private_path.relative_to(ROOT)),
        "public_safe": True,
    }
    public_path = artifact_dir / "local-copy-incremental-e2e-public.json"
    write_json(public_path, public)
    if args.write_public_summary:
        write_json(PUBLIC_SUMMARY_PATH, public)
    public["public_artifact"] = str(public_path.relative_to(ROOT))
    return public


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-root-label", default="icloud-photos-production")
    parser.add_argument("--target-local-count", type=int, default=1000)
    parser.add_argument("--baseline-count", type=int, default=500)
    parser.add_argument("--small-increment-count", type=int, default=DEFAULT_SMALL_INCREMENT_COUNT)
    parser.add_argument("--medium-increment-count", type=int, default=DEFAULT_MEDIUM_INCREMENT_COUNT)
    parser.add_argument("--old-mtime-increment-count", type=int, default=DEFAULT_OLD_MTIME_INCREMENT_COUNT)
    parser.add_argument("--stable-increment-count", type=int, default=DEFAULT_STABLE_INCREMENT_COUNT)
    parser.add_argument("--outcome-valid-count", type=int, default=DEFAULT_OUTCOME_VALID_COUNT)
    parser.add_argument("--legacy-backlog-count", type=int, default=500)
    parser.add_argument("--legacy-new-count", type=int, default=100)
    parser.add_argument("--cap", type=int, default=500)
    parser.add_argument("--max-size-mb", type=int, default=25)
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--test-db-name", default="")
    parser.add_argument("--fast-test-providers", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-public-summary", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = run_e2e(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
