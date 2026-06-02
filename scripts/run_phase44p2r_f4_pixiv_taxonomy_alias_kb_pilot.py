"""Phase 4.4-P2R-F4 Pixiv taxonomy / alias KB pilot.

This phase-scoped runner builds a bounded, provenance-backed Pixiv raw-tag
taxonomy and alias knowledge base. It may write only the approved taxonomy KB,
alias KB, and external tag category lookup cache tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import migrate_add_pixiv_tag_taxonomy_alias_kb  # noqa: E402
from app.models import (  # noqa: E402
    ExternalTagCategoryLookupCache,
    PixivTagAliasKnowledgeBase,
    PixivTagTaxonomyKnowledgeBase,
)
from scripts import run_phase44p2r_f1_gallery_dl_json_import_pilot as f1  # noqa: E402
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as f2  # noqa: E402
from scripts import run_phase44p2r_f3_pixiv_metadata_normalization_pilot as f3  # noqa: E402

PHASE = "4.4-P2R-F4"
PHASE_SLUG = "phase-4.4p2r-f4-pixiv-taxonomy-alias-knowledge-base"
PRIVATE_ROOT_SLUG = "phase-4.4p2r-f4-pixiv-taxonomy-alias-kb"
TITLE = "Pixiv Taxonomy / Alias Knowledge Base Construction and Classification Closure Pilot"

REPORT_MD = Path(f"docs/reports/{PHASE_SLUG}.md")
REPORT_JSON = Path(f"docs/reports/{PHASE_SLUG}-summary.json")
PHASE_OUTPUT_DIR = Path(f".local_manifests/{PRIVATE_ROOT_SLUG}")
PRIVATE_DETAILS_JSON = PHASE_OUTPUT_DIR / "details.json"
PRIVATE_MEDIA_SUMMARY_CSV = PHASE_OUTPUT_DIR / "media-summary.csv"
PRIVATE_TAG_CLASSIFICATION_CSV = PHASE_OUTPUT_DIR / "tag-classification.csv"
PRIVATE_ENTITY_CANDIDATES_PREVIEW_CSV = PHASE_OUTPUT_DIR / "entity-candidates-preview.csv"
PRIVATE_ALIAS_CANDIDATES_CSV = PHASE_OUTPUT_DIR / "alias-candidates.csv"
PRIVATE_UNRESOLVED_TAGS_CSV = PHASE_OUTPUT_DIR / "unresolved-tags-analysis.csv"
PRIVATE_KB_CACHE_SHEET_CSV = PHASE_OUTPUT_DIR / "kb-cache-sheet.csv"
PRIVATE_CURATED_TEMPLATE_CSV = PHASE_OUTPUT_DIR / "curated-mapping-template.csv"
PRIVATE_MANUAL_REVIEW_GUIDE = PHASE_OUTPUT_DIR / "manual-review-guide.md"
PRIVATE_RAW_DIR = PHASE_OUTPUT_DIR / "raw-gallery-dl-json"

PR90_PRIVATE_DIR = Path(".local_manifests/phase-4.4p2r-f3-pixiv-metadata-normalization-pilot")
PR90_SUMMARY_JSON = Path("docs/reports/phase-4.4p2r-f3-pixiv-metadata-normalization-pilot-summary.json")

DEFAULT_SAMPLE_SIZE = 50
MAX_WORK_IDS_WITHOUT_RENEWED_APPROVAL = 100
MAX_RECORDS_WITHOUT_RENEWED_APPROVAL = 500
MAX_UNIQUE_TAGS_WITHOUT_RENEWED_APPROVAL = 500
DEFAULT_MAX_EXTERNAL_REQUESTS = 600
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 5
DEFAULT_LOOKUP_DELAY_SECONDS = 0.05

GELBOORU_TAGS_SOURCE = "gelbooru_tags_xml_api_v1"
GELBOORU_TAG_LOOKUP_BASE_URL = "https://gelbooru.com/index.php"
LOOKUP_SOURCES = (
    f3.DANBOORU_TAGS_SOURCE,
    f3.DANBOORU_ALIAS_SOURCE,
    f3.SAFEBOORU_TAGS_SOURCE,
    GELBOORU_TAGS_SOURCE,
)

ALLOWED_WRITE_TABLES = {
    "blombooru_external_tag_category_lookup_cache",
    "blombooru_pixiv_tag_taxonomy_kb",
    "blombooru_pixiv_tag_alias_kb",
}
FORBIDDEN_TABLES = {
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_external_identities",
    "blombooru_entity_evidence",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
    "blombooru_media_tags",
    "blombooru_tag_translations",
}
ENTITY_NAMESPACES = {"artist", "character", "copyright"}
RESOLVED_NAMESPACES = {"artist", "character", "copyright", "general", "meta"}
SOURCE_SCOPE = "pixiv_raw_tag_v1"

PREVIOUS_PR90_BASELINE = {
    "unique_tag_count": 133,
    "resolved_unique_tag_count": 22,
    "resolved_unique_tag_coverage": 0.1654,
    "high_value_proper_noun_like_tag_count": 128,
    "high_value_proper_noun_like_resolved_count": 19,
    "high_value_proper_noun_like_resolution_rate": 0.1484,
    "ambiguous_unknown_candidate_count": 262,
    "original_f3_ambiguous_unknown_candidate_count": 305,
}


class Phase44P2RF4Error(RuntimeError):
    pass


class OutputPathError(Phase44P2RF4Error):
    pass


class SampleGateError(Phase44P2RF4Error):
    pass


class CuratedMappingError(Phase44P2RF4Error):
    pass


@dataclass(frozen=True)
class CuratedMapping:
    source_tag: str
    canonical_key: str
    candidate_namespace: str
    confidence: float = 0.95
    status: str = "resolved"
    target_tag: str | None = None
    relation_type: str | None = None
    notes: str | None = None


@dataclass
class TaxonomyKBEntry:
    raw_tag_private: str
    normalized_tag: str
    canonical_key: str
    candidate_namespace: str
    confidence: float
    status: str
    source_summary: dict[str, Any]
    frequency: int
    high_value_score: float
    language_script_hints: dict[str, Any]
    unresolved_reason: str | None = None
    next_action: str | None = None
    manual_override_status: str = "none"
    manual_override_value: str | None = None
    notes: str | None = None

    def to_model_fields(self) -> dict[str, Any]:
        return {
            "raw_tag": self.raw_tag_private,
            "normalized_tag": self.normalized_tag,
            "canonical_key": self.canonical_key,
            "source_scope": SOURCE_SCOPE,
            "language_script_hints": self.language_script_hints,
            "candidate_namespace": self.candidate_namespace,
            "confidence": self.confidence,
            "status": self.status,
            "source_summary": self.source_summary,
            "frequency": self.frequency,
            "high_value_score": self.high_value_score,
            "unresolved_reason": self.unresolved_reason,
            "next_action": self.next_action,
            "manual_override_status": self.manual_override_status,
            "manual_override_value": self.manual_override_value,
            "notes": self.notes,
        }

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_safe_dict(self) -> dict[str, Any]:
        return {
            "canonical_key_present": bool(self.canonical_key),
            "candidate_namespace": self.candidate_namespace,
            "confidence": round(self.confidence, 4),
            "status": self.status,
            "frequency": self.frequency,
            "high_value_score": round(self.high_value_score, 4),
            "script_hints": self.language_script_hints,
            "source_kinds": sorted(set(self.source_summary.get("source_kinds", []))),
            "unresolved_reason": self.unresolved_reason,
            "next_action": self.next_action,
            "manual_override_status": self.manual_override_status,
        }


@dataclass
class AliasKBEntry:
    source_tag_private: str
    source_canonical_key: str
    target_tag_private: str
    target_canonical_key: str
    relation_type: str
    evidence_source: str
    evidence_payload: dict[str, Any]
    confidence: float
    status: str = "candidate"
    frequency: int = 1
    manual_override_status: str = "none"
    manual_override_value: str | None = None
    notes: str | None = None

    def to_model_fields(self) -> dict[str, Any]:
        return {
            "source_tag": self.source_tag_private,
            "source_canonical_key": self.source_canonical_key,
            "target_tag": self.target_tag_private,
            "target_canonical_key": self.target_canonical_key,
            "relation_type": self.relation_type,
            "evidence_source": self.evidence_source,
            "evidence_payload": self.evidence_payload,
            "confidence": self.confidence,
            "status": self.status,
            "frequency": self.frequency,
            "manual_override_status": self.manual_override_status,
            "manual_override_value": self.manual_override_value,
            "notes": self.notes,
        }

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KbWriteSummary:
    taxonomy_insert_count: int = 0
    taxonomy_update_count: int = 0
    alias_insert_count: int = 0
    alias_update_count: int = 0
    external_cache_write_count: int = 0
    table_names: list[str] = field(default_factory=lambda: [
        "blombooru_pixiv_tag_taxonomy_kb",
        "blombooru_pixiv_tag_alias_kb",
        "blombooru_external_tag_category_lookup_cache",
    ])

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_json_safe(value: Any) -> Any:
    return f1._coerce_json_safe(value)


def _rel(path: Path) -> str:
    return f1._rel(path)


def resolve_repo_path(path: str | Path) -> Path:
    return f1.resolve_repo_path(path)


def write_private_json(path: Path, payload: Any) -> None:
    path = resolve_repo_path(path)
    require_under_phase_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_coerce_json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_private_text(path: Path, content: str) -> None:
    path = resolve_repo_path(path)
    require_under_phase_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def write_public_json(path: Path, payload: Any) -> None:
    f1.write_json(resolve_repo_path(path), payload, expected_parent=Path("docs/reports"))


def write_public_text(path: Path, content: str, *, private_markers: Iterable[str]) -> None:
    f1.assert_public_payload_safe(content, private_markers=private_markers)
    f1.write_text(resolve_repo_path(path), content, expected_parent=Path("docs/reports"))


def require_under_phase_output(path: Path) -> None:
    resolved = resolve_repo_path(path)
    try:
        f1.require_under_path(resolved, ROOT / PHASE_OUTPUT_DIR, code="f4_output_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc


def write_raw_stdout(raw_dir: Path, index: int, stdout: str) -> Path:
    raw_dir = resolve_repo_path(raw_dir)
    require_under_phase_output(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_path = raw_dir / f"metadata-{index:02d}.jsonl"
    require_under_phase_output(output_path)
    output_path.write_text(stdout, encoding="utf-8", newline="\n")
    return output_path


def validate_public_report_paths_before_effects(*, report_json: Path, report_md: Path) -> None:
    for path in (report_json, report_md):
        resolved = resolve_repo_path(path)
        try:
            f1.require_under_path(resolved, ROOT / "docs/reports", code="f4_report_path_violation")
        except f1.OutputPathError as exc:
            raise OutputPathError(str(exc)) from exc


def validate_private_output_paths_before_effects(output_dir: Path, *, private_paths: Sequence[Path]) -> None:
    root = resolve_repo_path(output_dir)
    try:
        f1.require_under_path(root, ROOT / ".local_manifests", code="f4_output_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc
    if PRIVATE_ROOT_SLUG not in root.as_posix():
        raise OutputPathError("f4_output_path_violation")
    for path in private_paths:
        require_under_phase_output(resolve_repo_path(path))


def validate_all_output_paths_before_effects(
    output_dir: Path,
    *,
    private_paths: Sequence[Path],
    report_json: Path,
    report_md: Path,
) -> None:
    validate_public_report_paths_before_effects(report_json=report_json, report_md=report_md)
    validate_private_output_paths_before_effects(output_dir, private_paths=private_paths)


def enforce_caps(*, sample_size: int, max_work_ids: int, max_records: int, max_unique_tags: int, max_external_requests: int) -> None:
    if sample_size < 0:
        raise SampleGateError("sample_or_record_cap_exceeded:sample_size_negative")
    if max_work_ids <= 0 or max_work_ids > MAX_WORK_IDS_WITHOUT_RENEWED_APPROVAL:
        raise SampleGateError("sample_or_record_cap_exceeded:max_work_ids")
    if sample_size > max_work_ids:
        raise SampleGateError("sample_or_record_cap_exceeded:sample_size_exceeds_max_work_ids")
    if max_records <= 0 or max_records > MAX_RECORDS_WITHOUT_RENEWED_APPROVAL:
        raise SampleGateError("sample_or_record_cap_exceeded:max_records")
    if max_unique_tags <= 0 or max_unique_tags > MAX_UNIQUE_TAGS_WITHOUT_RENEWED_APPROVAL:
        raise SampleGateError("tag_lookup_cap_exceeded:max_unique_tags")
    if max_external_requests < 0:
        raise SampleGateError("external_request_budget_negative")


def enforce_record_count(record_count: int, max_records: int) -> None:
    if max_records <= 0 or max_records > MAX_RECORDS_WITHOUT_RENEWED_APPROVAL:
        raise SampleGateError("sample_or_record_cap_exceeded:max_records")
    if record_count > max_records:
        raise SampleGateError("sample_or_record_cap_exceeded:generated_output_exceeds_max_records")


def run_metadata_commands(
    samples: Sequence[f3.SelectedSample],
    entrypoint: f3.GalleryDlEntrypoint,
    raw_dir: Path,
    *,
    max_records: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: int = 120,
) -> tuple[list[f3.CommandResult], f1.ParseResult]:
    f3.enforce_sample_size(len(samples) or 1)
    enforce_record_count(0, max_records)
    results: list[f3.CommandResult] = []
    accepted_parse_results: list[f1.ParseResult] = []
    accepted_media_record_count = 0
    markers = [sample.work_id for sample in samples]
    for index, sample in enumerate(samples, start=1):
        command = f3.build_metadata_command(entrypoint, sample.work_id)
        try:
            completed = runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
        except FileNotFoundError as exc:
            results.append(
                f3.CommandResult(
                    command_kind="metadata",
                    item_index=index,
                    success=False,
                    exit_code=None,
                    stdout_path_private=None,
                    stdout_bytes=0,
                    stderr_redacted=f3.redact_text(str(exc), private_markers=markers),
                    error_class=exc.__class__.__name__,
                )
            )
            continue
        except subprocess.SubprocessError as exc:
            results.append(
                f3.CommandResult(
                    command_kind="metadata",
                    item_index=index,
                    success=False,
                    exit_code=None,
                    stdout_path_private=None,
                    stdout_bytes=0,
                    stderr_redacted=f3.redact_text(str(exc), private_markers=markers),
                    error_class=exc.__class__.__name__,
                )
            )
            continue

        stdout_path = None
        stdout = completed.stdout or ""
        error_class, auth_blocked = f2.classify_stderr(completed.stderr or "")
        success = completed.returncode == 0 and bool(stdout.strip())
        parsed = f1.ParseResult(records=[], files=[])
        parsed_media_count = 0
        blocked_over_limit = False
        if success:
            candidate_path = resolve_repo_path(raw_dir) / f"metadata-{index:02d}.jsonl"
            parsed = f3._parse_gallery_dl_text(stdout, source_file=candidate_path)
            parsed_media_count = len(parsed.media_records)
            if accepted_media_record_count + parsed_media_count > max_records:
                success = False
                blocked_over_limit = True
                error_class = "sample_or_record_cap_exceeded"
            else:
                stdout_path = write_raw_stdout(raw_dir, index, stdout)
                accepted_media_record_count += parsed_media_count
                accepted_parse_results.append(parsed)
        results.append(
            f3.CommandResult(
                command_kind="metadata",
                item_index=index,
                success=success,
                exit_code=completed.returncode,
                stdout_path_private=_rel(stdout_path) if stdout_path else None,
                stdout_bytes=len(stdout.encode("utf-8")),
                stderr_redacted=f3.redact_text(completed.stderr or "", private_markers=markers),
                error_class=None if success else error_class or "empty_metadata_output",
                error_is_auth_or_config=auth_blocked,
                parsed_media_record_count=parsed_media_count,
                blocked_over_limit=blocked_over_limit,
            )
        )
        if blocked_over_limit:
            break
    return results, f3._merge_parse_results(accepted_parse_results)


def load_existing_raw_metadata(raw_dir: Path, *, max_records: int) -> tuple[list[f3.CommandResult], f1.ParseResult]:
    raw_dir = resolve_repo_path(raw_dir)
    require_under_phase_output(raw_dir)
    if not raw_dir.exists():
        return [], f1.ParseResult(records=[], files=[])
    accepted_parse_results: list[f1.ParseResult] = []
    results: list[f3.CommandResult] = []
    accepted_media_record_count = 0
    raw_files = sorted(
        path
        for path in raw_dir.iterdir()
        if path.is_file() and path.suffix.lower() in f1.JSON_EXTENSIONS
    )
    for index, path in enumerate(raw_files, start=1):
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = f3._parse_gallery_dl_text(text, source_file=path)
        parsed_media_count = len(parsed.media_records)
        blocked_over_limit = False
        success = True
        error_class = None
        if accepted_media_record_count + parsed_media_count > max_records:
            success = False
            blocked_over_limit = True
            error_class = "sample_or_record_cap_exceeded"
        else:
            accepted_media_record_count += parsed_media_count
            accepted_parse_results.append(parsed)
        results.append(
            f3.CommandResult(
                command_kind="metadata_reuse_raw",
                item_index=index,
                success=success,
                exit_code=0 if success else None,
                stdout_path_private=_rel(path),
                stdout_bytes=len(text.encode("utf-8")),
                stderr_redacted="",
                error_class=error_class,
                error_is_auth_or_config=False,
                parsed_media_record_count=parsed_media_count,
                blocked_over_limit=blocked_over_limit,
            )
        )
        if blocked_over_limit:
            break
    return results, f3._merge_parse_results(accepted_parse_results)


def language_script_hints(value: str) -> dict[str, Any]:
    text = str(value)
    return {
        "has_hiragana": any("\u3040" <= char <= "\u309f" for char in text),
        "has_katakana": any("\u30a0" <= char <= "\u30ff" for char in text),
        "has_han": any("\u3400" <= char <= "\u9fff" for char in text),
        "has_hangul": any("\uac00" <= char <= "\ud7af" for char in text),
        "has_latin": any("a" <= char.casefold() <= "z" for char in text),
        "has_digits": any(char.isdigit() for char in text),
        "nfkc_changed": unicodedata.normalize("NFKC", text) != text,
        "contains_parentheses": any(char in text for char in "()（）【】[]"),
    }


def high_value_score(raw_tag: str, frequency: int) -> float:
    normalized = f3.normalize_unicode_tag(raw_tag)
    score = 0.0
    if f3._looks_like_proper_noun_candidate(normalized):
        score += 1.0
    hints = language_script_hints(raw_tag)
    if hints["has_han"] or hints["has_hiragana"] or hints["has_katakana"] or hints["has_hangul"]:
        score += 0.25
    if hints["contains_parentheses"]:
        score += 0.2
    if "/" in raw_tag or "／" in raw_tag or ":" in raw_tag or "：" in raw_tag:
        score += 0.15
    score += min(max(frequency - 1, 0) * 0.05, 0.35)
    return round(score, 4)


def load_pr90_summary() -> dict[str, Any]:
    path = resolve_repo_path(PR90_SUMMARY_JSON)
    if not path.exists():
        return {"available": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"available": False, "error": "invalid_json"}
    return {
        "available": True,
        "phase": payload.get("phase"),
        "coverage_target": payload.get("coverage_target", {}),
        "classification_middleware": payload.get("classification_middleware", {}),
        "unresolved_reason_buckets": payload.get("unresolved_reason_buckets", {}),
        "closeout_conclusion": payload.get("closeout_conclusion", {}),
    }


def inspect_pr90_private_artifacts() -> dict[str, Any]:
    root = resolve_repo_path(PR90_PRIVATE_DIR)
    expected = [
        "unresolved-tags-analysis.csv",
        "unresolved-tags-analysis.md",
        "lookup-cache-sheet.csv",
        "lookup-cache-sheet.md",
        "parenthetical-pattern-candidates.csv",
        "parenthetical-pattern-candidates.md",
        "tag-candidates.csv",
        "tag-candidates.md",
        "entity-candidates.csv",
        "entity-candidates.md",
        "manual-review-guide.md",
    ]
    existing = [name for name in expected if (root / name).exists()]
    return {
        "private_artifact_root": str(PR90_PRIVATE_DIR),
        "available": bool(existing),
        "expected_count": len(expected),
        "available_count": len(existing),
        "missing_count": len(expected) - len(existing),
        "regeneration_required_if_live_run": len(existing) == 0,
    }


def fetch_gelbooru_tag_payload(normalized_key: str, *, timeout: int) -> list[dict[str, Any]]:
    query = quote(normalized_key, safe="*_-")
    url = f"{GELBOORU_TAG_LOOKUP_BASE_URL}?page=dapi&s=tag&q=index&name={query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/xml",
            "User-Agent": "VIOLET-Phase44P2R-F4/1.0 (bounded taxonomy alias kb pilot)",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 429}:
            raise f3.ExternalLookupProviderBlocked(f"gelbooru_lookup_blocked_http_{exc.code}") from exc
        raise
    root = ET.fromstring(payload)
    tags: list[dict[str, Any]] = []
    for tag in root.findall("tag"):
        tags.append(
            {
                "id": tag.attrib.get("id"),
                "name": tag.attrib.get("name"),
                "category": tag.attrib.get("type"),
                "count": tag.attrib.get("count"),
            }
        )
    return tags


def _lookup_one_source_f4(
    source: str,
    tag_input: f3.TagLookupInput,
    *,
    timeout_seconds: int,
    max_requests: int,
    delay_seconds: float,
    fetcher: Callable[[str, int], Any] | None = None,
) -> tuple[f3.ExternalTagLookupResult, int]:
    if source == GELBOORU_TAGS_SOURCE:
        if max_requests <= 0:
            return (
                f3.ExternalTagLookupResult(
                    raw_tag=tag_input.raw_tag,
                    normalized_tag=tag_input.normalized_tag,
                    canonical_lookup_key=tag_input.canonical_lookup_key,
                    lookup_source=source,
                    lookup_source_version=source,
                    source_tag_id=None,
                    source_tag_name=None,
                    source_category_raw=None,
                    mapped_candidate_namespace="unknown",
                    confidence=0.0,
                    provenance_url_or_key=f"{source}:{tag_input.canonical_lookup_key}",
                    status="lookup_error",
                    cache_status="miss",
                    lookup_error="external_request_budget_exhausted",
                ),
                0,
            )
        try:
            payload = fetch_gelbooru_tag_payload(tag_input.canonical_lookup_key, timeout=timeout_seconds)
        except f3.ExternalLookupProviderBlocked as exc:
            raise f3.ExternalLookupProviderBlocked(str(exc), request_count=1) from exc
        except Exception as exc:
            raise f3.ExternalLookupRequestError(exc.__class__.__name__, request_count=1) from exc
        return (
            f3._lookup_result_from_danbooru_payload(
                raw_tag=tag_input.raw_tag,
                normalized_tag=tag_input.normalized_tag,
                canonical_lookup_key=tag_input.canonical_lookup_key,
                lookup_source=source,
                lookup_source_version=source,
                payload=payload,
                cache_status="miss",
                matched_lookup_key=tag_input.canonical_lookup_key,
            ),
            1,
        )
    return f3._lookup_one_external_source(
        source,
        tag_input,
        timeout_seconds=timeout_seconds,
        fetcher=fetcher,
        max_requests=max_requests,
        delay_seconds=delay_seconds,
    )


def lookup_external_tag_categories_f4(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    *,
    session: Session | None,
    lookup_limit: int,
    max_external_requests: int,
    delay_seconds: float,
    timeout_seconds: int,
    cache_writes_enabled: bool,
    lookup_sources: Sequence[str] = LOOKUP_SOURCES,
    fetcher: Callable[[str, int], Any] | None = None,
) -> tuple[dict[str, f3.ExternalTagLookupResult], f3.ExternalLookupSummary]:
    f3.enforce_tag_lookup_limit(lookup_limit)
    tag_inputs = f3.build_tag_lookup_inputs(records)
    capped_keys = list(tag_inputs.keys())[:lookup_limit]
    summary = f3.ExternalLookupSummary(
        lookup_source="f4_taxonomy_alias_multisource_lookup_v1",
        lookup_sources_attempted=list(lookup_sources),
        external_request_budget=max_external_requests,
        lookup_limit=lookup_limit,
        unique_normalized_tag_count=len(tag_inputs),
        lookup_delay_seconds=delay_seconds,
        lookup_timeout_seconds=timeout_seconds,
        cache_write_enabled=bool(cache_writes_enabled and session is not None),
    )
    results: dict[str, f3.ExternalTagLookupResult] = {}
    blocked_sources: set[str] = set()
    for idx, key in enumerate(capped_keys):
        tag_input = tag_inputs[key]
        if idx > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        last_result: f3.ExternalTagLookupResult | None = None
        for source in lookup_sources:
            if source in blocked_sources:
                continue
            cached_result: f3.ExternalTagLookupResult | None = None
            if session is not None:
                cached_row = (
                    session.query(ExternalTagCategoryLookupCache)
                    .filter(
                        ExternalTagCategoryLookupCache.lookup_source == source,
                        ExternalTagCategoryLookupCache.canonical_lookup_key == key,
                    )
                    .one_or_none()
                )
                if cached_row and str(cached_row.status) in f3.SUCCESSFUL_LOOKUP_STATUSES:
                    cached_result = f3._cache_row_to_lookup_result(cached_row)
                    summary.cache_hit_count += 1
                    summary.cache_hit_resolved_count += 1
                    summary.source_cache_hit_counts[source] = summary.source_cache_hit_counts.get(source, 0) + 1
                elif cached_row and f3._not_found_cache_is_fresh(cached_row, source=source):
                    last_result = f3._cache_row_to_lookup_result(cached_row, cache_status="negative_not_found")
                    summary.negative_cache_hit_count += 1
                    summary.cache_negative_not_found_count += 1
                    summary.source_negative_cache_hit_counts[source] = (
                        summary.source_negative_cache_hit_counts.get(source, 0) + 1
                    )
                    continue
                elif cached_row and f3._lookup_error_cache_in_cooldown(cached_row, source=source):
                    last_result = f3._cache_row_to_lookup_result(cached_row, cache_status="error_cooldown")
                    summary.cache_error_cooldown_count += 1
                    continue
                else:
                    if cached_row and str(cached_row.status) in {"not_found", "lookup_error"}:
                        summary.cache_expired_retryable_count += 1
                    summary.cache_miss_count += 1
                    summary.source_cache_miss_counts[source] = summary.source_cache_miss_counts.get(source, 0) + 1
            else:
                summary.cache_miss_count += 1
                summary.source_cache_miss_counts[source] = summary.source_cache_miss_counts.get(source, 0) + 1
            if cached_result:
                results[key] = cached_result
                last_result = cached_result
                break
            remaining = max(summary.external_request_budget - summary.request_count, 0)
            if remaining <= 0:
                summary.request_budget_exhausted = True
                break
            cache_result_allowed = True
            try:
                result, request_count = _lookup_one_source_f4(
                    source,
                    tag_input,
                    timeout_seconds=timeout_seconds,
                    max_requests=remaining,
                    delay_seconds=delay_seconds,
                    fetcher=fetcher if len(lookup_sources) == 1 else None,
                )
                summary.request_count += request_count
                summary.source_request_counts[source] = summary.source_request_counts.get(source, 0) + request_count
            except f3.ExternalLookupProviderBlocked as exc:
                request_count = max(int(getattr(exc, "request_count", 1)), 0)
                summary.request_count += min(request_count, remaining)
                summary.source_request_counts[source] = summary.source_request_counts.get(source, 0) + min(request_count, remaining)
                summary.provider_blocked = True
                summary.provider_block_reason = str(exc)
                blocked_sources.add(source)
                if source not in summary.provider_blocked_sources:
                    summary.provider_blocked_sources.append(source)
                cache_result_allowed = False
                result = f3.ExternalTagLookupResult(
                    raw_tag=tag_input.raw_tag,
                    normalized_tag=tag_input.normalized_tag,
                    canonical_lookup_key=key,
                    lookup_source=source,
                    lookup_source_version=source,
                    source_tag_id=None,
                    source_tag_name=None,
                    source_category_raw=None,
                    mapped_candidate_namespace="unknown",
                    confidence=0.0,
                    provenance_url_or_key=f"{source}:{key}",
                    status="lookup_error",
                    cache_status="miss",
                    lookup_error=str(exc),
                )
            except f3.ExternalLookupRequestError as exc:
                request_count = max(int(getattr(exc, "request_count", 1)), 0)
                summary.request_count += min(request_count, remaining)
                summary.source_request_counts[source] = summary.source_request_counts.get(source, 0) + min(request_count, remaining)
                result = f3.ExternalTagLookupResult(
                    raw_tag=tag_input.raw_tag,
                    normalized_tag=tag_input.normalized_tag,
                    canonical_lookup_key=key,
                    lookup_source=source,
                    lookup_source_version=source,
                    source_tag_id=None,
                    source_tag_name=None,
                    source_category_raw=None,
                    mapped_candidate_namespace="unknown",
                    confidence=0.0,
                    provenance_url_or_key=f"{source}:{key}",
                    status="lookup_error",
                    cache_status="miss",
                    lookup_error=str(exc),
                )
            result = f3._with_default_cache_lifecycle(result)
            last_result = result
            if session is not None and cache_writes_enabled and cache_result_allowed:
                f3._upsert_lookup_cache_result(session, result)
                summary.cache_write_count += 1
                summary.source_cache_write_counts[source] = summary.source_cache_write_counts.get(source, 0) + 1
            if result.status == "hit":
                results[key] = result
                break
            if summary.request_count >= summary.external_request_budget:
                summary.request_budget_exhausted = True
                break
        if key not in results and last_result is not None:
            results[key] = last_result
        if summary.request_budget_exhausted:
            break
    if session is not None and cache_writes_enabled and summary.cache_write_count:
        session.commit()

    status_counts = Counter(result.status for result in results.values())
    namespace_counts = Counter(
        result.mapped_candidate_namespace or "unknown"
        for result in results.values()
        if result.status == "hit"
    )
    summary.hit_count = status_counts.get("hit", 0)
    summary.not_found_count = status_counts.get("not_found", 0)
    summary.lookup_error_count = status_counts.get("lookup_error", 0)
    summary.resolved_namespace_counts = dict(sorted(namespace_counts.items()))
    for result in results.values():
        if result.status == "hit":
            summary.source_hit_counts[result.lookup_source] = summary.source_hit_counts.get(result.lookup_source, 0) + 1
        elif result.status == "not_found":
            summary.source_not_found_counts[result.lookup_source] = summary.source_not_found_counts.get(result.lookup_source, 0) + 1
        elif result.status == "lookup_error":
            summary.source_lookup_error_counts[result.lookup_source] = summary.source_lookup_error_counts.get(result.lookup_source, 0) + 1
    return results, summary


def lookup_external_tag_categories_cache_only(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    *,
    session: Session | None,
    lookup_limit: int,
    lookup_sources: Sequence[str] = LOOKUP_SOURCES,
    skip_reason: str = "external_lookup_network_skipped",
) -> tuple[dict[str, f3.ExternalTagLookupResult], f3.ExternalLookupSummary]:
    f3.enforce_tag_lookup_limit(lookup_limit)
    tag_inputs = f3.build_tag_lookup_inputs(records)
    capped_keys = list(tag_inputs.keys())[:lookup_limit]
    summary = f3.ExternalLookupSummary(
        lookup_source="f4_taxonomy_alias_cache_only_lookup_v1",
        lookup_sources_attempted=list(lookup_sources),
        external_request_budget=0,
        lookup_limit=lookup_limit,
        unique_normalized_tag_count=len(tag_inputs),
        request_count=0,
        cache_write_enabled=False,
        provider_blocked=True,
        provider_block_reason=skip_reason,
        provider_blocked_sources=list(lookup_sources),
    )
    results: dict[str, f3.ExternalTagLookupResult] = {}
    for key in capped_keys:
        tag_input = tag_inputs[key]
        last_result: f3.ExternalTagLookupResult | None = None
        for source in lookup_sources:
            cached_row = None
            if session is not None:
                cached_row = (
                    session.query(ExternalTagCategoryLookupCache)
                    .filter(
                        ExternalTagCategoryLookupCache.lookup_source == source,
                        ExternalTagCategoryLookupCache.canonical_lookup_key == key,
                    )
                    .one_or_none()
                )
            if cached_row and str(cached_row.status) in f3.SUCCESSFUL_LOOKUP_STATUSES:
                result = f3._cache_row_to_lookup_result(cached_row)
                summary.cache_hit_count += 1
                summary.cache_hit_resolved_count += 1
                summary.source_cache_hit_counts[source] = summary.source_cache_hit_counts.get(source, 0) + 1
                results[key] = result
                last_result = result
                break
            if cached_row and f3._not_found_cache_is_fresh(cached_row, source=source):
                last_result = f3._cache_row_to_lookup_result(cached_row, cache_status="negative_not_found")
                summary.negative_cache_hit_count += 1
                summary.cache_negative_not_found_count += 1
                summary.source_negative_cache_hit_counts[source] = (
                    summary.source_negative_cache_hit_counts.get(source, 0) + 1
                )
                continue
            if cached_row and f3._lookup_error_cache_in_cooldown(cached_row, source=source):
                last_result = f3._cache_row_to_lookup_result(cached_row, cache_status="error_cooldown")
                summary.cache_error_cooldown_count += 1
                continue
            summary.cache_miss_count += 1
            summary.source_cache_miss_counts[source] = summary.source_cache_miss_counts.get(source, 0) + 1
        if key not in results:
            results[key] = last_result or f3.ExternalTagLookupResult(
                raw_tag=tag_input.raw_tag,
                normalized_tag=tag_input.normalized_tag,
                canonical_lookup_key=key,
                lookup_source="f4_cache_only_network_skipped",
                lookup_source_version="f4_cache_only_network_skipped",
                source_tag_id=None,
                source_tag_name=None,
                source_category_raw=None,
                mapped_candidate_namespace="unknown",
                confidence=0.0,
                provenance_url_or_key=f"cache_only:{key}",
                status="lookup_error",
                cache_status="network_skipped",
                lookup_error=skip_reason,
            )
    status_counts = Counter(result.status for result in results.values())
    namespace_counts = Counter(
        result.mapped_candidate_namespace or "unknown"
        for result in results.values()
        if result.status == "hit"
    )
    summary.hit_count = status_counts.get("hit", 0)
    summary.not_found_count = status_counts.get("not_found", 0)
    summary.lookup_error_count = status_counts.get("lookup_error", 0)
    summary.resolved_namespace_counts = dict(sorted(namespace_counts.items()))
    for result in results.values():
        if result.status == "hit":
            summary.source_hit_counts[result.lookup_source] = summary.source_hit_counts.get(result.lookup_source, 0) + 1
        elif result.status == "not_found":
            summary.source_not_found_counts[result.lookup_source] = summary.source_not_found_counts.get(result.lookup_source, 0) + 1
        elif result.status == "lookup_error":
            summary.source_lookup_error_counts[result.lookup_source] = summary.source_lookup_error_counts.get(result.lookup_source, 0) + 1
    return results, summary


def load_curated_mappings(path: str | Path | None) -> dict[str, CuratedMapping]:
    if not path:
        return {}
    resolved = resolve_repo_path(path)
    try:
        f1.require_under_path(resolved, ROOT, code="curated_mapping_path_violation")
    except f1.OutputPathError as exc:
        raise CuratedMappingError(str(exc)) from exc
    if not resolved.exists():
        raise CuratedMappingError("curated_mapping_input_missing")
    rows: list[dict[str, Any]]
    if resolved.suffix.lower() == ".json":
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = list(payload.get("mappings") or [])
        elif isinstance(payload, list):
            rows = payload
        else:
            raise CuratedMappingError("curated_mapping_json_shape_invalid")
    else:
        with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    mappings: dict[str, CuratedMapping] = {}
    for row in rows:
        source_tag = str(row.get("source_tag") or row.get("raw_tag") or "").strip()
        namespace = str(row.get("candidate_namespace") or "").strip().casefold()
        if not source_tag or namespace not in RESOLVED_NAMESPACES:
            continue
        canonical_key = str(row.get("canonical_key") or f3.multilingual_normalize_tag(source_tag).canonical_lookup_key)
        mappings[canonical_key] = CuratedMapping(
            source_tag=source_tag,
            canonical_key=canonical_key,
            candidate_namespace=namespace,
            confidence=float(row.get("confidence") or 0.95),
            status=str(row.get("status") or "resolved"),
            target_tag=str(row.get("target_tag") or "").strip() or None,
            relation_type=str(row.get("relation_type") or "").strip() or None,
            notes=str(row.get("notes") or "").strip() or None,
        )
    return mappings


def _row_key(row: f3.PixivCandidateRow) -> str:
    return row.canonical_lookup_key or row.lookup_key or f3.lookup_key(row.normalized_tag)


def _resolved_row_priority(row: f3.PixivCandidateRow) -> tuple[int, float]:
    source = row.lookup_source or ""
    if row.reason == "external_tag_category_lookup_manual_override":
        return (100, row.confidence)
    if source == "local_db_read_only":
        return (90, row.confidence)
    if source in {f3.DANBOORU_TAGS_SOURCE, f3.DANBOORU_ALIAS_SOURCE}:
        return (80, row.confidence)
    if source in {f3.SAFEBOORU_TAGS_SOURCE, GELBOORU_TAGS_SOURCE}:
        return (70, row.confidence)
    if row.reason == "pixiv_parenthetical_character_work_pattern":
        return (60, row.confidence)
    return (10, row.confidence)


def unresolved_reason_for(
    *,
    key: str,
    raw_tag: str,
    rows: Sequence[f3.PixivCandidateRow],
    lookup_result: f3.ExternalTagLookupResult | None,
) -> tuple[str, str]:
    if lookup_result and lookup_result.lookup_error:
        return "provider_limited_or_lookup_error", "retry_provider_or_add_curated_mapping_after_source_status_review"
    if lookup_result and lookup_result.status == "not_found":
        return "provider_not_found", "add_curated_mapping_or_add_new_taxonomy_source"
    if any(row.reason == "deterministic_original_marker" for row in rows):
        return "original_or_creator_specific_tag", "keep_as_unresolved_context_or_curate_as_original_marker"
    if any(row.reason == "pixiv_parenthetical_character_work_pattern" for row in rows):
        return "parenthetical_context_needs_alias_confirmation", "review_parenthetical_alias_relation_before_entity_candidate_use"
    if f3._looks_like_proper_noun_candidate(f3.normalize_unicode_tag(raw_tag)):
        return "language_alias_mismatch_or_pixiv_only_tag", "curated_mapping_or_add_multilingual_alias_source"
    if "/" in raw_tag or "／" in raw_tag:
        return "slash_separated_alias_or_multi_tag", "split_or_curate_alias_mapping"
    if key:
        return "no_provenance_backed_category", "curated_mapping_or_new_source_required"
    return "normalization_failed", "inspect_raw_tag_and_add_parser_support"


def build_taxonomy_entries(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    candidate_rows: Sequence[f3.PixivCandidateRow],
    lookup_results: Mapping[str, f3.ExternalTagLookupResult],
    curated_mappings: Mapping[str, CuratedMapping],
) -> list[TaxonomyKBEntry]:
    raw_by_key: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        for raw_tag in record.tags:
            profile = f3.multilingual_normalize_tag(raw_tag)
            if profile.canonical_lookup_key:
                raw_by_key[profile.canonical_lookup_key][raw_tag] += 1
    rows_by_key: dict[str, list[f3.PixivCandidateRow]] = defaultdict(list)
    for row in candidate_rows:
        key = _row_key(row)
        if key:
            rows_by_key[key].append(row)
            if row.raw_tag:
                raw_by_key[key][row.raw_tag] += 0

    entries: list[TaxonomyKBEntry] = []
    for key, raw_counter in sorted(raw_by_key.items()):
        raw_tag, frequency = raw_counter.most_common(1)[0]
        profile = f3.multilingual_normalize_tag(raw_tag)
        rows = rows_by_key.get(key, [])
        curated = curated_mappings.get(key)
        lookup_result = lookup_results.get(key)
        source_kinds = sorted({row.lookup_source or "none" for row in rows})
        if lookup_result:
            source_kinds.append(lookup_result.lookup_source)
        resolved_rows = [
            row
            for row in rows
            if row.candidate_namespace in RESOLVED_NAMESPACES
            and (
                row.lookup_source in {
                    "local_db_read_only",
                    f3.DANBOORU_TAGS_SOURCE,
                    f3.DANBOORU_ALIAS_SOURCE,
                    f3.SAFEBOORU_TAGS_SOURCE,
                    GELBOORU_TAGS_SOURCE,
                }
                or row.reason == "pixiv_parenthetical_character_work_pattern"
            )
        ]
        if curated:
            namespace = curated.candidate_namespace
            status = "resolved_manual_override"
            confidence = curated.confidence
            unresolved_reason = None
            next_action = None
            source_summary = {
                "source_kinds": ["curated_mapping_input"],
                "selected_reason": "curated_mapping_input",
                "notes": curated.notes,
            }
            manual_status = "operator_curated"
            manual_value = namespace
        elif resolved_rows:
            selected = sorted(resolved_rows, key=_resolved_row_priority, reverse=True)[0]
            namespace = selected.candidate_namespace
            status = "resolved"
            confidence = selected.confidence
            unresolved_reason = None
            next_action = None
            source_summary = {
                "source_kinds": sorted(set(source_kinds)),
                "selected_reason": selected.reason,
                "selected_lookup_source": selected.lookup_source,
                "selected_provenance": selected.provenance,
            }
            manual_status = "none"
            manual_value = None
        else:
            namespace = "ambiguous" if f3._looks_like_proper_noun_candidate(profile.whitespace_normalized) else "unknown"
            status = "unresolved_governed"
            confidence = 0.0
            unresolved_reason, next_action = unresolved_reason_for(
                key=key,
                raw_tag=raw_tag,
                rows=rows,
                lookup_result=lookup_result,
            )
            source_summary = {
                "source_kinds": sorted(set(source_kinds)),
                "selected_reason": "unresolved_governance",
                "lookup_status": lookup_result.status if lookup_result else "not_looked_up",
                "lookup_error": lookup_result.lookup_error if lookup_result else None,
            }
            manual_status = "none"
            manual_value = None
        entries.append(
            TaxonomyKBEntry(
                raw_tag_private=raw_tag,
                normalized_tag=profile.whitespace_normalized,
                canonical_key=key,
                candidate_namespace=namespace,
                confidence=confidence,
                status=status,
                source_summary=source_summary,
                frequency=int(frequency),
                high_value_score=high_value_score(raw_tag, int(frequency)),
                language_script_hints=language_script_hints(raw_tag),
                unresolved_reason=unresolved_reason,
                next_action=next_action,
                manual_override_status=manual_status,
                manual_override_value=manual_value,
            )
        )
    return entries


def _alias_key(source: str, target: str, relation_type: str, evidence_source: str) -> tuple[str, str, str, str]:
    return (source, target, relation_type, evidence_source)


def build_alias_entries(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    candidate_rows: Sequence[f3.PixivCandidateRow],
    lookup_results: Mapping[str, f3.ExternalTagLookupResult],
    curated_mappings: Mapping[str, CuratedMapping],
) -> list[AliasKBEntry]:
    entries: dict[tuple[str, str, str, str], AliasKBEntry] = {}
    for record in records:
        for raw_tag in record.tags:
            profile = f3.multilingual_normalize_tag(raw_tag)
            normalized = profile.punctuation_normalized_form
            if normalized and normalized != raw_tag:
                target_key = f3.multilingual_normalize_tag(normalized).canonical_lookup_key
                key = _alias_key(profile.canonical_lookup_key, target_key, "translation", "multilingual_normalization")
                entries.setdefault(
                    key,
                    AliasKBEntry(
                        source_tag_private=raw_tag,
                        source_canonical_key=profile.canonical_lookup_key,
                        target_tag_private=normalized,
                        target_canonical_key=target_key,
                        relation_type="translation",
                        evidence_source="multilingual_normalization",
                        evidence_payload=profile.public_safe_dict(),
                        confidence=0.55,
                        status="candidate",
                    ),
                )
            pattern = f3.parse_pixiv_parenthetical_tag(raw_tag)
            if pattern and not pattern.ambiguous_or_nested:
                source_key = f3.multilingual_normalize_tag(pattern.outer_name).canonical_lookup_key
                target_key = f3.multilingual_normalize_tag(pattern.inner_work_or_context).canonical_lookup_key
                key = _alias_key(source_key, target_key, "parenthetical_character_of_work", "pixiv_parenthetical_pattern")
                entries.setdefault(
                    key,
                    AliasKBEntry(
                        source_tag_private=pattern.outer_name,
                        source_canonical_key=source_key,
                        target_tag_private=pattern.inner_work_or_context,
                        target_canonical_key=target_key,
                        relation_type="parenthetical_character_of_work",
                        evidence_source="pixiv_parenthetical_pattern",
                        evidence_payload={"ambiguous_or_nested": False},
                        confidence=0.72,
                        status="candidate",
                    ),
                )

    for result in lookup_results.values():
        if result.status != "hit" or not result.source_tag_name:
            continue
        target_key = f3.multilingual_normalize_tag(result.source_tag_name).canonical_lookup_key
        if target_key == result.canonical_lookup_key:
            continue
        key = _alias_key(result.canonical_lookup_key, target_key, "provider_canonical", result.lookup_source)
        entries.setdefault(
            key,
            AliasKBEntry(
                source_tag_private=result.raw_tag or result.normalized_tag,
                source_canonical_key=result.canonical_lookup_key,
                target_tag_private=result.source_tag_name,
                target_canonical_key=target_key,
                relation_type="provider_canonical",
                evidence_source=result.lookup_source,
                evidence_payload={"source_tag_id_present": bool(result.source_tag_id)},
                confidence=float(result.confidence or 0.0),
                status="candidate",
            ),
        )

    for curated in curated_mappings.values():
        if not curated.target_tag:
            continue
        target_key = f3.multilingual_normalize_tag(curated.target_tag).canonical_lookup_key
        relation_type = curated.relation_type or "alias"
        key = _alias_key(curated.canonical_key, target_key, relation_type, "curated_mapping_input")
        entries[key] = AliasKBEntry(
            source_tag_private=curated.source_tag,
            source_canonical_key=curated.canonical_key,
            target_tag_private=curated.target_tag,
            target_canonical_key=target_key,
            relation_type=relation_type,
            evidence_source="curated_mapping_input",
            evidence_payload={"notes": curated.notes},
            confidence=curated.confidence,
            status="candidate",
            manual_override_status="operator_curated",
            manual_override_value=curated.candidate_namespace,
        )

    work_tag_sets: dict[str, set[str]] = defaultdict(set)
    raw_for_key: dict[str, str] = {}
    resolved_context: set[str] = set()
    row_namespace_by_key: dict[str, str] = {}
    for row in candidate_rows:
        key = _row_key(row)
        if not key:
            continue
        raw_for_key.setdefault(key, row.raw_tag)
        row_namespace_by_key.setdefault(key, row.candidate_namespace)
        if row.work_id_private:
            work_tag_sets[str(row.work_id_private)].add(key)
        if row.candidate_namespace in {"copyright", "character"}:
            resolved_context.add(key)
    pair_counts: Counter[tuple[str, str]] = Counter()
    for keys in work_tag_sets.values():
        unresolved = [
            key
            for key in keys
            if row_namespace_by_key.get(key) in {"ambiguous", "unknown"}
        ]
        contexts = [key for key in keys if key in resolved_context]
        for source_key in unresolved:
            for target_key in contexts:
                if source_key != target_key:
                    pair_counts[(source_key, target_key)] += 1
    for (source_key, target_key), frequency in pair_counts.items():
        if frequency < 2:
            continue
        key = _alias_key(source_key, target_key, "cooccurrence_candidate", "pixiv_same_work_tag_cooccurrence")
        entries.setdefault(
            key,
            AliasKBEntry(
                source_tag_private=raw_for_key.get(source_key, source_key),
                source_canonical_key=source_key,
                target_tag_private=raw_for_key.get(target_key, target_key),
                target_canonical_key=target_key,
                relation_type="cooccurrence_candidate",
                evidence_source="pixiv_same_work_tag_cooccurrence",
                evidence_payload={"work_cooccurrence_count": frequency},
                confidence=min(0.45 + frequency * 0.05, 0.65),
                status="candidate",
                frequency=frequency,
            ),
        )
    return sorted(entries.values(), key=lambda item: (item.relation_type, item.source_canonical_key, item.target_canonical_key))


def upsert_taxonomy_entries(session: Session, entries: Sequence[TaxonomyKBEntry]) -> tuple[int, int]:
    inserts = 0
    updates = 0
    for entry in entries:
        row = (
            session.query(PixivTagTaxonomyKnowledgeBase)
            .filter(
                PixivTagTaxonomyKnowledgeBase.source_scope == SOURCE_SCOPE,
                PixivTagTaxonomyKnowledgeBase.canonical_key == entry.canonical_key,
            )
            .one_or_none()
        )
        fields = entry.to_model_fields()
        if row is None:
            session.add(PixivTagTaxonomyKnowledgeBase(**fields))
            inserts += 1
            continue
        if str(row.manual_override_status or "none") != "none":
            fields.pop("manual_override_status", None)
            fields.pop("manual_override_value", None)
            fields["candidate_namespace"] = row.manual_override_value or row.candidate_namespace
            fields["status"] = "resolved_manual_override"
        for key, value in fields.items():
            if key == "created_at":
                continue
            setattr(row, key, value)
        updates += 1
    return inserts, updates


def upsert_alias_entries(session: Session, entries: Sequence[AliasKBEntry]) -> tuple[int, int]:
    inserts = 0
    updates = 0
    for entry in entries:
        row = (
            session.query(PixivTagAliasKnowledgeBase)
            .filter(
                PixivTagAliasKnowledgeBase.source_canonical_key == entry.source_canonical_key,
                PixivTagAliasKnowledgeBase.target_canonical_key == entry.target_canonical_key,
                PixivTagAliasKnowledgeBase.relation_type == entry.relation_type,
                PixivTagAliasKnowledgeBase.evidence_source == entry.evidence_source,
            )
            .one_or_none()
        )
        fields = entry.to_model_fields()
        if row is None:
            session.add(PixivTagAliasKnowledgeBase(**fields))
            inserts += 1
            continue
        if str(row.manual_override_status or "none") != "none":
            fields.pop("manual_override_status", None)
            fields.pop("manual_override_value", None)
        for key, value in fields.items():
            setattr(row, key, value)
        updates += 1
    return inserts, updates


def install_taxonomy_alias_kb_write_guard(engine) -> None:
    write_re = re.compile(r"^\s*(insert|update|delete|alter|drop|truncate|create)\b", re.IGNORECASE)
    destructive_re = re.compile(r"^\s*(delete|drop|truncate)\b", re.IGNORECASE)
    allowed_re = re.compile(
        r"^\s*(insert\s+into|update)\s+\"?(?:"
        + "|".join(re.escape(table) for table in sorted(ALLOWED_WRITE_TABLES))
        + r")\"?\b",
        re.IGNORECASE,
    )

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(_conn, _cursor, statement, _parameters, _context, _executemany):
        sql = str(statement).strip()
        if not write_re.search(sql):
            return
        lowered = sql.casefold()
        if destructive_re.search(sql):
            touched = sorted(table for table in FORBIDDEN_TABLES | ALLOWED_WRITE_TABLES if table in lowered)
            detail = ",".join(touched) if touched else "destructive_write"
            raise f1.ReadOnlyViolation(f"db_write_blocked_f4_destructive:{detail}")
        if allowed_re.search(sql):
            return
        touched_truth_tables = sorted(table for table in FORBIDDEN_TABLES if table in lowered)
        detail = ",".join(touched_truth_tables) if touched_truth_tables else "non_kb_write"
        raise f1.ReadOnlyViolation(f"db_write_blocked_except_f4_kb_tables:{detail}")


def write_kb_entries(
    session: Session,
    taxonomy_entries: Sequence[TaxonomyKBEntry],
    alias_entries: Sequence[AliasKBEntry],
    *,
    external_cache_write_count: int,
) -> KbWriteSummary:
    taxonomy_inserts, taxonomy_updates = upsert_taxonomy_entries(session, taxonomy_entries)
    alias_inserts, alias_updates = upsert_alias_entries(session, alias_entries)
    session.commit()
    return KbWriteSummary(
        taxonomy_insert_count=taxonomy_inserts,
        taxonomy_update_count=taxonomy_updates,
        alias_insert_count=alias_inserts,
        alias_update_count=alias_updates,
        external_cache_write_count=external_cache_write_count,
    )


def taxonomy_kb_summary(entries: Sequence[TaxonomyKBEntry]) -> dict[str, Any]:
    status_counts = Counter(entry.status for entry in entries)
    namespace_counts = Counter(entry.candidate_namespace for entry in entries)
    unresolved_reasons = Counter(entry.unresolved_reason for entry in entries if entry.unresolved_reason)
    return {
        "entry_count": len(entries),
        "status_counts": dict(sorted(status_counts.items())),
        "candidate_namespace_counts": dict(sorted(namespace_counts.items())),
        "unresolved_reason_buckets": dict(sorted(unresolved_reasons.items())),
        "resolved_entry_count": sum(1 for entry in entries if entry.candidate_namespace in RESOLVED_NAMESPACES and entry.status.startswith("resolved")),
        "manual_override_entry_count": sum(1 for entry in entries if entry.manual_override_status != "none"),
    }


def alias_kb_summary(entries: Sequence[AliasKBEntry]) -> dict[str, Any]:
    return {
        "entry_count": len(entries),
        "relation_type_counts": dict(sorted(Counter(entry.relation_type for entry in entries).items())),
        "evidence_source_counts": dict(sorted(Counter(entry.evidence_source for entry in entries).items())),
        "manual_override_entry_count": sum(1 for entry in entries if entry.manual_override_status != "none"),
    }


def top_high_impact_entries(entries: Sequence[TaxonomyKBEntry], *, limit: int = 50) -> list[TaxonomyKBEntry]:
    candidates = [
        entry
        for entry in entries
        if entry.high_value_score > 0
        and (
            entry.candidate_namespace in {"ambiguous", "unknown"}
            or entry.status.startswith("unresolved")
        )
    ]
    return sorted(candidates, key=lambda entry: (entry.high_value_score, entry.frequency, entry.canonical_key), reverse=True)[:limit]


def coverage_target_summary(entries: Sequence[TaxonomyKBEntry], candidate_rows: Sequence[f3.PixivCandidateRow]) -> dict[str, Any]:
    unique_count = len(entries)
    resolved_entries = [
        entry
        for entry in entries
        if entry.candidate_namespace in RESOLVED_NAMESPACES
        and entry.status.startswith("resolved")
    ]
    unique_coverage = len(resolved_entries) / max(unique_count, 1)
    high_value = [entry for entry in entries if entry.high_value_score > 0]
    high_value_resolved = [
        entry
        for entry in high_value
        if entry.candidate_namespace in {"artist", "copyright", "character"}
        and entry.status.startswith("resolved")
    ]
    high_value_rate = len(high_value_resolved) / max(len(high_value), 1)
    namespace_counts = Counter(row.candidate_namespace for row in candidate_rows)
    ambiguous_unknown = namespace_counts.get("ambiguous", 0) + namespace_counts.get("unknown", 0)
    ambiguous_reduction = (
        (PREVIOUS_PR90_BASELINE["ambiguous_unknown_candidate_count"] - ambiguous_unknown)
        / max(PREVIOUS_PR90_BASELINE["ambiguous_unknown_candidate_count"], 1)
        if candidate_rows
        else 0.0
    )
    top_entries = top_high_impact_entries(entries)
    top_governed = [
        entry
        for entry in top_entries
        if (
            entry.candidate_namespace in RESOLVED_NAMESPACES
            and entry.status.startswith("resolved")
        )
        or (entry.unresolved_reason and entry.next_action)
    ]
    top_coverage = len(top_governed) / max(len(top_entries), 1)
    unique_target = unique_coverage >= 0.6
    high_value_target = high_value_rate >= 0.8
    ambiguous_target = bool(candidate_rows and ambiguous_reduction >= 0.6)
    top_target = len(top_entries) > 0 and top_coverage >= 1.0
    classification_target = unique_target or high_value_target or ambiguous_target
    if unique_target:
        target_status = "reached_unique_tag_classification"
    elif high_value_target:
        target_status = "reached_high_value_proper_noun_classification"
    elif ambiguous_target:
        target_status = "reached_ambiguous_reduction"
    elif top_target:
        target_status = "classification_not_reached_top_high_impact_governed"
    else:
        target_status = "not_reached"
    return {
        "target_status": target_status,
        "target_reached": classification_target,
        "classification_target_reached": classification_target,
        "governance_target_reached": top_target,
        "unique_tag_count": unique_count,
        "resolved_unique_tag_count": len(resolved_entries),
        "resolved_unique_tag_coverage": round(unique_coverage, 4),
        "high_value_proper_noun_like_tag_count": len(high_value),
        "high_value_proper_noun_like_resolved_count": len(high_value_resolved),
        "high_value_proper_noun_like_resolution_rate": round(high_value_rate, 4),
        "ambiguous_unknown_candidate_count_after_f4": ambiguous_unknown,
        "ambiguous_unknown_reduction_vs_pr90": round(ambiguous_reduction, 4),
        "top_high_impact_count": len(top_entries),
        "top_high_impact_governed_count": len(top_governed),
        "top_high_impact_governance_rate": round(top_coverage, 4),
        "coverage_target_thresholds": {
            "unique_tag_coverage": 0.6,
            "high_value_resolution": 0.8,
            "ambiguous_reduction": 0.6,
            "top_high_impact_governance": 1.0,
        },
        "thresholds_reached": {
            "unique_tag_classification": unique_target,
            "high_value_proper_noun_classification": high_value_target,
            "ambiguous_reduction": ambiguous_target,
            "top_high_impact_governance": top_target,
        },
    }


def recommendation_from_coverage(coverage: Mapping[str, Any]) -> dict[str, str]:
    if not coverage.get("target_reached"):
        return {
            "persistence_recommendation": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "entity_candidate_persistence": "blocked_until_category_coverage_and_provenance_improve",
            "next_route": "continue_taxonomy_alias_kb_with_curated_mapping_or_new_source",
            "reason": "coverage_target_not_reached",
        }
    if coverage.get("thresholds_reached", {}).get("unique_tag_classification") or coverage.get("thresholds_reached", {}).get("high_value_proper_noun_classification"):
        return {
            "persistence_recommendation": "PixivMetadata_LocalSourceHint_persistence_design_may_proceed_as_design_only",
            "entity_candidate_persistence": "still_blocked_until_separate_review_and_stronger_category_evidence",
            "next_route": "design_persistence_boundary_without_entity_candidate_writes",
            "reason": "classification_threshold_reached_but_truth_tables_remain_out_of_scope",
        }
    return {
        "persistence_recommendation": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet_based_on_governance_only",
        "entity_candidate_persistence": "blocked_until_category_coverage_and_provenance_improve",
        "next_route": "use_top_high_impact_governance_to_drive_curated_mapping_or_new_source",
        "reason": "only_high_impact_unresolved_governance_threshold_reached",
    }


def _csv_from_dicts(rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> str:
    from io import StringIO

    handle = StringIO()
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return handle.getvalue()


def taxonomy_csv(entries: Sequence[TaxonomyKBEntry]) -> str:
    rows = [entry.to_private_dict() for entry in entries]
    return _csv_from_dicts(
        rows,
        [
            "raw_tag_private",
            "normalized_tag",
            "canonical_key",
            "candidate_namespace",
            "confidence",
            "status",
            "frequency",
            "high_value_score",
            "unresolved_reason",
            "next_action",
            "manual_override_status",
            "manual_override_value",
            "notes",
        ],
    )


def alias_csv(entries: Sequence[AliasKBEntry]) -> str:
    rows = [entry.to_private_dict() for entry in entries]
    return _csv_from_dicts(
        rows,
        [
            "source_tag_private",
            "source_canonical_key",
            "target_tag_private",
            "target_canonical_key",
            "relation_type",
            "evidence_source",
            "confidence",
            "status",
            "frequency",
            "manual_override_status",
            "manual_override_value",
            "notes",
        ],
    )


def unresolved_csv(entries: Sequence[TaxonomyKBEntry]) -> str:
    unresolved = [
        entry
        for entry in entries
        if entry.status.startswith("unresolved") or entry.candidate_namespace in {"ambiguous", "unknown"}
    ]
    return taxonomy_csv(sorted(unresolved, key=lambda item: (item.high_value_score, item.frequency), reverse=True))


def media_summary_csv(records: Sequence[f2.PixivGalleryDlAdapterRecord]) -> str:
    rows = [
        {
            "work_id_private": record.work_id,
            "media_id_private": record.local_media_id_private,
            "page_index": record.page_index,
            "tag_count": len(record.tags),
            "artist_present": bool(record.artist_name or record.artist_id),
            "local_match_status": record.local_match_status,
            "eligible_for_future_local_source_hint": record.eligible_for_future_local_source_hint,
            "eligible_for_future_entity_candidate": record.eligible_for_future_entity_candidate,
        }
        for record in records
    ]
    return _csv_from_dicts(
        rows,
        [
            "work_id_private",
            "media_id_private",
            "page_index",
            "tag_count",
            "artist_present",
            "local_match_status",
            "eligible_for_future_local_source_hint",
            "eligible_for_future_entity_candidate",
        ],
    )


def entity_candidates_preview_csv(rows: Sequence[f3.PixivCandidateRow]) -> str:
    entity_rows = [
        row.to_private_dict()
        for row in rows
        if row.candidate_kind in {"entity_candidate", "candidate_alias_group"}
    ]
    return _csv_from_dicts(
        entity_rows,
        [
            "raw_tag",
            "normalized_tag",
            "canonical_lookup_key",
            "candidate_kind",
            "candidate_namespace",
            "confidence",
            "reason",
            "lookup_source",
            "lookup_category",
            "provenance",
            "requires_manual_review",
            "db_write_allowed",
        ],
    )


def curated_mapping_template_csv(entries: Sequence[TaxonomyKBEntry]) -> str:
    unresolved = top_high_impact_entries(entries, limit=50)
    rows = [
        {
            "source_tag": entry.raw_tag_private,
            "canonical_key": entry.canonical_key,
            "candidate_namespace": "",
            "confidence": "",
            "status": "resolved",
            "target_tag": "",
            "relation_type": "",
            "notes": entry.unresolved_reason or "",
        }
        for entry in unresolved
    ]
    return _csv_from_dicts(
        rows,
        ["source_tag", "canonical_key", "candidate_namespace", "confidence", "status", "target_tag", "relation_type", "notes"],
    )


def build_manual_review_guide() -> str:
    return "\n".join(
        [
            "# Phase 4.4-P2R-F4 Pixiv taxonomy / alias KB 人工复核指南",
            "",
            "## 先看 resolved taxonomy mappings",
            "",
            "- 打开 `tag-classification.csv`，优先检查 `status=resolved` 或 `resolved_manual_override` 的行。",
            "- `candidate_namespace` 只能来自 local trusted DB evidence、external category lookup、parenthetical parser、curated mapping input，或明确的 deterministic descriptor 规则。",
            "- 不要把 `unresolved_governed` 当成已经分类；它只是有了原因和下一步动作。",
            "",
            "## 再看 alias candidates",
            "",
            "- 打开 `alias-candidates.csv`，按 `relation_type` 分组复核。",
            "- `parenthetical_character_of_work` 只说明 Pixiv tag 结构像 `character(work)`，不是 confirmed assignment。",
            "- `cooccurrence_candidate` 是弱证据，只能用于后续 curation 或 lookup prioritization。",
            "- `provider_canonical` 来自外部 provider canonical/alias 结构，但仍不是 EntityAlias truth table。",
            "",
            "## unresolved high-value tags",
            "",
            "- 打开 `unresolved-tags-analysis.csv`，按 `high_value_score` 和 `frequency` 排序。",
            "- `unresolved_reason` 和 `next_action` 用来决定下一步：补 curated mapping、引入新 taxonomy source、或改 parser。",
            "- 若没有用户提供 curated mapping 文件，不要自行填写 mapping，也不要凭常识把 tag 写死成角色/作品/画师。",
            "",
            "## 如何提供 curated mapping input",
            "",
            "- 使用 `curated-mapping-template.csv` 作为模板。",
            "- 必填：`source_tag`、`candidate_namespace`。可选：`canonical_key`、`confidence`、`target_tag`、`relation_type`、`notes`。",
            "- mapping 只会写入 taxonomy / alias KB，不会写 Entity、EntityAlias、EntityEvidence、MediaEntityCandidate 或 confirmed assignment。",
            "",
            "## 哪些行不能进入 EntityCandidate",
            "",
            "- `unresolved_governed` 行不能进入 EntityCandidate。",
            "- `candidate_namespace=ambiguous/unknown` 不能进入 EntityCandidate。",
            "- parenthetical、co-occurrence、translation normalization 只能作为候选证据，必须等待单独批准的 EntityCandidate persistence 阶段。",
            "",
        ]
    )


def _private_markers(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    samples: Sequence[f3.SelectedSample],
    entries: Sequence[TaxonomyKBEntry],
) -> list[str]:
    markers: set[str] = set()
    for sample in samples:
        markers.add(sample.work_id)
        markers.update(sample.local_basenames_private)
    for record in records:
        for value in (record.work_id, record.canonical_url, record.gallery_dl_filename):
            if value not in (None, ""):
                markers.add(str(value))
    for entry in entries:
        if entry.raw_tag_private:
            markers.add(entry.raw_tag_private)
    return sorted({marker for marker in markers if marker}, key=len, reverse=True)


def build_public_summary(
    *,
    generated_at: str,
    pr90_confirmation: Mapping[str, Any],
    pr90_summary: Mapping[str, Any],
    pr90_private_artifacts: Mapping[str, Any],
    git_context: Mapping[str, Any],
    db_identity: Mapping[str, Any] | None,
    sample_public: Mapping[str, Any],
    command_public: Mapping[str, Any],
    input_summary: Mapping[str, Any],
    lookup_summary: Mapping[str, Any],
    taxonomy_entries: Sequence[TaxonomyKBEntry],
    alias_entries: Sequence[AliasKBEntry],
    kb_write_summary: Mapping[str, Any],
    coverage: Mapping[str, Any],
    recommendation: Mapping[str, str],
    curated_mapping_count: int,
) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": generated_at,
        "artifact_lifecycle": "phase_scoped_operational_runner",
        "pr90_merge_confirmation": dict(pr90_confirmation),
        "git_context": dict(git_context),
        "why_this_stage_exists": (
            "PR #90 completed lookup/cache proof but did not reach classification coverage. "
            "F4 builds a taxonomy/alias KB and unresolved governance layer before any truth-table persistence."
        ),
        "db_identity": dict(db_identity or {"db_read": False}),
        "sample_selection": dict(sample_public),
        "command_summary": dict(command_public),
        "input_summary": dict(input_summary),
        "pr90_baseline": dict(PREVIOUS_PR90_BASELINE),
        "pr90_report_baseline": dict(pr90_summary),
        "pr90_private_artifacts": dict(pr90_private_artifacts),
        "strategy_layers_attempted": {
            "local_trusted_db_evidence": True,
            "pr90_lookup_cache": True,
            "danbooru_exact": f3.DANBOORU_TAGS_SOURCE in lookup_summary.get("lookup_sources_attempted", []),
            "danbooru_alias_canonical": f3.DANBOORU_ALIAS_SOURCE in lookup_summary.get("lookup_sources_attempted", []),
            "safebooru": f3.SAFEBOORU_TAGS_SOURCE in lookup_summary.get("lookup_sources_attempted", []),
            "gelbooru_or_unavailable_proof": GELBOORU_TAGS_SOURCE in lookup_summary.get("lookup_sources_attempted", []),
            "pixiv_parenthetical_pattern": True,
            "multilingual_normalization": True,
            "alias_cooccurrence_evidence": True,
            "curated_mapping_import_path": True,
            "unresolved_reason_bucketing": True,
        },
        "automated_tag_category_lookup": dict(lookup_summary),
        "taxonomy_kb": taxonomy_kb_summary(taxonomy_entries),
        "alias_kb": alias_kb_summary(alias_entries),
        "kb_write_summary": dict(kb_write_summary),
        "coverage_target": dict(coverage),
        "before_after_comparison": {
            "pr90": {
                "resolved_unique_tag_coverage": PREVIOUS_PR90_BASELINE["resolved_unique_tag_coverage"],
                "high_value_resolution_rate": PREVIOUS_PR90_BASELINE["high_value_proper_noun_like_resolution_rate"],
                "ambiguous_unknown_candidate_count": PREVIOUS_PR90_BASELINE["ambiguous_unknown_candidate_count"],
            },
            "f4": {
                "resolved_unique_tag_coverage": coverage.get("resolved_unique_tag_coverage"),
                "high_value_resolution_rate": coverage.get("high_value_proper_noun_like_resolution_rate"),
                "ambiguous_unknown_candidate_count": coverage.get("ambiguous_unknown_candidate_count_after_f4"),
                "top_high_impact_governance_rate": coverage.get("top_high_impact_governance_rate"),
            },
        },
        "unresolved_reason_buckets": taxonomy_kb_summary(taxonomy_entries)["unresolved_reason_buckets"],
        "top_unresolved_private_artifact": "private_unresolved_tags_analysis_csv",
        "curated_mapping": {
            "input_mapping_count": curated_mapping_count,
            "template_private_artifact": "private_curated_mapping_template_csv",
            "no_mapping_invented": curated_mapping_count == 0,
        },
        "recommendation": dict(recommendation),
        "public_report_redaction": {
            "contains_exact_pixiv_ids": False,
            "contains_exact_media_ids": False,
            "contains_exact_local_paths": False,
            "contains_raw_gallery_dl_json": False,
            "contains_raw_image_urls": False,
            "contains_raw_pixiv_tags": False,
            "exact_unresolved_tags_private_only": True,
        },
        "safety_confirmation": {
            "additive_db_migration": True,
            "db_write_limited_to_taxonomy_alias_external_tag_category_cache": True,
            "entity_write": False,
            "entity_alias_write": False,
            "entity_external_identity_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "media_entity_assignment_write": False,
            "local_source_hint_write": False,
            "provider_cache_write": False,
            "negative_lookup_cache_write": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "confirmed_assignment": False,
            "automatic_entity_creation": False,
            "entity_resolver": False,
            "llm_classification": False,
            "sample_specific_hardcoded_mapping": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "push_main": False,
            "merge": False,
        },
    }


def build_markdown_report(summary: Mapping[str, Any], *, private_markers: Iterable[str]) -> str:
    coverage = summary["coverage_target"]
    lines = [
        f"# {PHASE}: Pixiv taxonomy / alias knowledge base",
        "",
        "## Summary",
        "",
        f"- Target status: `{coverage['target_status']}`.",
        f"- Unique tag coverage: `{coverage['resolved_unique_tag_coverage']}`.",
        f"- High-value proper-noun resolution: `{coverage['high_value_proper_noun_like_resolution_rate']}`.",
        f"- Top high-impact governance: `{coverage['top_high_impact_governance_rate']}`.",
        f"- Recommendation: `{summary['recommendation']['persistence_recommendation']}`.",
        "",
        "## Scope",
        "",
        "- Builds taxonomy / alias KB rows only.",
        "- Does not write Entity, EntityAlias, EntityEvidence, MediaEntityCandidate, ProviderCache, NegativeLookupCache, media_tags, TagTranslation, LocalSourceHint, or confirmed assignments.",
        "",
        "## PR #90 Baseline",
        "",
        f"- PR #90: `{json.dumps(summary['pr90_merge_confirmation'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Baseline: `{json.dumps(summary['pr90_baseline'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Input / Strategy",
        "",
        f"- Input summary: `{json.dumps(summary['input_summary'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Strategy layers attempted: `{json.dumps(summary['strategy_layers_attempted'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Lookup summary: `{json.dumps(summary['automated_tag_category_lookup'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Knowledge Base",
        "",
        f"- Taxonomy KB: `{json.dumps(summary['taxonomy_kb'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Alias KB: `{json.dumps(summary['alias_kb'], ensure_ascii=False, sort_keys=True)}`.",
        f"- KB writes: `{json.dumps(summary['kb_write_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Coverage",
        "",
        f"- Coverage target: `{json.dumps(summary['coverage_target'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Before/after: `{json.dumps(summary['before_after_comparison'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Unresolved reason buckets: `{json.dumps(summary['unresolved_reason_buckets'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Exact top unresolved tags are private-only: `{summary['top_unresolved_private_artifact']}`.",
        "",
        "## Curated Mapping",
        "",
        f"- Curated mapping: `{json.dumps(summary['curated_mapping'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Recommendation",
        "",
        f"- Recommendation: `{json.dumps(summary['recommendation'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Safety Confirmation",
        "",
        f"- Safety: `{json.dumps(summary['safety_confirmation'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Redaction: `{json.dumps(summary['public_report_redaction'], ensure_ascii=False, sort_keys=True)}`.",
        "",
    ]
    report = "\n".join(lines)
    f1.assert_public_payload_safe(report, private_markers=private_markers)
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--max-work-ids", type=int, default=MAX_WORK_IDS_WITHOUT_RENEWED_APPROVAL)
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS_WITHOUT_RENEWED_APPROVAL)
    parser.add_argument("--max-unique-tags", type=int, default=MAX_UNIQUE_TAGS_WITHOUT_RENEWED_APPROVAL)
    parser.add_argument("--max-external-requests", type=int, default=DEFAULT_MAX_EXTERNAL_REQUESTS)
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--skip-external-lookup-network", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--curated-mapping-input", default="")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--reuse-raw-metadata", action="store_true")
    parser.add_argument("--output-dir", default=str(PHASE_OUTPUT_DIR))
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
    parser.add_argument("--lookup-delay-seconds", type=float, default=DEFAULT_LOOKUP_DELAY_SECONDS)
    parser.add_argument("--lookup-timeout", type=int, default=DEFAULT_LOOKUP_TIMEOUT_SECONDS)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--media-summary-csv", default=str(PRIVATE_MEDIA_SUMMARY_CSV))
    parser.add_argument("--tag-classification-csv", default=str(PRIVATE_TAG_CLASSIFICATION_CSV))
    parser.add_argument("--entity-candidates-preview-csv", default=str(PRIVATE_ENTITY_CANDIDATES_PREVIEW_CSV))
    parser.add_argument("--alias-candidates-csv", default=str(PRIVATE_ALIAS_CANDIDATES_CSV))
    parser.add_argument("--unresolved-tags-csv", default=str(PRIVATE_UNRESOLVED_TAGS_CSV))
    parser.add_argument("--kb-cache-sheet-csv", default=str(PRIVATE_KB_CACHE_SHEET_CSV))
    parser.add_argument("--curated-mapping-template-csv", default=str(PRIVATE_CURATED_TEMPLATE_CSV))
    parser.add_argument("--manual-review-guide", default=str(PRIVATE_MANUAL_REVIEW_GUIDE))
    parser.add_argument("--raw-dir", default=str(PRIVATE_RAW_DIR))
    parser.add_argument("--pr-number", type=int, default=0)
    parser.add_argument("--pr-head-sha", default="")
    parser.add_argument("--pr90-state", default="MERGED")
    parser.add_argument("--pr90-merged-at", default="2026-06-02T10:50:31Z")
    parser.add_argument("--pr90-merge-commit", default="e9d49ea921f5a98ed63e496647b89d0f322de522")
    parser.add_argument("--pr90-url", default="https://github.com/kyloris0660/VIOLET/pull/90")
    return parser


def entrypoint_for_args(args: argparse.Namespace) -> f3.GalleryDlEntrypoint:
    if args.dry_run or args.skip_network:
        command = f2.split_operator_command(args.gallery_dl_command or "") if args.gallery_dl_command else ()
        return f3.GalleryDlEntrypoint(
            mode="dry_run_no_gallery_dl_probe",
            command=command,
            version=None,
            available=False,
            reproducibility_status="dry_run_no_gallery_dl_probe",
        )
    return f3.probe_gallery_dl_entrypoint(args.gallery_dl_command or None)


def migration_and_writes_allowed(args: argparse.Namespace) -> bool:
    return not (args.no_db or args.dry_run or args.skip_network)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = resolve_repo_path(args.output_dir)
    report_json = resolve_repo_path(args.report_json)
    report_md = resolve_repo_path(args.report_md)
    raw_dir = resolve_repo_path(args.raw_dir)
    details_json = resolve_repo_path(args.details_json)
    media_summary_path = resolve_repo_path(args.media_summary_csv)
    tag_classification_path = resolve_repo_path(args.tag_classification_csv)
    entity_preview_path = resolve_repo_path(args.entity_candidates_preview_csv)
    alias_candidates_path = resolve_repo_path(args.alias_candidates_csv)
    unresolved_path = resolve_repo_path(args.unresolved_tags_csv)
    kb_cache_path = resolve_repo_path(args.kb_cache_sheet_csv)
    curated_template_path = resolve_repo_path(args.curated_mapping_template_csv)
    manual_review_guide_path = resolve_repo_path(args.manual_review_guide)
    private_paths = [
        raw_dir,
        details_json,
        media_summary_path,
        tag_classification_path,
        entity_preview_path,
        alias_candidates_path,
        unresolved_path,
        kb_cache_path,
        curated_template_path,
        manual_review_guide_path,
    ]
    validate_all_output_paths_before_effects(
        output_dir,
        private_paths=private_paths,
        report_json=report_json,
        report_md=report_md,
    )
    enforce_caps(
        sample_size=args.sample_size,
        max_work_ids=args.max_work_ids,
        max_records=args.max_records,
        max_unique_tags=args.max_unique_tags,
        max_external_requests=args.max_external_requests,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = f2.load_project_config(ROOT)
    curated_mappings = load_curated_mappings(args.curated_mapping_input or None)
    pr90_summary = load_pr90_summary()
    pr90_private_artifacts = inspect_pr90_private_artifacts()
    db_identity: dict[str, Any] | None = None
    prior_index: f1.LocalPriorIndex | None = None
    local_index = f3.LocalClassificationIndex()
    samples: list[f3.SelectedSample] = []
    sample_public: dict[str, Any] = {
        "selected_count": 0,
        "requested_sample_size": args.sample_size,
        "sample_gate_status": "db_skipped",
        "exact_work_ids_public": False,
        "exact_media_ids_public": False,
        "exact_filenames_public": False,
    }
    db_cache_table_available = False
    kb_tables_available = False
    cache_migration_ran = False
    kb_migration_ran = False

    if not args.no_db:
        engine = create_engine(config.database_url)
        SessionLocal = sessionmaker(bind=engine)
        session: Session = SessionLocal()
        try:
            db_identity = f1.prove_db_identity(session, config)
            prior_index = f1.build_local_prior_index(session)
            local_index = f3.build_local_classification_index(session)
            sample_public, samples = f3.select_local_pixiv_prior_samples(
                prior_index,
                sample_size=args.sample_size,
            )
        finally:
            session.close()
        if migration_and_writes_allowed(args):
            migrate_add_pixiv_tag_taxonomy_alias_kb(engine, inspect(engine))
            kb_migration_ran = True
        inspector = inspect(engine)
        db_cache_table_available = "blombooru_external_tag_category_lookup_cache" in inspector.get_table_names()
        kb_tables_available = {
            "blombooru_pixiv_tag_taxonomy_kb",
            "blombooru_pixiv_tag_alias_kb",
        }.issubset(set(inspector.get_table_names()))
        engine.dispose()

    entrypoint = entrypoint_for_args(args)
    metadata_results: list[f3.CommandResult] = []
    parse_result = f3._empty_parse_result(raw_dir)
    records: list[f2.PixivGalleryDlAdapterRecord] = []
    join_summary: dict[str, Any] = {
        "status_counts": {},
        "page_index_status_counts": {},
        "match_content_class_counts": {},
        "future_eligibility_counts": {},
        "local_prior_join_ran": prior_index is not None,
        "local_prior_total_keys": prior_index.total_prior_keys if prior_index else 0,
        "local_prior_total_media": prior_index.total_prior_media if prior_index else 0,
        "local_prior_total_media_inspected": prior_index.total_media_inspected if prior_index else 0,
        "local_prior_content_class_distribution": prior_index.content_class_distribution if prior_index else {},
    }
    if not (args.dry_run or args.skip_network):
        if args.reuse_raw_metadata:
            metadata_results, parse_result = load_existing_raw_metadata(raw_dir, max_records=args.max_records)
        else:
            metadata_results, parse_result = run_metadata_commands(
                samples,
                entrypoint,
                raw_dir,
                max_records=args.max_records,
                timeout=args.timeout,
            )
        if any(result.blocked_over_limit for result in metadata_results):
            raise SampleGateError("sample_or_record_cap_exceeded")
        records = f2.normalize_adapter_records(parse_result, entrypoint=entrypoint)
        enforce_record_count(len(records), args.max_records)
        records, join_summary = f2.join_records_to_local_priors(records, prior_index)
        records = f2._finalize_joined_records(
            records,
            reference_download_enabled=False,
            downloaded_file_count=0,
        )

    lookup_results: dict[str, f3.ExternalTagLookupResult] = {}
    lookup_summary = f3.ExternalLookupSummary(
        lookup_source="f4_taxonomy_alias_multisource_lookup_v1",
        lookup_sources_attempted=list(LOOKUP_SOURCES),
        external_request_budget=args.max_external_requests,
        lookup_limit=args.max_unique_tags,
        unique_normalized_tag_count=len(f3.build_tag_lookup_inputs(records)),
        lookup_delay_seconds=args.lookup_delay_seconds,
        lookup_timeout_seconds=args.lookup_timeout,
        cache_write_enabled=bool(
            records
            and not args.no_db
            and not (args.dry_run or args.skip_network)
            and db_cache_table_available
        ),
    )
    if records and not (args.dry_run or args.skip_network):
        if args.no_db:
            if args.skip_external_lookup_network:
                lookup_results, lookup_summary = lookup_external_tag_categories_cache_only(
                    records,
                    session=None,
                    lookup_limit=args.max_unique_tags,
                    skip_reason="external_lookup_network_skipped_by_flag",
                )
            else:
                lookup_results, lookup_summary = lookup_external_tag_categories_f4(
                    records,
                    session=None,
                    lookup_limit=args.max_unique_tags,
                    max_external_requests=args.max_external_requests,
                    delay_seconds=args.lookup_delay_seconds,
                    timeout_seconds=args.lookup_timeout,
                    cache_writes_enabled=False,
                )
        else:
            engine = create_engine(config.database_url)
            install_taxonomy_alias_kb_write_guard(engine)
            SessionLocal = sessionmaker(bind=engine)
            session = SessionLocal()
            try:
                if args.skip_external_lookup_network:
                    lookup_results, lookup_summary = lookup_external_tag_categories_cache_only(
                        records,
                        session=session,
                        lookup_limit=args.max_unique_tags,
                        skip_reason="external_lookup_network_skipped_after_bounded_live_timeout",
                    )
                else:
                    lookup_results, lookup_summary = lookup_external_tag_categories_f4(
                        records,
                        session=session,
                        lookup_limit=args.max_unique_tags,
                        max_external_requests=args.max_external_requests,
                        delay_seconds=args.lookup_delay_seconds,
                        timeout_seconds=args.lookup_timeout,
                        cache_writes_enabled=db_cache_table_available,
                    )
            finally:
                session.close()
                engine.dispose()

    normalized_records, candidate_rows = f3.normalize_metadata_candidates(
        records,
        local_index,
        external_lookup_results=lookup_results,
    )
    taxonomy_entries = build_taxonomy_entries(records, candidate_rows, lookup_results, curated_mappings)
    alias_entries = build_alias_entries(records, candidate_rows, lookup_results, curated_mappings)
    kb_write_summary = KbWriteSummary(external_cache_write_count=int(lookup_summary.cache_write_count))
    if records and migration_and_writes_allowed(args) and kb_tables_available:
        engine = create_engine(config.database_url)
        install_taxonomy_alias_kb_write_guard(engine)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            kb_write_summary = write_kb_entries(
                session,
                taxonomy_entries,
                alias_entries,
                external_cache_write_count=int(lookup_summary.cache_write_count),
            )
        finally:
            session.close()
            engine.dispose()

    lookup_summary_public = lookup_summary.public_dict()
    successful_raw_files = [
        result.stdout_path_private
        for result in metadata_results
        if result.success and result.stdout_path_private
    ]
    raw_scope = f3.current_run_raw_scope_summary(raw_dir, successful_raw_files)
    input_summary = {
        "sample_size_requested": args.sample_size,
        "max_work_ids": args.max_work_ids,
        "max_records": args.max_records,
        "max_unique_tags": args.max_unique_tags,
        "max_external_requests": args.max_external_requests,
        "reuse_raw_metadata": bool(args.reuse_raw_metadata),
        "external_lookup_network_skipped": bool(args.skip_external_lookup_network),
        "metadata_command_count": len(metadata_results),
        "metadata_success_count": sum(1 for result in metadata_results if result.success),
        "raw_record_count": len(parse_result.records),
        "normalized_media_record_count": len(records),
        "normalized_candidate_media_record_count": len(normalized_records),
        "unique_normalized_tag_count": len(taxonomy_entries),
        "raw_scope": raw_scope,
        "local_source_prior_join": join_summary,
    }
    coverage = coverage_target_summary(taxonomy_entries, candidate_rows)
    recommendation = recommendation_from_coverage(coverage)
    generated_at = _now_iso()
    pr90_confirmation = {
        "number": 90,
        "state": args.pr90_state,
        "merged_at": args.pr90_merged_at,
        "merge_commit": args.pr90_merge_commit,
        "url": args.pr90_url,
    }
    summary = build_public_summary(
        generated_at=generated_at,
        pr90_confirmation=pr90_confirmation,
        pr90_summary=pr90_summary,
        pr90_private_artifacts=pr90_private_artifacts,
        git_context=f3._git_context(pr_number=args.pr_number or None, pr_head_sha=args.pr_head_sha or None),
        db_identity=db_identity,
        sample_public=sample_public,
        command_public=f3.command_summary(metadata_results, max_record_limit=args.max_records),
        input_summary=input_summary,
        lookup_summary=lookup_summary_public,
        taxonomy_entries=taxonomy_entries,
        alias_entries=alias_entries,
        kb_write_summary={
            **kb_write_summary.public_dict(),
            "kb_migration_ran": kb_migration_ran,
            "external_cache_table_available": db_cache_table_available,
            "taxonomy_alias_tables_available": kb_tables_available,
            "cache_migration_ran": cache_migration_ran,
        },
        coverage=coverage,
        recommendation=recommendation,
        curated_mapping_count=len(curated_mappings),
    )
    markers = _private_markers(records, samples, taxonomy_entries)
    f1.assert_public_payload_safe(summary, private_markers=markers)
    report = build_markdown_report(summary, private_markers=markers)
    write_public_json(report_json, summary)
    write_public_text(report_md, report, private_markers=markers)

    write_private_json(
        details_json,
        {
            "generated_at": generated_at,
            "private_exact_mappings": True,
            "public_report_contains_exact_mappings": False,
            "selected_samples_private": [asdict(sample) for sample in samples],
            "metadata_command_results_private": [asdict(result) for result in metadata_results],
            "records_private": [record.to_private_dict() for record in records],
            "normalized_records_private": [record.to_private_dict() for record in normalized_records],
            "candidate_rows_private": [row.to_private_dict() for row in candidate_rows],
            "taxonomy_entries_private": [entry.to_private_dict() for entry in taxonomy_entries],
            "alias_entries_private": [entry.to_private_dict() for entry in alias_entries],
            "lookup_results_private": [result.to_private_dict() for result in lookup_results.values()],
            "summary_public": summary,
        },
    )
    write_private_text(media_summary_path, media_summary_csv(records))
    write_private_text(tag_classification_path, taxonomy_csv(taxonomy_entries))
    write_private_text(entity_preview_path, entity_candidates_preview_csv(candidate_rows))
    write_private_text(alias_candidates_path, alias_csv(alias_entries))
    write_private_text(unresolved_path, unresolved_csv(taxonomy_entries))
    write_private_text(kb_cache_path, taxonomy_csv(taxonomy_entries))
    write_private_text(curated_template_path, curated_mapping_template_csv(taxonomy_entries))
    write_private_text(manual_review_guide_path, build_manual_review_guide())

    return {
        "summary": summary,
        "report_md": _rel(report_md),
        "report_json": _rel(report_json),
        "details_json": _rel(details_json),
        "target_status": coverage["target_status"],
        "target_reached": coverage["target_reached"],
        "taxonomy_entry_count": len(taxonomy_entries),
        "alias_entry_count": len(alias_entries),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run(args)
    print(json.dumps(_coerce_json_safe(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
