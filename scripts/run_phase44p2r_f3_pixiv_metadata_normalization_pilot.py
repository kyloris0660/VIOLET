"""Phase 4.4-P2R-F3 Pixiv metadata normalization pilot.

Lifecycle: phase-scoped operational runner. It invokes a user-installed
gallery-dl boundary for a bounded Pixiv filename-prior sample, converts raw
Pixiv metadata into tag/entity candidate middleware rows, writes public-safe
reports plus ignored private artifacts, and writes only the approved external
tag category lookup cache table when DB cache writes are enabled.
"""

from __future__ import annotations

import argparse
import csv
import io
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
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.enums import (  # noqa: E402
    EntityExternalIdentityStatusEnum,
    EntityMetadataSourceEnum,
    EntityStatusEnum,
    EntityTypeEnum,
    TagCategoryEnum,
)
from app.database import migrate_add_external_tag_category_lookup_cache  # noqa: E402
from app.models import (  # noqa: E402
    Entity,
    EntityAlias,
    EntityExternalIdentity,
    ExternalTagCategoryLookupCache,
    Tag,
    TagAlias,
)
from scripts import run_phase44p2r_f1_gallery_dl_json_import_pilot as f1  # noqa: E402
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as f2  # noqa: E402

PHASE = "4.4-P2R-F3b"
TITLE = "Automated Pixiv Tag Category Lookup and Classification Cache Pilot"
PHASE_SLUG = "phase-4.4p2r-f3-pixiv-metadata-normalization-pilot"

REPORT_MD = Path(f"docs/reports/{PHASE_SLUG}.md")
REPORT_JSON = Path(f"docs/reports/{PHASE_SLUG}-summary.json")
PHASE_OUTPUT_DIR = Path(".local_manifests") / PHASE_SLUG
PRIVATE_DETAILS_JSON = PHASE_OUTPUT_DIR / "details.json"
PRIVATE_MEDIA_SUMMARY_CSV = PHASE_OUTPUT_DIR / "media-summary.csv"
PRIVATE_MEDIA_SUMMARY_MD = PHASE_OUTPUT_DIR / "media-summary.md"
PRIVATE_TAG_CANDIDATES_CSV = PHASE_OUTPUT_DIR / "tag-candidates.csv"
PRIVATE_TAG_CANDIDATES_MD = PHASE_OUTPUT_DIR / "tag-candidates.md"
PRIVATE_ENTITY_CANDIDATES_CSV = PHASE_OUTPUT_DIR / "entity-candidates.csv"
PRIVATE_ENTITY_CANDIDATES_MD = PHASE_OUTPUT_DIR / "entity-candidates.md"
PRIVATE_LOOKUP_CACHE_CSV = PHASE_OUTPUT_DIR / "lookup-cache-sheet.csv"
PRIVATE_LOOKUP_CACHE_MD = PHASE_OUTPUT_DIR / "lookup-cache-sheet.md"
PRIVATE_UNRESOLVED_TAGS_CSV = PHASE_OUTPUT_DIR / "unresolved-tags-analysis.csv"
PRIVATE_UNRESOLVED_TAGS_MD = PHASE_OUTPUT_DIR / "unresolved-tags-analysis.md"
PRIVATE_PARENTHETICAL_CANDIDATES_CSV = PHASE_OUTPUT_DIR / "parenthetical-pattern-candidates.csv"
PRIVATE_PARENTHETICAL_CANDIDATES_MD = PHASE_OUTPUT_DIR / "parenthetical-pattern-candidates.md"
PRIVATE_MANUAL_REVIEW_GUIDE = PHASE_OUTPUT_DIR / "manual-review-guide.md"
PRIVATE_RAW_DIR = PHASE_OUTPUT_DIR / "raw"

DEFAULT_SAMPLE_SIZE = 30
MAX_SAMPLE_SIZE = 50
DEFAULT_MAX_RECORDS = 200
MAX_RECORDS_WITHOUT_RENEWED_APPROVAL = 200
DEFAULT_TAG_LOOKUP_LIMIT = 200
MAX_TAG_LOOKUP_LIMIT_WITHOUT_RENEWED_APPROVAL = 500
DEFAULT_TAG_LOOKUP_DELAY_SECONDS = 0.25
DEFAULT_TAG_LOOKUP_TIMEOUT_SECONDS = 20
GALLERY_DL_METADATA_COMMAND_TEMPLATE = (
    "<gallery_dl_entrypoint> --dump-json --no-download https://www.pixiv.net/artworks/<WORK_ID>"
)
EXTERNAL_TAG_LOOKUP_SOURCE = "danbooru_tags_and_aliases_api_v1"
EXTERNAL_TAG_LOOKUP_BASE_URL = "https://danbooru.donmai.us/tags.json"
EXTERNAL_TAG_ALIAS_LOOKUP_BASE_URL = "https://danbooru.donmai.us/tag_aliases.json"
EXTERNAL_TAG_LOOKUP_DOC_URL = "https://danbooru.donmai.us/wiki_pages/help%3Aapi"
EXTERNAL_TAG_LOOKUP_USER_AGENT = "VIOLET-Phase44P2R-F3b/1.0 (bounded tag category lookup pilot)"
DANBOORU_TAGS_SOURCE = "danbooru_tags_api_v2"
DANBOORU_ALIAS_SOURCE = "danbooru_tag_aliases_api_v2"
SAFEBOORU_TAGS_SOURCE = "safebooru_tags_xml_api_v1"
SAFEBOORU_TAG_LOOKUP_BASE_URL = "https://safebooru.org/index.php"
SUCCESSFUL_LOOKUP_STATUSES = frozenset({"hit"})
NEGATIVE_LOOKUP_CACHE_TTL_SECONDS = 24 * 60 * 60
LOOKUP_ERROR_RETRY_AFTER_SECONDS = 60 * 60
DEFAULT_LOOKUP_SOURCES = (DANBOORU_TAGS_SOURCE, DANBOORU_ALIAS_SOURCE, SAFEBOORU_TAGS_SOURCE)
PREVIOUS_F3_BASELINE = {
    "ambiguous_unknown_candidate_count": 305,
    "copyright_series_candidate_count": 0,
    "character_candidate_count": 0,
}
PREVIOUS_DANBOORU_ONLY_F3B = {
    "ambiguous_unknown_candidate_count": 262,
    "copyright_series_candidate_count": 9,
    "character_candidate_count": 1,
    "unique_tag_lookup_hit_count": 17,
    "unique_tag_lookup_total_count": 133,
}

GENERAL_DESCRIPTOR_TERMS = frozenset(
    {
        "1girl",
        "1boy",
        "2girls",
        "solo",
        "girl",
        "girls",
        "boy",
        "boys",
        "woman",
        "women",
        "man",
        "men",
        "illustration",
        "illust",
        "digital_art",
        "fanart",
        "fan_art",
        "anime",
        "manga",
        "cute",
        "kawaii",
        "portrait",
        "landscape",
        "background",
        "comic",
        "sketch",
        "drawing",
        "blue_hair",
        "long_hair",
        "short_hair",
        "smile",
        "制服",
        "女の子",
        "男の子",
        "少女",
        "少年",
        "イラスト",
        "漫画",
    }
)
SENSITIVE_OR_META_TERMS = frozenset(
    {
        "r-18",
        "r18",
        "r-18g",
        "r18g",
        "nsfw",
        "adult",
        "explicit",
        "questionable",
        "sensitive",
        "guro",
        "18禁",
        "成人向け",
        "センシティブ",
        "グロ",
    }
)
ORIGINAL_WORK_TERMS = frozenset({"original", "oc", "創作", "オリジナル", "原创", "原創"})
ENTITY_NAMESPACES = frozenset({"artist", "character", "copyright"})
LOOKUP_SOURCE_EXTERNAL_DISABLED = "external_category_lookup_disabled_in_f3"
PIXIV_IDENTITY_PROVIDERS = frozenset(
    {
        "pixiv",
        "pixiv_user",
        "pixiv_user_id",
        "pixiv_artist",
        "pixiv_artist_id",
    }
)
FORBIDDEN_TRUTH_TABLES = frozenset(
    {
        "blombooru_entities",
        "blombooru_entity_aliases",
        "blombooru_entity_external_identities",
        "blombooru_entity_evidence",
        "blombooru_media_entity_candidates",
        "blombooru_media_entity_assignments",
        "blombooru_media_tags",
        "blombooru_provider_cache",
        "blombooru_negative_lookup_cache",
        "blombooru_tag_translations",
    }
)

GalleryDlEntrypoint = f2.GalleryDlEntrypoint
CommandResult = f2.CommandResult
SelectedSample = f2.SelectedSample
CompletedRunner = Callable[..., subprocess.CompletedProcess[str]]


class Phase44P2RF3Error(RuntimeError):
    pass


class SampleGateError(Phase44P2RF3Error):
    pass


class GalleryDlAuthBlocked(Phase44P2RF3Error):
    pass


class GalleryDlUnavailable(Phase44P2RF3Error):
    pass


class OutputPathError(Phase44P2RF3Error):
    pass


class PrivacyBlocked(Phase44P2RF3Error):
    pass


MANUAL_OVERRIDE_VALUE_UNSET = object()


@dataclass(frozen=True)
class LocalTagCategoryMatch:
    tag_id_private: int
    tag_name_private: str
    category: str
    match_kind: str


@dataclass(frozen=True)
class LocalEntityMatch:
    entity_id_private: int
    canonical_name_private: str
    entity_type: str
    match_kind: str
    alias_id_private: int | None = None
    external_identity_id_private: int | None = None
    source: str | None = None
    confidence: float = 0.9


@dataclass(frozen=True)
class ExternalTagLookupResult:
    raw_tag: str
    normalized_tag: str
    canonical_lookup_key: str
    lookup_source: str
    lookup_source_version: str | None
    source_tag_id: str | None
    source_tag_name: str | None
    source_category_raw: str | None
    mapped_candidate_namespace: str | None
    confidence: float | None
    provenance_url_or_key: str | None
    status: str
    cache_status: str
    lookup_error: str | None = None
    retry_after: str | None = None
    expires_at: str | None = None

    def to_cache_fields(
        self,
        *,
        manual_override_status: str | None = None,
        manual_override_value: Any = MANUAL_OVERRIDE_VALUE_UNSET,
    ) -> dict[str, Any]:
        return {
            "raw_tag": self.raw_tag,
            "normalized_tag": self.normalized_tag,
            "canonical_lookup_key": self.canonical_lookup_key,
            "lookup_source": self.lookup_source,
            "lookup_source_version": self.lookup_source_version,
            "source_tag_id": self.source_tag_id,
            "source_tag_name": self.source_tag_name,
            "source_category_raw": self.source_category_raw,
            "mapped_candidate_namespace": self.mapped_candidate_namespace,
            "confidence": self.confidence,
            "provenance_url_or_key": self.provenance_url_or_key,
            "status": self.status,
            "last_checked_at": datetime.now(timezone.utc),
            "retry_after": _parse_iso_datetime(self.retry_after),
            "expires_at": _parse_iso_datetime(self.expires_at),
            "lookup_error": self.lookup_error,
            **(
                {"manual_override_status": manual_override_status}
                if manual_override_status is not None
                else {}
            ),
            **(
                {"manual_override_value": manual_override_value}
                if manual_override_value is not MANUAL_OVERRIDE_VALUE_UNSET
                else {}
            ),
        }

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultilingualTagNormalization:
    raw_tag: str
    unicode_nfkc_normalized: str
    whitespace_normalized: str
    casefolded_latin_form: str | None
    underscore_form: str
    punctuation_normalized_form: str
    bracket_normalized_form: str
    lookup_candidates: tuple[str, ...]
    canonical_lookup_key: str

    def public_safe_dict(self) -> dict[str, Any]:
        return {
            "lookup_candidate_count": len(self.lookup_candidates),
            "has_casefolded_latin_form": self.casefolded_latin_form is not None,
            "canonical_lookup_key_present": bool(self.canonical_lookup_key),
        }

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParentheticalTagPattern:
    raw_tag: str
    outer_name: str
    inner_work_or_context: str
    bracket_style: str
    ambiguous_or_nested: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TagLookupInput:
    raw_tag: str
    normalized_tag: str
    canonical_lookup_key: str
    lookup_candidates: tuple[str, ...]


@dataclass
class ExternalLookupSummary:
    lookup_source: str = "multisource_tag_category_lookup_v1"
    lookup_sources_attempted: list[str] = field(default_factory=list)
    external_request_budget: int = 0
    request_budget_exhausted: bool = False
    source_request_counts: dict[str, int] = field(default_factory=dict)
    source_cache_hit_counts: dict[str, int] = field(default_factory=dict)
    source_negative_cache_hit_counts: dict[str, int] = field(default_factory=dict)
    source_cache_miss_counts: dict[str, int] = field(default_factory=dict)
    source_cache_write_counts: dict[str, int] = field(default_factory=dict)
    source_hit_counts: dict[str, int] = field(default_factory=dict)
    source_not_found_counts: dict[str, int] = field(default_factory=dict)
    source_lookup_error_counts: dict[str, int] = field(default_factory=dict)
    enabled: bool = True
    unique_normalized_tag_count: int = 0
    lookup_limit: int = DEFAULT_TAG_LOOKUP_LIMIT
    hard_lookup_limit: int = MAX_TAG_LOOKUP_LIMIT_WITHOUT_RENEWED_APPROVAL
    lookup_delay_seconds: float = DEFAULT_TAG_LOOKUP_DELAY_SECONDS
    lookup_timeout_seconds: int = DEFAULT_TAG_LOOKUP_TIMEOUT_SECONDS
    request_count: int = 0
    cache_hit_count: int = 0
    negative_cache_hit_count: int = 0
    cache_miss_count: int = 0
    cache_write_count: int = 0
    cache_write_enabled: bool = True
    provider_blocked: bool = False
    provider_block_reason: str | None = None
    hit_count: int = 0
    not_found_count: int = 0
    lookup_error_count: int = 0
    resolved_namespace_counts: dict[str, int] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalClassificationIndex:
    tag_matches: dict[str, LocalTagCategoryMatch] = field(default_factory=dict)
    entity_matches: dict[str, LocalEntityMatch] = field(default_factory=dict)
    provider_entity_matches: dict[tuple[str, str], LocalEntityMatch] = field(default_factory=dict)
    total_tags_indexed: int = 0
    total_tag_aliases_indexed: int = 0
    total_entities_indexed: int = 0
    total_entity_aliases_indexed: int = 0
    total_external_identities_indexed: int = 0
    total_provider_scoped_external_identities_indexed: int = 0

    def public_summary(self) -> dict[str, Any]:
        return {
            "local_tag_categories_indexed": self.total_tags_indexed,
            "local_tag_aliases_indexed": self.total_tag_aliases_indexed,
            "local_entities_indexed": self.total_entities_indexed,
            "local_entity_aliases_indexed": self.total_entity_aliases_indexed,
            "local_external_identities_indexed": self.total_external_identities_indexed,
            "local_provider_scoped_external_identities_indexed": self.total_provider_scoped_external_identities_indexed,
            "external_identities_match_raw_pixiv_tags": False,
            "external_identity_lookup_requires_provider_namespace": True,
            "db_read_only": True,
            "db_write_allowed": False,
        }


@dataclass
class PixivCandidateRow:
    media_id_private: int | None
    work_id_private: str | None
    page_index: int | None
    title_private: str | None
    artist_name_private: str | None
    artist_id_private: str | None
    raw_tag: str
    normalized_tag: str
    candidate_kind: str
    candidate_namespace: str
    confidence: float
    reason: str
    unicode_nfkc_normalized: str | None = None
    whitespace_normalized: str | None = None
    casefolded_latin_form: str | None = None
    underscore_form: str | None = None
    punctuation_normalized_form: str | None = None
    bracket_normalized_form: str | None = None
    lookup_candidates: tuple[str, ...] = ()
    canonical_lookup_key: str | None = None
    lookup_source: str | None = None
    lookup_key: str | None = None
    lookup_category: str | None = None
    provenance: str | None = None
    cache_status: str | None = None
    existing_entity_match: bool = False
    existing_alias_match: bool = False
    existing_entity_id_private: int | None = None
    existing_alias_id_private: int | None = None
    future_local_source_hint_eligible: bool = False
    future_entity_candidate_eligible: bool = False
    future_tag_suggestion_eligible: bool = False
    requires_manual_review: bool = True
    db_write_allowed: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PixivNormalizedMetadataCandidate:
    media_id_private: int | None
    work_id_private: str | None
    page_index: int | None
    page_count: int | None
    title_private: str | None
    artist_name_private: str | None
    artist_id_private: str | None
    raw_pixiv_tags: tuple[str, ...]
    normalized_unicode_tags: tuple[str, ...]
    multilingual_normalizations: list[dict[str, Any]]
    parenthetical_patterns: list[dict[str, Any]]
    tag_candidates: list[dict[str, Any]]
    entity_candidates: list[dict[str, Any]]
    unresolved_or_ambiguous_tags: tuple[str, ...]
    original_work_status: str
    eligibility: dict[str, Any]
    confidence: float
    reasons: tuple[str, ...]
    db_write_allowed: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _future_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _not_found_cache_is_fresh(row: ExternalTagCategoryLookupCache, *, source: str) -> bool:
    if str(row.status) != "not_found":
        return False
    if row.lookup_source_version and str(row.lookup_source_version) != source:
        return False
    expires_at = row.expires_at
    if not expires_at and row.last_checked_at:
        checked_at = row.last_checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        expires_at = checked_at + timedelta(seconds=NEGATIVE_LOOKUP_CACHE_TTL_SECONDS)
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _with_default_cache_lifecycle(result: ExternalTagLookupResult) -> ExternalTagLookupResult:
    if result.status == "not_found" and not result.expires_at:
        return replace(result, expires_at=_future_iso(NEGATIVE_LOOKUP_CACHE_TTL_SECONDS))
    if result.status == "lookup_error" and not result.retry_after:
        return replace(result, retry_after=_future_iso(LOOKUP_ERROR_RETRY_AFTER_SECONDS))
    return result


def _rel(path: Path) -> str:
    return f1._rel(path)


def resolve_repo_path(path: str | Path) -> Path:
    return f1.resolve_repo_path(path)


def _coerce_json_safe(value: Any) -> Any:
    return f1._coerce_json_safe(value)


def require_under_phase_output(path: Path) -> None:
    resolved = resolve_repo_path(path)
    try:
        f1.require_under_path(resolved, ROOT / PHASE_OUTPUT_DIR, code="f3_output_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc


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


def enforce_sample_size(sample_size: int) -> None:
    if sample_size < 1:
        raise SampleGateError("sample_or_record_cap_exceeded:sample_size_must_be_positive")
    if sample_size > MAX_SAMPLE_SIZE:
        raise SampleGateError("sample_or_record_cap_exceeded:sample_size_exceeds_max_50")


def enforce_record_count(record_count: int, max_records: int) -> None:
    if max_records < 1:
        raise SampleGateError("sample_or_record_cap_exceeded:max_records_must_be_positive")
    if max_records > MAX_RECORDS_WITHOUT_RENEWED_APPROVAL:
        raise SampleGateError("sample_or_record_cap_exceeded:max_records_exceeds_max_200")
    if record_count > max_records:
        raise SampleGateError("sample_or_record_cap_exceeded:generated_output_exceeds_max_records")


def redact_text(text: str, *, private_markers: Iterable[str] = ()) -> str:
    return f2.redact_text(text, private_markers=private_markers)


def probe_gallery_dl_entrypoint(
    explicit_command: str | None = None,
    *,
    runner: CompletedRunner = subprocess.run,
    python_executable: str | None = None,
) -> GalleryDlEntrypoint:
    try:
        return f2.probe_gallery_dl_entrypoint(
            explicit_command,
            runner=runner,
            python_executable=python_executable,
        )
    except f2.GalleryDlUnavailable as exc:
        raise GalleryDlUnavailable(str(exc)) from exc


def build_metadata_command(entrypoint: GalleryDlEntrypoint, work_id: str) -> list[str]:
    return f2.build_metadata_command(entrypoint, work_id)


def _parse_gallery_dl_text(text_value: str, *, source_file: Path) -> f1.ParseResult:
    return f2._parse_gallery_dl_text(text_value, source_file=source_file)


def _merge_parse_results(results: Sequence[f1.ParseResult]) -> f1.ParseResult:
    return f2._merge_parse_results(results)


def _write_raw_stdout(raw_dir: Path, index: int, stdout: str) -> Path:
    raw_dir = resolve_repo_path(raw_dir)
    require_under_phase_output(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_path = raw_dir / f"metadata-{index:02d}.jsonl"
    require_under_phase_output(output_path)
    output_path.write_text(stdout, encoding="utf-8", newline="\n")
    return output_path


def run_metadata_commands(
    samples: Sequence[SelectedSample],
    entrypoint: GalleryDlEntrypoint,
    raw_dir: Path,
    *,
    max_records: int,
    runner: CompletedRunner = subprocess.run,
    timeout: int = 120,
) -> tuple[list[CommandResult], f1.ParseResult]:
    enforce_sample_size(len(samples) or 1)
    enforce_record_count(0, max_records)
    results: list[CommandResult] = []
    accepted_parse_results: list[f1.ParseResult] = []
    accepted_media_record_count = 0
    markers = [sample.work_id for sample in samples]
    for index, sample in enumerate(samples, start=1):
        command = build_metadata_command(entrypoint, sample.work_id)
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
                CommandResult(
                    command_kind="metadata",
                    item_index=index,
                    success=False,
                    exit_code=None,
                    stdout_path_private=None,
                    stdout_bytes=0,
                    stderr_redacted=redact_text(str(exc), private_markers=markers),
                    error_class=exc.__class__.__name__,
                )
            )
            continue
        except subprocess.SubprocessError as exc:
            results.append(
                CommandResult(
                    command_kind="metadata",
                    item_index=index,
                    success=False,
                    exit_code=None,
                    stdout_path_private=None,
                    stdout_bytes=0,
                    stderr_redacted=redact_text(str(exc), private_markers=markers),
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
            parsed = _parse_gallery_dl_text(stdout, source_file=candidate_path)
            parsed_media_count = len(parsed.media_records)
            if accepted_media_record_count + parsed_media_count > max_records:
                success = False
                blocked_over_limit = True
                error_class = "sample_or_record_cap_exceeded"
            else:
                stdout_path = _write_raw_stdout(raw_dir, index, stdout)
                accepted_media_record_count += parsed_media_count
                accepted_parse_results.append(parsed)
        results.append(
            CommandResult(
                command_kind="metadata",
                item_index=index,
                success=success,
                exit_code=completed.returncode,
                stdout_path_private=_rel(stdout_path) if stdout_path else None,
                stdout_bytes=len(stdout.encode("utf-8")),
                stderr_redacted=redact_text(completed.stderr or "", private_markers=markers),
                error_class=None if success else error_class or "empty_metadata_output",
                error_is_auth_or_config=auth_blocked,
                parsed_media_record_count=parsed_media_count,
                blocked_over_limit=blocked_over_limit,
            )
        )
        if blocked_over_limit:
            break
    return results, _merge_parse_results(accepted_parse_results)


def select_local_pixiv_prior_samples(
    prior_index: f1.LocalPriorIndex,
    *,
    sample_size: int,
) -> tuple[dict[str, Any], list[SelectedSample]]:
    enforce_sample_size(sample_size)
    grouped: dict[str, dict[str, Any]] = {}
    for (_work_id, page_index), candidates in prior_index.by_work_page.items():
        for candidate in candidates:
            entry = grouped.setdefault(
                candidate.work_id,
                {
                    "work_id": candidate.work_id,
                    "page_indexes": set(),
                    "content_classes": set(),
                    "media_ids": set(),
                    "basenames": set(),
                    "duplicate_or_ambiguous": False,
                },
            )
            entry["page_indexes"].add(int(page_index))
            entry["content_classes"].add(candidate.content_class)
            entry["media_ids"].add(int(candidate.media_id))
            entry["basenames"].update(candidate.basenames_private)
        if len(candidates) != 1:
            grouped.setdefault(
                _work_id,
                {
                    "work_id": _work_id,
                    "page_indexes": {int(page_index)},
                    "content_classes": set(),
                    "media_ids": set(),
                    "basenames": set(),
                    "duplicate_or_ambiguous": True,
                },
            )["duplicate_or_ambiguous"] = True

    samples = [
        SelectedSample(
            work_id=str(value["work_id"]),
            page_indexes=tuple(sorted(value["page_indexes"])),
            content_classes=tuple(sorted(value["content_classes"])),
            local_media_ids_private=tuple(sorted(value["media_ids"])),
            local_basenames_private=tuple(sorted(value["basenames"])),
            has_p0_page=0 in value["page_indexes"],
            has_non_p0_page=any(int(page) > 0 for page in value["page_indexes"]),
            duplicate_or_ambiguous=bool(value["duplicate_or_ambiguous"]),
        )
        for value in grouped.values()
        if value["content_classes"] == {"anime"}
    ]

    def sort_key(sample: SelectedSample) -> tuple[int, int, int, str]:
        return (
            0 if "anime" in sample.content_classes else 1,
            0 if not sample.duplicate_or_ambiguous else 1,
            -len(sample.page_indexes),
            sample.work_id,
        )

    ordered = sorted(samples, key=sort_key)
    selected: list[SelectedSample] = []
    selected_ids: set[str] = set()

    def add_first(predicate: Callable[[SelectedSample], bool]) -> None:
        for sample in ordered:
            if sample.work_id not in selected_ids and predicate(sample):
                selected.append(sample)
                selected_ids.add(sample.work_id)
                return

    add_first(lambda sample: sample.has_p0_page and not sample.has_non_p0_page)
    add_first(lambda sample: sample.has_non_p0_page)
    add_first(lambda sample: len(sample.page_indexes) > 1)
    for sample in ordered:
        if len(selected) >= sample_size:
            break
        if sample.work_id not in selected_ids:
            selected.append(sample)
            selected_ids.add(sample.work_id)

    content_counts: Counter[str] = Counter()
    page_case_counts: Counter[str] = Counter()
    multi_local_page_count = 0
    for sample in selected:
        for content_class in sample.content_classes:
            content_counts[content_class] += 1
        if len(sample.page_indexes) > 1:
            multi_local_page_count += 1
        if sample.has_p0_page and sample.has_non_p0_page:
            page_case_counts["p0_and_non_p0"] += 1
        elif sample.has_non_p0_page:
            page_case_counts["non_p0_only"] += 1
        else:
            page_case_counts["p0_only"] += 1

    public = {
        "selected_count": len(selected),
        "requested_sample_size": sample_size,
        "default_sample_size": DEFAULT_SAMPLE_SIZE,
        "max_sample_size": MAX_SAMPLE_SIZE,
        "max_normalized_records": MAX_RECORDS_WITHOUT_RENEWED_APPROVAL,
        "sample_gate_status": "passed",
        "selection_strategy": "anime_only_cover_p0_non_p0_multi_page_then_fill_by_work_id",
        "page_case_distribution": dict(sorted(page_case_counts.items())),
        "multi_local_page_work_count": multi_local_page_count,
        "content_class_distribution": dict(sorted(content_counts.items())),
        "duplicate_or_ambiguous_count": sum(1 for sample in selected if sample.duplicate_or_ambiguous),
        "exact_work_ids_public": False,
        "exact_media_ids_public": False,
        "exact_filenames_public": False,
        "prior_total_keys": prior_index.total_prior_keys,
        "prior_total_media": prior_index.total_prior_media,
        "prior_total_media_inspected": prior_index.total_media_inspected,
    }
    return public, selected


def normalize_unicode_tag(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"\s+", " ", normalized)


def lookup_key(value: str) -> str:
    normalized = normalize_unicode_tag(value).casefold()
    normalized = normalized.replace(" ", "_")
    return re.sub(r"_+", "_", normalized).strip("_")


PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u3000": " ",
        "\uff08": "(",
        "\uff09": ")",
        "\u3010": "(",
        "\u3011": ")",
        "\u300c": "(",
        "\u300d": ")",
        "\u300e": "(",
        "\u300f": ")",
        "\uff3b": "[",
        "\uff3d": "]",
        "\uff0f": "/",
        "\uff1a": ":",
        "\uff06": "&",
        "\uff0b": "+",
        "\uff0d": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u30fb": "_",
        "\u00b7": "_",
        "\uff0c": ",",
        "\u3001": ",",
    }
)


def punctuation_normalize(value: str) -> str:
    translated = normalize_unicode_tag(value).translate(PUNCTUATION_TRANSLATION)
    translated = re.sub(r"\s*([()/:,&+\-\[\]])\s*", r"\1", translated)
    return re.sub(r"\s+", " ", translated).strip()


def bracket_normalize(value: str) -> str:
    normalized = punctuation_normalize(value)
    normalized = re.sub(r"\[([^\[\]]+)\]", r"(\1)", normalized)
    return normalized


def _is_latinish(value: str) -> bool:
    return any("a" <= char.casefold() <= "z" for char in value)


def multilingual_normalize_tag(raw_tag: str) -> MultilingualTagNormalization:
    raw = str(raw_tag)
    nfkc = unicodedata.normalize("NFKC", raw)
    whitespace = re.sub(r"\s+", " ", nfkc).strip()
    punctuation = punctuation_normalize(whitespace)
    bracket = bracket_normalize(punctuation)
    casefolded = punctuation.casefold() if _is_latinish(punctuation) else None
    underscore = lookup_key(punctuation)
    candidates: list[str] = []
    for value in (
        punctuation,
        bracket,
        casefolded or "",
        underscore,
        punctuation.replace(" ", "_"),
        bracket.replace(" ", "_"),
        raw,
        nfkc,
        whitespace,
    ):
        candidate = lookup_key(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return MultilingualTagNormalization(
        raw_tag=raw,
        unicode_nfkc_normalized=nfkc,
        whitespace_normalized=whitespace,
        casefolded_latin_form=casefolded,
        underscore_form=underscore,
        punctuation_normalized_form=punctuation,
        bracket_normalized_form=bracket,
        lookup_candidates=tuple(candidates),
        canonical_lookup_key=candidates[0] if candidates else "",
    )


def tag_normalization_fields(raw_tag: str) -> dict[str, Any]:
    profile = multilingual_normalize_tag(raw_tag)
    return {
        "unicode_nfkc_normalized": profile.unicode_nfkc_normalized,
        "whitespace_normalized": profile.whitespace_normalized,
        "casefolded_latin_form": profile.casefolded_latin_form,
        "underscore_form": profile.underscore_form,
        "punctuation_normalized_form": profile.punctuation_normalized_form,
        "bracket_normalized_form": profile.bracket_normalized_form,
        "lookup_candidates": profile.lookup_candidates,
        "canonical_lookup_key": profile.canonical_lookup_key,
    }


def _lookup_key_variants(value: str) -> set[str]:
    normalized = multilingual_normalize_tag(value)
    keys = {
        lookup_key(normalized.raw_tag),
        lookup_key(normalized.unicode_nfkc_normalized),
        lookup_key(normalized.punctuation_normalized_form),
        lookup_key(normalized.bracket_normalized_form),
        normalized.canonical_lookup_key,
    }
    keys.update(normalized.lookup_candidates)
    return {key for key in keys if key}


def _enum_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _entity_namespace(entity_type: str) -> str:
    if entity_type == EntityTypeEnum.character.value:
        return "character"
    if entity_type in {EntityTypeEnum.artist.value, EntityTypeEnum.circle.value}:
        return "artist"
    if entity_type in {EntityTypeEnum.work.value, EntityTypeEnum.franchise.value, EntityTypeEnum.source.value}:
        return "copyright"
    return "ambiguous"


def _tag_namespace(category: str) -> str:
    if category == TagCategoryEnum.character.value:
        return "character"
    if category == TagCategoryEnum.copyright.value:
        return "copyright"
    if category == TagCategoryEnum.artist.value:
        return "artist"
    if category == TagCategoryEnum.meta.value:
        return "meta"
    return "general"


def map_danbooru_category_to_namespace(category: Any) -> str:
    raw = str(category).strip().casefold()
    mapping = {
        "0": "general",
        "general": "general",
        "1": "artist",
        "artist": "artist",
        "3": "copyright",
        "copyright": "copyright",
        "4": "character",
        "character": "character",
        "5": "meta",
        "meta": "meta",
    }
    return mapping.get(raw, "unknown")


def _provider_identity_key(provider: str | None, external_id: str | None) -> tuple[str, str] | None:
    if not provider or external_id is None:
        return None
    normalized_provider = normalize_unicode_tag(provider).casefold()
    normalized_external_id = normalize_unicode_tag(external_id)
    if not normalized_provider or not normalized_external_id:
        return None
    return normalized_provider, normalized_external_id


def _trusted_alias_source(source: str | None, needs_review: bool | None) -> bool:
    if needs_review:
        return False
    return source in {
        EntityMetadataSourceEnum.manual.value,
        EntityMetadataSourceEnum.trusted_external.value,
        EntityMetadataSourceEnum.imported.value,
        EntityMetadataSourceEnum.system.value,
    }


def _put_entity_match(index: LocalClassificationIndex, key_value: str, match: LocalEntityMatch) -> None:
    for key in _lookup_key_variants(key_value):
        index.entity_matches.setdefault(key, match)


def _put_provider_entity_match(
    index: LocalClassificationIndex,
    provider: str | None,
    external_id: str | None,
    match: LocalEntityMatch,
) -> None:
    identity_key = _provider_identity_key(provider, external_id)
    if identity_key is not None:
        index.provider_entity_matches.setdefault(identity_key, match)


def _pixiv_provider_entity_match(
    index: LocalClassificationIndex,
    *,
    provider: str,
    external_id: str | None,
) -> LocalEntityMatch | None:
    identity_key = _provider_identity_key(provider, external_id)
    if identity_key is None:
        return None
    normalized_provider, normalized_external_id = identity_key
    providers = {normalized_provider}
    if normalized_provider == "pixiv_user":
        providers.update({"pixiv", "pixiv_user_id", "pixiv_artist", "pixiv_artist_id"})
    for candidate_provider in providers:
        if candidate_provider not in PIXIV_IDENTITY_PROVIDERS:
            continue
        match = index.provider_entity_matches.get((candidate_provider, normalized_external_id))
        if match:
            return match
    return None


def _put_tag_match(index: LocalClassificationIndex, key_value: str, match: LocalTagCategoryMatch) -> None:
    for key in _lookup_key_variants(key_value):
        index.tag_matches.setdefault(key, match)


def build_local_classification_index(session: Session) -> LocalClassificationIndex:
    index = LocalClassificationIndex()

    tags = session.query(Tag).order_by(Tag.id.asc()).all()
    index.total_tags_indexed = len(tags)
    for tag in tags:
        match = LocalTagCategoryMatch(
            tag_id_private=int(tag.id),
            tag_name_private=str(tag.name),
            category=_enum_value(tag.category or TagCategoryEnum.general),
            match_kind="existing_tag_category_match",
        )
        _put_tag_match(index, str(tag.name), match)

    tag_aliases = session.query(TagAlias).order_by(TagAlias.id.asc()).all()
    index.total_tag_aliases_indexed = len(tag_aliases)
    for alias in tag_aliases:
        target = alias.target_tag
        if not target:
            continue
        match = LocalTagCategoryMatch(
            tag_id_private=int(target.id),
            tag_name_private=str(target.name),
            category=_enum_value(target.category or TagCategoryEnum.general),
            match_kind="existing_tag_alias_category_match",
        )
        _put_tag_match(index, str(alias.alias_name), match)

    entities = session.query(Entity).order_by(Entity.id.asc()).all()
    index.total_entities_indexed = len(entities)
    for entity in entities:
        if _enum_value(entity.status) != EntityStatusEnum.active.value:
            continue
        match = LocalEntityMatch(
            entity_id_private=int(entity.id),
            canonical_name_private=str(entity.canonical_name),
            entity_type=_enum_value(entity.type),
            match_kind="existing_entity_match",
            confidence=0.9,
        )
        _put_entity_match(index, str(entity.canonical_name), match)
        if entity.normalized_key:
            _put_entity_match(index, str(entity.normalized_key), match)

    aliases = session.query(EntityAlias).order_by(EntityAlias.id.asc()).all()
    index.total_entity_aliases_indexed = len(aliases)
    for alias in aliases:
        entity = alias.entity
        if not entity or _enum_value(entity.status) != EntityStatusEnum.active.value:
            continue
        source = _enum_value(alias.source)
        trusted = _trusted_alias_source(source, bool(alias.needs_review))
        match = LocalEntityMatch(
            entity_id_private=int(entity.id),
            canonical_name_private=str(entity.canonical_name),
            entity_type=_enum_value(entity.type),
            match_kind="existing_entity_alias_match",
            alias_id_private=int(alias.id),
            source=source,
            confidence=0.95 if trusted else 0.82,
        )
        _put_entity_match(index, str(alias.alias), match)
        if alias.normalized_alias:
            _put_entity_match(index, str(alias.normalized_alias), match)

    identities = session.query(EntityExternalIdentity).order_by(EntityExternalIdentity.id.asc()).all()
    indexed_identity_count = 0
    for identity in identities:
        entity = identity.entity
        if not entity or _enum_value(entity.status) != EntityStatusEnum.active.value:
            continue
        if _enum_value(identity.identity_status) != EntityExternalIdentityStatusEnum.verified.value:
            continue
        match = LocalEntityMatch(
            entity_id_private=int(entity.id),
            canonical_name_private=str(entity.canonical_name),
            entity_type=_enum_value(entity.type),
            match_kind="existing_verified_external_identity_match",
            external_identity_id_private=int(identity.id),
            source=str(identity.provider),
            confidence=0.9,
        )
        _put_provider_entity_match(index, str(identity.provider), str(identity.external_id), match)
        indexed_identity_count += 1
    index.total_external_identities_indexed = indexed_identity_count
    index.total_provider_scoped_external_identities_indexed = indexed_identity_count
    return index


def enforce_tag_lookup_limit(limit: int) -> None:
    if limit < 0 or limit > MAX_TAG_LOOKUP_LIMIT_WITHOUT_RENEWED_APPROVAL:
        raise SampleGateError("tag_lookup_cap_exceeded")


def build_tag_lookup_inputs(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
) -> dict[str, TagLookupInput]:
    inputs: dict[str, TagLookupInput] = {}
    for record in records:
        for raw_tag in record.tags:
            profile = multilingual_normalize_tag(raw_tag)
            key = profile.canonical_lookup_key
            if key:
                inputs.setdefault(
                    key,
                    TagLookupInput(
                        raw_tag=raw_tag,
                        normalized_tag=profile.whitespace_normalized,
                        canonical_lookup_key=key,
                        lookup_candidates=profile.lookup_candidates,
                    ),
                )
    return inputs


def fetch_danbooru_tag_payload(normalized_key: str, *, timeout: int) -> list[dict[str, Any]]:
    query = quote(normalized_key, safe="*_-")
    url = f"{EXTERNAL_TAG_LOOKUP_BASE_URL}?search[name_matches]={query}&limit=5"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": EXTERNAL_TAG_LOOKUP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise ExternalLookupProviderBlocked(f"danbooru_lookup_blocked_http_{exc.code}") from exc
        raise
    if not isinstance(payload, list):
        raise Phase44P2RF3Error("external_tag_lookup_payload_shape_invalid")
    return [item for item in payload if isinstance(item, dict)]


def fetch_danbooru_tag_alias_payload(normalized_key: str, *, timeout: int) -> list[dict[str, Any]]:
    query = quote(normalized_key, safe="*_-")
    url = (
        f"{EXTERNAL_TAG_ALIAS_LOOKUP_BASE_URL}"
        f"?search[antecedent_name]={query}&search[status]=active&limit=5"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": EXTERNAL_TAG_LOOKUP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise ExternalLookupProviderBlocked(f"danbooru_alias_lookup_blocked_http_{exc.code}") from exc
        raise
    if not isinstance(payload, list):
        raise Phase44P2RF3Error("external_tag_alias_lookup_payload_shape_invalid")
    return [item for item in payload if isinstance(item, dict)]


class ExternalLookupProviderBlocked(Phase44P2RF3Error):
    pass


def _select_danbooru_tag_match(payload: Sequence[Mapping[str, Any]], normalized_key: str) -> Mapping[str, Any] | None:
    for item in payload:
        name = item.get("name")
        if isinstance(name, str) and lookup_key(name) == normalized_key:
            return item
    for item in payload:
        name = item.get("name")
        if isinstance(name, str) and lookup_key(name).replace("_", "") == normalized_key.replace("_", ""):
            return item
    return None


def fetch_danbooru_tag_category_payload(normalized_key: str, *, timeout: int) -> tuple[list[dict[str, Any]], int, str]:
    tag_payload = fetch_danbooru_tag_payload(normalized_key, timeout=timeout)
    request_count = 1
    if _select_danbooru_tag_match(tag_payload, normalized_key):
        return tag_payload, request_count, normalized_key

    alias_payload = fetch_danbooru_tag_alias_payload(normalized_key, timeout=timeout)
    request_count += 1
    for alias in alias_payload:
        consequent_name = alias.get("consequent_name")
        if not isinstance(consequent_name, str) or not consequent_name:
            continue
        canonical_key = lookup_key(consequent_name)
        if not canonical_key:
            continue
        canonical_payload = fetch_danbooru_tag_payload(canonical_key, timeout=timeout)
        request_count += 1
        if _select_danbooru_tag_match(canonical_payload, canonical_key):
            return canonical_payload, request_count, canonical_key
    return tag_payload, request_count, normalized_key


def fetch_safebooru_tag_payload(normalized_key: str, *, timeout: int) -> list[dict[str, Any]]:
    query = quote(normalized_key, safe="*_-")
    url = f"{SAFEBOORU_TAG_LOOKUP_BASE_URL}?page=dapi&s=tag&q=index&name={query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/xml",
            "User-Agent": EXTERNAL_TAG_LOOKUP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise ExternalLookupProviderBlocked(f"safebooru_lookup_blocked_http_{exc.code}") from exc
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


def _coerce_fetch_result(fetch_result: Any, *, default_key: str) -> tuple[list[dict[str, Any]], int, str]:
    if isinstance(fetch_result, tuple) and len(fetch_result) == 3:
        payload, request_count, matched_key = fetch_result
        return list(payload), int(request_count), str(matched_key)
    if isinstance(fetch_result, tuple) and len(fetch_result) == 2:
        payload, request_count = fetch_result
        return list(payload), int(request_count), default_key
    return list(fetch_result), 1, default_key


def _lookup_result_from_danbooru_payload(
    *,
    raw_tag: str,
    normalized_tag: str,
    canonical_lookup_key: str,
    lookup_source: str,
    lookup_source_version: str,
    payload: Sequence[Mapping[str, Any]],
    cache_status: str,
    matched_lookup_key: str | None = None,
) -> ExternalTagLookupResult:
    key = canonical_lookup_key or lookup_key(normalized_tag)
    match_key = matched_lookup_key or key
    match = _select_danbooru_tag_match(payload, match_key)
    if not match:
        return ExternalTagLookupResult(
            raw_tag=raw_tag,
            normalized_tag=normalized_tag,
            canonical_lookup_key=key,
            lookup_source=lookup_source,
            lookup_source_version=lookup_source_version,
            source_tag_id=None,
            source_tag_name=None,
            source_category_raw=None,
            mapped_candidate_namespace="unknown",
            confidence=0.0,
            provenance_url_or_key=f"{lookup_source}:{key}",
            status="not_found",
            cache_status=cache_status,
        )

    source_tag_id = match.get("id")
    source_tag_name = match.get("name")
    source_category = match.get("category")
    namespace = map_danbooru_category_to_namespace(source_category)
    status = "hit" if namespace != "unknown" else "lookup_error"
    error = None if status == "hit" else "unknown_danbooru_category"
    if lookup_source == SAFEBOORU_TAGS_SOURCE:
        provenance = f"{SAFEBOORU_TAGS_SOURCE}:tag_id:{source_tag_id}" if source_tag_id is not None else f"{lookup_source}:{key}"
    else:
        provenance = (
            f"https://danbooru.donmai.us/tags/{source_tag_id}"
            if source_tag_id is not None
            else f"{lookup_source}:{key}"
        )
    return ExternalTagLookupResult(
        raw_tag=raw_tag,
        normalized_tag=normalized_tag,
        canonical_lookup_key=key,
        lookup_source=lookup_source,
        lookup_source_version=lookup_source_version,
        source_tag_id=str(source_tag_id) if source_tag_id is not None else None,
        source_tag_name=str(source_tag_name) if source_tag_name is not None else None,
        source_category_raw=str(source_category) if source_category is not None else None,
        mapped_candidate_namespace=namespace,
        confidence=0.84 if status == "hit" else 0.0,
        provenance_url_or_key=provenance,
        status=status,
        cache_status=cache_status,
        lookup_error=error,
    )


def _cache_row_to_lookup_result(row: ExternalTagCategoryLookupCache) -> ExternalTagLookupResult:
    return ExternalTagLookupResult(
        raw_tag=str(row.raw_tag or ""),
        normalized_tag=str(row.normalized_tag),
        canonical_lookup_key=str(row.canonical_lookup_key or lookup_key(str(row.normalized_tag))),
        lookup_source=str(row.lookup_source),
        lookup_source_version=str(row.lookup_source_version) if row.lookup_source_version else None,
        source_tag_id=str(row.source_tag_id) if row.source_tag_id is not None else None,
        source_tag_name=str(row.source_tag_name) if row.source_tag_name is not None else None,
        source_category_raw=str(row.source_category_raw) if row.source_category_raw is not None else None,
        mapped_candidate_namespace=str(row.mapped_candidate_namespace) if row.mapped_candidate_namespace else None,
        confidence=float(row.confidence) if row.confidence is not None else None,
        provenance_url_or_key=str(row.provenance_url_or_key) if row.provenance_url_or_key else None,
        status=str(row.status),
        cache_status="hit",
        lookup_error=str(row.lookup_error) if row.lookup_error else None,
        retry_after=row.retry_after.isoformat() if row.retry_after else None,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
    )


def _upsert_lookup_cache_result(
    session: Session,
    result: ExternalTagLookupResult,
    *,
    manual_override_status: str | None = None,
    manual_override_value: Any = MANUAL_OVERRIDE_VALUE_UNSET,
) -> bool:
    fields = result.to_cache_fields(
        manual_override_status=manual_override_status,
        manual_override_value=manual_override_value,
    )
    row = (
        session.query(ExternalTagCategoryLookupCache)
        .filter(
            ExternalTagCategoryLookupCache.lookup_source == result.lookup_source,
            ExternalTagCategoryLookupCache.canonical_lookup_key == result.canonical_lookup_key,
        )
        .one_or_none()
    )
    source_tag_id_unique_for_lookup = result.lookup_source != DANBOORU_ALIAS_SOURCE
    if row is None and result.source_tag_id and source_tag_id_unique_for_lookup:
        row = (
            session.query(ExternalTagCategoryLookupCache)
            .filter(
                ExternalTagCategoryLookupCache.lookup_source == result.lookup_source,
                ExternalTagCategoryLookupCache.source_tag_id == result.source_tag_id,
            )
            .one_or_none()
        )
    if row is None:
        row = (
            session.query(ExternalTagCategoryLookupCache)
            .filter(
                ExternalTagCategoryLookupCache.lookup_source == result.lookup_source,
                ExternalTagCategoryLookupCache.normalized_tag == result.normalized_tag,
            )
            .one_or_none()
        )
    if row is None:
        session.add(ExternalTagCategoryLookupCache(**fields))
        return True
    for key, value in fields.items():
        if key == "first_seen_at":
            continue
        if key in {"raw_tag", "normalized_tag"} and getattr(row, key, None):
            continue
        if key == "manual_override_status" and value is None:
            continue
        setattr(row, key, value)
    return False


def _lookup_one_external_source(
    source: str,
    tag_input: TagLookupInput,
    *,
    timeout_seconds: int,
    fetcher: Callable[[str, int], Any] | None = None,
    max_requests: int | None = None,
    delay_seconds: float = 0.0,
) -> tuple[ExternalTagLookupResult, int]:
    key = tag_input.canonical_lookup_key
    if max_requests is not None and max_requests <= 0:
        return (
            ExternalTagLookupResult(
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
                lookup_error="external_request_budget_exhausted",
            ),
            0,
        )
    if fetcher is not None:
        payload, request_count, matched_lookup_key = _coerce_fetch_result(fetcher(key, timeout_seconds), default_key=key)
        return (
            _lookup_result_from_danbooru_payload(
                raw_tag=tag_input.raw_tag,
                normalized_tag=tag_input.normalized_tag,
                canonical_lookup_key=key,
                lookup_source=source,
                lookup_source_version=source,
                payload=payload,
                cache_status="miss",
                matched_lookup_key=matched_lookup_key,
            ),
            request_count,
        )
    if source == DANBOORU_TAGS_SOURCE:
        payload = fetch_danbooru_tag_payload(key, timeout=timeout_seconds)
        return (
            _lookup_result_from_danbooru_payload(
                raw_tag=tag_input.raw_tag,
                normalized_tag=tag_input.normalized_tag,
                canonical_lookup_key=key,
                lookup_source=source,
                lookup_source_version=source,
                payload=payload,
                cache_status="miss",
                matched_lookup_key=key,
            ),
            1,
        )
    if source == DANBOORU_ALIAS_SOURCE:
        alias_payload = fetch_danbooru_tag_alias_payload(key, timeout=timeout_seconds)
        request_count = 1
        for alias in alias_payload:
            if max_requests is not None and request_count >= max_requests:
                break
            consequent_name = alias.get("consequent_name")
            if not isinstance(consequent_name, str) or not consequent_name:
                continue
            canonical_key = lookup_key(consequent_name)
            if not canonical_key:
                continue
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            tag_payload = fetch_danbooru_tag_payload(canonical_key, timeout=timeout_seconds)
            request_count += 1
            result = _lookup_result_from_danbooru_payload(
                raw_tag=tag_input.raw_tag,
                normalized_tag=tag_input.normalized_tag,
                canonical_lookup_key=key,
                lookup_source=source,
                lookup_source_version=source,
                payload=tag_payload,
                cache_status="miss",
                matched_lookup_key=canonical_key,
            )
            if result.status == "hit":
                return result, request_count
        return (
            ExternalTagLookupResult(
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
                status="not_found",
                cache_status="miss",
            ),
            request_count,
        )
    if source == SAFEBOORU_TAGS_SOURCE:
        payload = fetch_safebooru_tag_payload(key, timeout=timeout_seconds)
        return (
            _lookup_result_from_danbooru_payload(
                raw_tag=tag_input.raw_tag,
                normalized_tag=tag_input.normalized_tag,
                canonical_lookup_key=key,
                lookup_source=source,
                lookup_source_version=source,
                payload=payload,
                cache_status="miss",
                matched_lookup_key=key,
            ),
            1,
        )
    raise Phase44P2RF3Error(f"unsupported_lookup_source:{source}")


def lookup_external_tag_categories(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    *,
    session: Session | None,
    lookup_limit: int,
    delay_seconds: float,
    timeout_seconds: int,
    cache_writes_enabled: bool,
    fetcher: Callable[[str, int], Any] | None = None,
    lookup_sources: Sequence[str] = DEFAULT_LOOKUP_SOURCES,
) -> tuple[dict[str, ExternalTagLookupResult], ExternalLookupSummary]:
    enforce_tag_lookup_limit(lookup_limit)
    if fetcher is not None and tuple(lookup_sources) == DEFAULT_LOOKUP_SOURCES:
        lookup_sources = (EXTERNAL_TAG_LOOKUP_SOURCE,)
    tag_inputs = build_tag_lookup_inputs(records)
    ordered_keys = list(tag_inputs.keys())
    capped_keys = ordered_keys[:lookup_limit]
    summary = ExternalLookupSummary(
        unique_normalized_tag_count=len(tag_inputs),
        lookup_limit=lookup_limit,
        external_request_budget=lookup_limit * max(len(lookup_sources), 1),
        lookup_delay_seconds=delay_seconds,
        lookup_timeout_seconds=timeout_seconds,
        cache_write_enabled=bool(cache_writes_enabled and session is not None),
        lookup_sources_attempted=list(lookup_sources),
    )
    if not tag_inputs:
        return {}, summary

    results: dict[str, ExternalTagLookupResult] = {}
    for idx, key in enumerate(capped_keys):
        tag_input = tag_inputs[key]
        if idx > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        last_result: ExternalTagLookupResult | None = None
        for source in lookup_sources:
            cached_result: ExternalTagLookupResult | None = None
            if session is not None:
                cached_row = (
                    session.query(ExternalTagCategoryLookupCache)
                    .filter(
                        ExternalTagCategoryLookupCache.lookup_source == source,
                        ExternalTagCategoryLookupCache.canonical_lookup_key == key,
                    )
                    .one_or_none()
                )
                if cached_row and str(cached_row.status) in SUCCESSFUL_LOOKUP_STATUSES:
                    cached_result = _cache_row_to_lookup_result(cached_row)
                    summary.cache_hit_count += 1
                    summary.source_cache_hit_counts[source] = summary.source_cache_hit_counts.get(source, 0) + 1
                elif cached_row and _not_found_cache_is_fresh(cached_row, source=source):
                    last_result = _cache_row_to_lookup_result(cached_row)
                    summary.negative_cache_hit_count += 1
                    summary.source_negative_cache_hit_counts[source] = (
                        summary.source_negative_cache_hit_counts.get(source, 0) + 1
                    )
                    continue
                else:
                    summary.cache_miss_count += 1
                    summary.source_cache_miss_counts[source] = summary.source_cache_miss_counts.get(source, 0) + 1
            else:
                summary.cache_miss_count += 1
                summary.source_cache_miss_counts[source] = summary.source_cache_miss_counts.get(source, 0) + 1
            if cached_result:
                results[key] = cached_result
                last_result = cached_result
                break
            remaining_request_budget = max(summary.external_request_budget - summary.request_count, 0)
            if remaining_request_budget <= 0:
                summary.request_budget_exhausted = True
                break
            try:
                result, request_count = _lookup_one_external_source(
                    source,
                    tag_input,
                    timeout_seconds=timeout_seconds,
                    fetcher=fetcher if len(lookup_sources) == 1 else None,
                    max_requests=remaining_request_budget,
                    delay_seconds=delay_seconds,
                )
                summary.request_count += request_count
                summary.source_request_counts[source] = summary.source_request_counts.get(source, 0) + request_count
            except ExternalLookupProviderBlocked as exc:
                summary.request_count += 1
                summary.source_request_counts[source] = summary.source_request_counts.get(source, 0) + 1
                summary.provider_blocked = True
                summary.provider_block_reason = str(exc)
                result = ExternalTagLookupResult(
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
            except Exception as exc:  # noqa: BLE001 - item-level lookup failures stay unresolved.
                summary.request_count += 1
                summary.source_request_counts[source] = summary.source_request_counts.get(source, 0) + 1
                result = ExternalTagLookupResult(
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
                    lookup_error=exc.__class__.__name__,
                )
            result = _with_default_cache_lifecycle(result)
            last_result = result
            if session is not None and cache_writes_enabled:
                _upsert_lookup_cache_result(session, result)
                summary.cache_write_count += 1
                summary.source_cache_write_counts[source] = summary.source_cache_write_counts.get(source, 0) + 1
            if result.status == "hit":
                results[key] = result
                break
        if key not in results and last_result is not None:
            results[key] = last_result

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


def install_external_lookup_cache_write_guard(engine) -> None:
    write_re = re.compile(r"^\s*(insert|update|delete|alter|drop|truncate|create)\b", re.IGNORECASE)
    allowed_cache_write_re = re.compile(
        r"^\s*(insert\s+into|update)\s+\"?blombooru_external_tag_category_lookup_cache\"?\b",
        re.IGNORECASE,
    )

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(_conn, _cursor, statement, _parameters, _context, _executemany):
        sql = str(statement).strip()
        if not write_re.search(sql):
            return
        if allowed_cache_write_re.search(sql):
            return
        lowered = sql.casefold()
        touched_truth_tables = sorted(table for table in FORBIDDEN_TRUTH_TABLES if table in lowered)
        detail = ",".join(touched_truth_tables) if touched_truth_tables else "non_cache_write"
        raise f1.ReadOnlyViolation(f"db_write_blocked_except_external_tag_category_lookup_cache:{detail}")


def _is_sensitive_or_meta(key: str) -> bool:
    return key in SENSITIVE_OR_META_TERMS or key.replace("_", "-") in SENSITIVE_OR_META_TERMS


def _is_general_descriptor(key: str, normalized: str) -> bool:
    return key in GENERAL_DESCRIPTOR_TERMS or normalized in GENERAL_DESCRIPTOR_TERMS


def _is_original_marker(key: str, normalized: str) -> bool:
    return key in ORIGINAL_WORK_TERMS or normalized in ORIGINAL_WORK_TERMS


def _looks_like_proper_noun_candidate(normalized: str) -> bool:
    if not normalized:
        return False
    has_cjk = any("\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff" for char in normalized)
    has_upper = any(char.isupper() for char in normalized if char.isalpha())
    has_punct = any(char in normalized for char in (":", "\u30fb", "\uff0f", "/", "&", "+"))
    return has_cjk or has_upper or has_punct


def parse_pixiv_parenthetical_tag(raw_tag: str) -> ParentheticalTagPattern | None:
    profile = multilingual_normalize_tag(raw_tag)
    text = profile.bracket_normalized_form
    if not text or text.count("(") != 1 or text.count(")") != 1:
        return None
    start = text.find("(")
    end = text.rfind(")")
    if start <= 0 or end <= start + 1 or end != len(text) - 1:
        return None
    outer = text[:start].strip()
    inner = text[start + 1 : end].strip()
    if not outer or not inner:
        return None
    if any(mark in outer + inner for mark in ("(", ")")):
        return ParentheticalTagPattern(
            raw_tag=raw_tag,
            outer_name=outer,
            inner_work_or_context=inner,
            bracket_style="normalized_parentheses",
            ambiguous_or_nested=True,
        )
    return ParentheticalTagPattern(
        raw_tag=raw_tag,
        outer_name=outer,
        inner_work_or_context=inner,
        bracket_style="normalized_parentheses",
    )


def _parenthetical_part_candidate(
    part: str,
    *,
    raw_tag: str,
    record: f2.PixivGalleryDlAdapterRecord,
    expected_namespace: str,
    local_index: LocalClassificationIndex,
    external_lookup_results: Mapping[str, ExternalTagLookupResult] | None,
) -> PixivCandidateRow:
    evidence_row = classify_pixiv_tag(
        part,
        record=record,
        local_index=local_index,
        external_lookup_results=external_lookup_results,
    )
    confirmed_by_source = evidence_row.candidate_namespace == expected_namespace and evidence_row.lookup_source in {
        "local_db_read_only",
        DANBOORU_TAGS_SOURCE,
        DANBOORU_ALIAS_SOURCE,
        SAFEBOORU_TAGS_SOURCE,
        EXTERNAL_TAG_LOOKUP_SOURCE,
    }
    confidence = max(0.72, evidence_row.confidence) if confirmed_by_source else 0.58
    return PixivCandidateRow(
        **{
            **_record_base_fields(record),
            **tag_normalization_fields(part),
        },
        raw_tag=raw_tag,
        normalized_tag=normalize_unicode_tag(part),
        candidate_kind="entity_candidate",
        candidate_namespace=expected_namespace,
        confidence=confidence,
        reason="pixiv_parenthetical_character_work_pattern",
        lookup_source=evidence_row.lookup_source if confirmed_by_source else "pixiv_parenthetical_pattern",
        lookup_key=lookup_key(part),
        lookup_category=evidence_row.lookup_category if confirmed_by_source else "pattern_inferred_context",
        provenance=evidence_row.provenance if confirmed_by_source else "pixiv_raw_tag_parenthetical_structure",
        cache_status=evidence_row.cache_status if confirmed_by_source else "not_applicable",
        existing_entity_match=evidence_row.existing_entity_match if confirmed_by_source else False,
        existing_alias_match=evidence_row.existing_alias_match if confirmed_by_source else False,
        existing_entity_id_private=evidence_row.existing_entity_id_private if confirmed_by_source else None,
        existing_alias_id_private=evidence_row.existing_alias_id_private if confirmed_by_source else None,
        future_entity_candidate_eligible=bool(record.eligible_for_future_entity_candidate),
        future_tag_suggestion_eligible=False,
        requires_manual_review=True,
        db_write_allowed=False,
    )


def parenthetical_candidate_rows(
    raw_tag: str,
    *,
    record: f2.PixivGalleryDlAdapterRecord,
    local_index: LocalClassificationIndex,
    external_lookup_results: Mapping[str, ExternalTagLookupResult] | None,
) -> list[PixivCandidateRow]:
    pattern = parse_pixiv_parenthetical_tag(raw_tag)
    if not pattern or pattern.ambiguous_or_nested:
        return []
    return [
        _parenthetical_part_candidate(
            pattern.outer_name,
            raw_tag=raw_tag,
            record=record,
            expected_namespace="character",
            local_index=local_index,
            external_lookup_results=external_lookup_results,
        ),
        _parenthetical_part_candidate(
            pattern.inner_work_or_context,
            raw_tag=raw_tag,
            record=record,
            expected_namespace="copyright",
            local_index=local_index,
            external_lookup_results=external_lookup_results,
        ),
    ]


def _record_base_fields(record: f2.PixivGalleryDlAdapterRecord) -> dict[str, Any]:
    return {
        "media_id_private": record.local_media_id_private,
        "work_id_private": record.work_id,
        "page_index": record.page_index,
        "title_private": record.title,
        "artist_name_private": record.artist_name,
        "artist_id_private": record.artist_id,
        "future_local_source_hint_eligible": bool(record.eligible_for_future_local_source_hint),
    }


def _artist_candidate(
    record: f2.PixivGalleryDlAdapterRecord,
    *,
    local_index: LocalClassificationIndex,
) -> PixivCandidateRow | None:
    if not (record.artist_name or record.artist_id):
        return None
    eligible = bool(record.eligible_for_future_entity_candidate)
    entity_match = _pixiv_provider_entity_match(
        local_index,
        provider="pixiv_user",
        external_id=record.artist_id,
    )
    reason = "pixiv_user_metadata"
    confidence = 0.92
    existing_entity_match = False
    existing_entity_id_private: int | None = None
    requires_manual_review = False
    if entity_match:
        reason = "pixiv_user_metadata_verified_local_pixiv_identity"
        confidence = max(0.93, entity_match.confidence)
        existing_entity_match = True
        existing_entity_id_private = entity_match.entity_id_private
        eligible = False
    return PixivCandidateRow(
        **_record_base_fields(record),
        raw_tag="",
        normalized_tag=normalize_unicode_tag(record.artist_name or record.artist_id or ""),
        candidate_kind="entity_candidate",
        candidate_namespace="artist",
        confidence=confidence,
        reason=reason,
        lookup_source="pixiv_metadata",
        lookup_key="pixiv_user_id" if record.artist_id else "pixiv_user_name",
        lookup_category="artist",
        provenance="pixiv_user_metadata_field",
        cache_status="not_applicable",
        existing_entity_match=existing_entity_match,
        existing_entity_id_private=existing_entity_id_private,
        future_entity_candidate_eligible=eligible,
        future_tag_suggestion_eligible=False,
        requires_manual_review=requires_manual_review,
        db_write_allowed=False,
    )


def classify_pixiv_tag(
    raw_tag: str,
    *,
    record: f2.PixivGalleryDlAdapterRecord,
    local_index: LocalClassificationIndex,
    external_lookup_results: Mapping[str, ExternalTagLookupResult] | None = None,
) -> PixivCandidateRow:
    normalized = normalize_unicode_tag(raw_tag)
    key = lookup_key(normalized)
    base = {
        **_record_base_fields(record),
        **tag_normalization_fields(raw_tag),
    }

    entity_match = local_index.entity_matches.get(key)
    if entity_match:
        namespace = _entity_namespace(entity_match.entity_type)
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="entity_candidate",
            candidate_namespace=namespace,
            confidence=entity_match.confidence,
            reason="existing_entity_or_alias_match",
            lookup_source="local_db_read_only",
            lookup_key=key,
            lookup_category=entity_match.entity_type,
            provenance=entity_match.match_kind,
            cache_status="not_applicable",
            existing_entity_match=True,
            existing_alias_match=entity_match.alias_id_private is not None,
            existing_entity_id_private=entity_match.entity_id_private,
            existing_alias_id_private=entity_match.alias_id_private,
            future_entity_candidate_eligible=bool(
                record.eligible_for_future_entity_candidate and namespace in ENTITY_NAMESPACES
            ),
            future_tag_suggestion_eligible=False,
            requires_manual_review=entity_match.confidence < 0.9,
            db_write_allowed=False,
        )

    tag_match = local_index.tag_matches.get(key)
    if tag_match:
        namespace = _tag_namespace(tag_match.category)
        is_entity_namespace = namespace in ENTITY_NAMESPACES
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="entity_candidate" if is_entity_namespace else "tag_candidate",
            candidate_namespace=namespace,
            confidence=0.78 if is_entity_namespace else 0.7,
            reason=tag_match.match_kind,
            lookup_source="local_db_read_only",
            lookup_key=key,
            lookup_category=tag_match.category,
            provenance="existing_tag_category",
            cache_status="not_applicable",
            future_entity_candidate_eligible=bool(
                record.eligible_for_future_entity_candidate and is_entity_namespace
            ),
            future_tag_suggestion_eligible=not is_entity_namespace,
            requires_manual_review=is_entity_namespace,
            db_write_allowed=False,
        )

    if _is_original_marker(key, normalized):
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="original_work_context",
            candidate_namespace="ambiguous",
            confidence=0.5,
            reason="deterministic_original_marker",
            lookup_source="deterministic_fallback",
            lookup_key=key,
            lookup_category="original_or_unknown",
            provenance="pixiv_raw_tag",
            cache_status="not_applicable",
            future_entity_candidate_eligible=False,
            future_tag_suggestion_eligible=False,
            requires_manual_review=True,
            db_write_allowed=False,
        )

    lookup_result = (external_lookup_results or {}).get(key)
    if lookup_result and lookup_result.status == "hit" and lookup_result.mapped_candidate_namespace:
        namespace = lookup_result.mapped_candidate_namespace
        is_entity_namespace = namespace in ENTITY_NAMESPACES
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="entity_candidate" if is_entity_namespace else "tag_candidate",
            candidate_namespace=namespace,
            confidence=float(lookup_result.confidence or 0.0),
            reason="external_tag_category_lookup",
            lookup_source=lookup_result.lookup_source,
            lookup_key=key,
            lookup_category=lookup_result.source_category_raw,
            provenance=lookup_result.provenance_url_or_key,
            cache_status=lookup_result.cache_status,
            future_entity_candidate_eligible=bool(
                record.eligible_for_future_entity_candidate and is_entity_namespace
            ),
            future_tag_suggestion_eligible=not is_entity_namespace,
            requires_manual_review=is_entity_namespace,
            db_write_allowed=False,
        )

    if _is_sensitive_or_meta(key):
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="sensitive_or_meta_tag_candidate",
            candidate_namespace="meta",
            confidence=0.58,
            reason="deterministic_sensitive_or_meta_descriptor",
            lookup_source="deterministic_fallback",
            lookup_key=key,
            lookup_category="meta",
            provenance="rule_based_descriptor_list",
            cache_status="not_applicable",
            future_entity_candidate_eligible=False,
            future_tag_suggestion_eligible=False,
            requires_manual_review=False,
            db_write_allowed=False,
        )

    if _is_general_descriptor(key, normalized):
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="tag_candidate",
            candidate_namespace="general",
            confidence=0.48,
            reason="deterministic_general_descriptor_fallback",
            lookup_source="deterministic_fallback",
            lookup_key=key,
            lookup_category="general_unverified",
            provenance="rule_based_descriptor_list",
            cache_status="not_applicable",
            future_entity_candidate_eligible=False,
            future_tag_suggestion_eligible=True,
            requires_manual_review=True,
            db_write_allowed=False,
        )

    if _looks_like_proper_noun_candidate(normalized):
        return PixivCandidateRow(
            **base,
            raw_tag=raw_tag,
            normalized_tag=normalized,
            candidate_kind="ambiguous_proper_noun_candidate",
            candidate_namespace="ambiguous",
            confidence=0.32,
            reason="deterministic_proper_noun_shape_unverified",
            lookup_source="deterministic_fallback",
            lookup_key=key,
            lookup_category="ambiguous",
            provenance="pixiv_raw_tag_shape_only",
            cache_status="not_applicable",
            future_entity_candidate_eligible=False,
            future_tag_suggestion_eligible=False,
            requires_manual_review=True,
            db_write_allowed=False,
        )

    return PixivCandidateRow(
        **base,
        raw_tag=raw_tag,
        normalized_tag=normalized,
        candidate_kind="unknown_or_unresolved_pixiv_tag",
        candidate_namespace="unknown",
        confidence=0.2,
        reason="no_provenance_backed_category",
        lookup_source=LOOKUP_SOURCE_EXTERNAL_DISABLED,
        lookup_key=key,
        lookup_category=None,
        provenance="pixiv_raw_tag_preserved_only",
        cache_status="not_looked_up",
        future_entity_candidate_eligible=False,
        future_tag_suggestion_eligible=False,
        requires_manual_review=True,
        db_write_allowed=False,
    )


def _alias_group_candidates(
    rows: Sequence[PixivCandidateRow],
    *,
    record: f2.PixivGalleryDlAdapterRecord,
) -> list[PixivCandidateRow]:
    groups: dict[int, list[PixivCandidateRow]] = defaultdict(list)
    for row in rows:
        if row.existing_entity_id_private is not None:
            groups[int(row.existing_entity_id_private)].append(row)
    candidates: list[PixivCandidateRow] = []
    for entity_id, group_rows in groups.items():
        alias_rows = [row for row in group_rows if row.existing_alias_match]
        if len(group_rows) < 2 and not alias_rows:
            continue
        first = group_rows[0]
        candidates.append(
            PixivCandidateRow(
                **_record_base_fields(record),
                raw_tag=" | ".join(row.raw_tag for row in group_rows if row.raw_tag),
                normalized_tag=" | ".join(row.normalized_tag for row in group_rows if row.normalized_tag),
                candidate_kind="candidate_alias_group",
                candidate_namespace=first.candidate_namespace,
                confidence=min(0.82, max(row.confidence for row in group_rows)),
                reason="existing_entity_or_alias_match",
                lookup_source="local_db_read_only",
                lookup_key=f"entity:{entity_id}",
                lookup_category=first.lookup_category,
                provenance="cooccurring_tags_share_existing_entity_or_alias",
                cache_status="not_applicable",
                existing_entity_match=True,
                existing_alias_match=bool(alias_rows),
                existing_entity_id_private=entity_id,
                existing_alias_id_private=alias_rows[0].existing_alias_id_private if alias_rows else None,
                future_entity_candidate_eligible=bool(record.eligible_for_future_entity_candidate),
                future_tag_suggestion_eligible=False,
                requires_manual_review=True,
                db_write_allowed=False,
            )
        )
    return candidates


def _original_work_status(rows: Sequence[PixivCandidateRow]) -> str:
    has_context = any(
        row.candidate_namespace in {"copyright", "character"}
        and row.candidate_kind in {"entity_candidate", "candidate_alias_group"}
        for row in rows
    )
    has_original = any(row.reason == "deterministic_original_marker" for row in rows)
    if has_context:
        return "known_or_candidate_work_context"
    if has_original:
        return "original_work_context_claimed_by_pixiv_tag"
    return "original_or_unknown_work_context"


def normalize_metadata_candidates(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    local_index: LocalClassificationIndex,
    external_lookup_results: Mapping[str, ExternalTagLookupResult] | None = None,
) -> tuple[list[PixivNormalizedMetadataCandidate], list[PixivCandidateRow]]:
    normalized_records: list[PixivNormalizedMetadataCandidate] = []
    all_rows: list[PixivCandidateRow] = []
    for record in records:
        rows: list[PixivCandidateRow] = []
        artist = _artist_candidate(record, local_index=local_index)
        if artist:
            rows.append(artist)
        tag_rows = [
            classify_pixiv_tag(
                raw_tag,
                record=record,
                local_index=local_index,
                external_lookup_results=external_lookup_results,
            )
            for raw_tag in record.tags
        ]
        pattern_rows: list[PixivCandidateRow] = []
        parenthetical_patterns = []
        for raw_tag in record.tags:
            pattern = parse_pixiv_parenthetical_tag(raw_tag)
            if pattern:
                parenthetical_patterns.append(pattern.to_private_dict())
            pattern_rows.extend(
                parenthetical_candidate_rows(
                    raw_tag,
                    record=record,
                    local_index=local_index,
                    external_lookup_results=external_lookup_results,
                )
            )
        tag_rows.extend(pattern_rows)
        rows.extend(tag_rows)
        rows.extend(_alias_group_candidates(tag_rows, record=record))
        original_status = _original_work_status(rows)
        unresolved = tuple(
            row.normalized_tag
            for row in rows
            if row.candidate_kind in {"ambiguous_proper_noun_candidate", "unknown_or_unresolved_pixiv_tag"}
        )
        confidence_values = [row.confidence for row in rows]
        confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0
        reasons = tuple(sorted({row.reason for row in rows if row.reason}))
        normalized_records.append(
            PixivNormalizedMetadataCandidate(
                media_id_private=record.local_media_id_private,
                work_id_private=record.work_id,
                page_index=record.page_index,
                page_count=record.page_count,
                title_private=record.title,
                artist_name_private=record.artist_name,
                artist_id_private=record.artist_id,
                raw_pixiv_tags=tuple(record.tags),
                normalized_unicode_tags=tuple(normalize_unicode_tag(tag) for tag in record.tags),
                multilingual_normalizations=[
                    multilingual_normalize_tag(tag).to_private_dict()
                    for tag in record.tags
                ],
                parenthetical_patterns=parenthetical_patterns,
                tag_candidates=[
                    row.to_private_dict()
                    for row in rows
                    if row.candidate_kind
                    in {
                        "tag_candidate",
                        "sensitive_or_meta_tag_candidate",
                        "ambiguous_proper_noun_candidate",
                        "unknown_or_unresolved_pixiv_tag",
                        "original_work_context",
                    }
                ],
                entity_candidates=[
                    row.to_private_dict()
                    for row in rows
                    if row.candidate_kind in {"entity_candidate", "candidate_alias_group"}
                ],
                unresolved_or_ambiguous_tags=unresolved,
                original_work_status=original_status,
                eligibility={
                    "future_local_source_hint_eligible": bool(record.eligible_for_future_local_source_hint),
                    "future_entity_candidate_eligible": bool(record.eligible_for_future_entity_candidate),
                    "db_write_allowed": False,
                },
                confidence=confidence,
                reasons=reasons,
                db_write_allowed=False,
            )
        )
        all_rows.extend(rows)
    return normalized_records, all_rows


def build_media_summaries(
    normalized_records: Sequence[PixivNormalizedMetadataCandidate],
    rows: Sequence[PixivCandidateRow],
) -> list[dict[str, Any]]:
    rows_by_record: dict[tuple[str | None, int | None, int | None], list[PixivCandidateRow]] = defaultdict(list)
    for row in rows:
        key = (row.work_id_private, row.page_index, row.media_id_private)
        rows_by_record[key].append(row)

    summaries: list[dict[str, Any]] = []
    for record in normalized_records:
        key = (record.work_id_private, record.page_index, record.media_id_private)
        record_rows = rows_by_record.get(key, [])
        namespace_counts = Counter(row.candidate_namespace for row in record_rows)
        kind_counts = Counter(row.candidate_kind for row in record_rows)
        summaries.append(
            {
                "media_id": record.media_id_private,
                "work_id": record.work_id_private,
                "page_index": record.page_index,
                "title": record.title_private,
                "artist_name": record.artist_name_private,
                "artist_id": record.artist_id_private,
                "tag_count": len(record.raw_pixiv_tags),
                "artist_present": bool(record.artist_name_private or record.artist_id_private),
                "title_present": bool(record.title_private),
                "page_count_present": record.page_count is not None,
                "likely_original": record.original_work_status,
                "copyright_candidates_count": namespace_counts.get("copyright", 0),
                "character_candidates_count": namespace_counts.get("character", 0),
                "general_candidates_count": namespace_counts.get("general", 0),
                "ambiguous_candidates_count": namespace_counts.get("ambiguous", 0),
                "sensitive_meta_candidates_count": namespace_counts.get("meta", 0),
                "unresolved_candidates_count": kind_counts.get("unknown_or_unresolved_pixiv_tag", 0),
                "requires_manual_review_count": sum(1 for row in record_rows if row.requires_manual_review),
                "db_write_allowed": False,
            }
        )
    return summaries


def candidate_distribution(rows: Sequence[PixivCandidateRow]) -> dict[str, Any]:
    return {
        "candidate_kind_counts": dict(sorted(Counter(row.candidate_kind for row in rows).items())),
        "candidate_namespace_counts": dict(sorted(Counter(row.candidate_namespace for row in rows).items())),
        "candidate_reason_counts": dict(sorted(Counter(row.reason for row in rows).items())),
        "lookup_source_counts": dict(sorted(Counter(row.lookup_source or "none" for row in rows).items())),
        "manual_review_required_count": sum(1 for row in rows if row.requires_manual_review),
        "db_write_allowed_count": sum(1 for row in rows if row.db_write_allowed),
    }


def field_availability(records: Sequence[f2.PixivGalleryDlAdapterRecord]) -> dict[str, int]:
    return f2.field_availability(records)


def output_containment_summary(output_dir: Path, *, private_paths: Sequence[Path]) -> dict[str, Any]:
    root = resolve_repo_path(output_dir)
    try:
        f1.require_under_path(root, ROOT / ".local_manifests", code="f3_output_path_violation")
        if PHASE_SLUG not in root.as_posix():
            raise OutputPathError("f3_output_path_violation")
        for path in private_paths:
            require_under_phase_output(resolve_repo_path(path))
    except (f1.OutputPathError, OutputPathError) as exc:
        raise OutputPathError(str(exc)) from exc
    return {
        "phase_output_root": ".local_manifests/<phase-private-root>",
        "private_artifacts_under_phase_root": True,
        "public_reports_under_docs_reports": True,
        "gitignored_private_artifacts": True,
        "output_path_violation": False,
    }


def validate_private_output_paths_before_effects(output_dir: Path, *, private_paths: Sequence[Path]) -> None:
    output_containment_summary(output_dir, private_paths=private_paths)


def current_run_raw_scope_summary(raw_dir: Path, accepted_raw_files: Sequence[str | Path]) -> dict[str, Any]:
    root = resolve_repo_path(raw_dir)
    accepted = {resolve_repo_path(path) for path in accepted_raw_files if path}
    all_raw_files: set[Path] = set()
    if root.exists():
        all_raw_files = {
            path.resolve()
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in f1.JSON_EXTENSIONS
        }
    stale = sorted(all_raw_files - accepted)
    return {
        "raw_input_scope": "current_run_only",
        "current_run_raw_file_count": len(accepted),
        "stale_raw_files_ignored_count": len(stale),
        "stale_raw_files_private": [_rel(path) for path in stale],
    }


def command_summary(results: Sequence[CommandResult], *, max_record_limit: int) -> dict[str, Any]:
    return {
        "metadata_command_template": GALLERY_DL_METADATA_COMMAND_TEMPLATE,
        "metadata_command_count": len(results),
        "metadata_success_count": sum(1 for result in results if result.success),
        "metadata_failure_count": sum(1 for result in results if not result.success),
        "metadata_auth_or_config_failure_count": sum(1 for result in results if result.error_is_auth_or_config),
        "blocked_over_limit_count": sum(1 for result in results if result.blocked_over_limit),
        "max_record_limit": max_record_limit,
        "record_cap_enforced_before_accepted_raw_write": True,
        "exact_commands_private_only": True,
        "subprocess_uses_shell": False,
        "reference_download_enabled": False,
        "per_item_results_public": [result.public_dict() for result in results],
    }


def tag_category_lookup_cache_summary(
    *,
    db_enabled: bool,
    table_available: bool,
    cache_migration_ran: bool,
    cache_writes_enabled: bool,
    lookup_summary: Mapping[str, Any],
) -> dict[str, Any]:
    unique_tag_count = int(lookup_summary.get("unique_normalized_tag_count", 0) or 0)
    if not db_enabled:
        mode = "db_skipped"
    elif not table_available:
        mode = "db_cache_unavailable"
    elif unique_tag_count == 0:
        mode = "db_cache_skipped_no_tags"
    elif cache_writes_enabled:
        mode = "db_cache_enabled"
    else:
        mode = "db_cache_read_only"
    return {
        "table": "blombooru_external_tag_category_lookup_cache",
        "table_available": table_available,
        "cache_migration_ran": cache_migration_ran,
        "cache_write_mode": mode,
        "cache_write_enabled": bool(cache_writes_enabled and db_enabled and table_available),
        "cache_write_count": int(lookup_summary.get("cache_write_count", 0) or 0),
        "cache_hit_count": int(lookup_summary.get("cache_hit_count", 0) or 0),
        "cache_miss_count": int(lookup_summary.get("cache_miss_count", 0) or 0),
        "truth_table_write_count": 0,
        "provider_cache_write_count": 0,
        "entity_evidence_write_count": 0,
        "media_entity_candidate_write_count": 0,
        "confirmed_assignment_write_count": 0,
    }


def classification_improvement_summary(
    *,
    candidate_rows: Sequence[PixivCandidateRow],
    lookup_summary: Mapping[str, Any],
) -> dict[str, Any]:
    namespace_counts = Counter(row.candidate_namespace for row in candidate_rows)
    ambiguous_unknown = namespace_counts.get("ambiguous", 0) + namespace_counts.get("unknown", 0)
    copyright_count = namespace_counts.get("copyright", 0)
    character_count = namespace_counts.get("character", 0)
    return {
        "baseline_previous_f3": dict(PREVIOUS_F3_BASELINE),
        "current_ambiguous_unknown_candidate_count": ambiguous_unknown,
        "current_copyright_series_candidate_count": copyright_count,
        "current_character_candidate_count": character_count,
        "ambiguous_unknown_delta_vs_previous_f3": ambiguous_unknown
        - int(PREVIOUS_F3_BASELINE["ambiguous_unknown_candidate_count"]),
        "copyright_series_delta_vs_previous_f3": copyright_count
        - int(PREVIOUS_F3_BASELINE["copyright_series_candidate_count"]),
        "character_delta_vs_previous_f3": character_count
        - int(PREVIOUS_F3_BASELINE["character_candidate_count"]),
        "unique_tag_lookup_hit_count": int(lookup_summary.get("hit_count", 0) or 0),
        "unique_tag_lookup_total_count": int(lookup_summary.get("unique_normalized_tag_count", 0) or 0),
    }


def unresolved_reason_buckets(rows: Sequence[PixivCandidateRow]) -> dict[str, int]:
    unresolved_rows = [
        row
        for row in rows
        if row.candidate_namespace in {"ambiguous", "unknown"}
        or row.candidate_kind in {"unknown_or_unresolved_pixiv_tag", "ambiguous_proper_noun_candidate"}
    ]
    return dict(sorted(Counter(row.reason for row in unresolved_rows).items()))


def parenthetical_pattern_summary(rows: Sequence[PixivCandidateRow]) -> dict[str, Any]:
    pattern_rows = [row for row in rows if row.reason == "pixiv_parenthetical_character_work_pattern"]
    namespace_counts = Counter(row.candidate_namespace for row in pattern_rows)
    return {
        "parenthetical_candidate_count": len(pattern_rows),
        "parenthetical_character_candidate_count": namespace_counts.get("character", 0),
        "parenthetical_copyright_candidate_count": namespace_counts.get("copyright", 0),
        "parenthetical_resolved_count": len(pattern_rows),
        "parenthetical_confirmed_assignment_count": 0,
    }


def coverage_target_summary(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    rows: Sequence[PixivCandidateRow],
    lookup_summary: Mapping[str, Any],
) -> dict[str, Any]:
    unique_keys = {multilingual_normalize_tag(tag).canonical_lookup_key for record in records for tag in record.tags}
    unique_keys.discard("")
    resolved_sources = {
        "local_db_read_only",
        DANBOORU_TAGS_SOURCE,
        DANBOORU_ALIAS_SOURCE,
        SAFEBOORU_TAGS_SOURCE,
        EXTERNAL_TAG_LOOKUP_SOURCE,
        "pixiv_parenthetical_pattern",
    }
    resolved_keys = {
        row.canonical_lookup_key or row.lookup_key or lookup_key(row.normalized_tag)
        for row in rows
        if (row.lookup_source in resolved_sources or row.reason == "pixiv_parenthetical_character_work_pattern")
        and row.candidate_namespace not in {"ambiguous", "unknown"}
    }
    resolved_keys &= unique_keys
    unique_coverage = len(resolved_keys) / max(len(unique_keys), 1)

    namespace_counts = Counter(row.candidate_namespace for row in rows)
    ambiguous_unknown = namespace_counts.get("ambiguous", 0) + namespace_counts.get("unknown", 0)
    ambiguous_reduction = (
        (int(PREVIOUS_F3_BASELINE["ambiguous_unknown_candidate_count"]) - ambiguous_unknown)
        / max(int(PREVIOUS_F3_BASELINE["ambiguous_unknown_candidate_count"]), 1)
    )

    high_value_keys = {
        multilingual_normalize_tag(tag).canonical_lookup_key
        for record in records
        for tag in record.tags
        if _looks_like_proper_noun_candidate(normalize_unicode_tag(tag))
    }
    high_value_keys.discard("")
    high_value_resolved = {
        key
        for key in high_value_keys
        if any(
            (row.canonical_lookup_key or row.lookup_key or lookup_key(row.normalized_tag)) == key
            and row.candidate_namespace in {"artist", "copyright", "character", "general", "meta"}
            and (
                row.lookup_source in resolved_sources
                or row.reason == "pixiv_parenthetical_character_work_pattern"
            )
            for row in rows
        )
    }
    high_value_resolution_rate = len(high_value_resolved) / max(len(high_value_keys), 1)

    target_reached = (
        unique_coverage >= 0.6
        or ambiguous_reduction >= 0.6
        or high_value_resolution_rate >= 0.8
    )
    attempted = list(lookup_summary.get("lookup_sources_attempted") or [])
    lookup_limit = int(lookup_summary.get("lookup_limit") or 0)
    lookup_limit_covers_all_unique_tags = lookup_limit >= len(unique_keys)
    lookup_sources_exhausted = all(
        source in attempted for source in (DANBOORU_TAGS_SOURCE, DANBOORU_ALIAS_SOURCE, SAFEBOORU_TAGS_SOURCE)
    )
    lookup_error_count = int(lookup_summary.get("lookup_error_count") or 0)
    source_lookup_error_counts = dict(lookup_summary.get("source_lookup_error_counts") or {})
    provider_blocked = bool(lookup_summary.get("provider_blocked"))
    request_budget_exhausted = bool(lookup_summary.get("request_budget_exhausted"))
    lookup_sources_successfully_exhausted = bool(
        lookup_sources_exhausted
        and not provider_blocked
        and not request_budget_exhausted
        and lookup_error_count == 0
        and not source_lookup_error_counts
    )
    automated_paths_exhausted = bool(lookup_limit_covers_all_unique_tags and lookup_sources_successfully_exhausted)
    if target_reached:
        target_status = "reached"
    elif automated_paths_exhausted:
        target_status = "not_reached_after_exhaustion"
    elif lookup_limit_covers_all_unique_tags:
        target_status = "not_reached_current_iteration"
    else:
        target_status = "not_reached_bounded_lookup_cap"
    return {
        "target_status": target_status,
        "unique_tag_count": len(unique_keys),
        "lookup_limit": lookup_limit,
        "lookup_limit_covers_all_unique_tags": lookup_limit_covers_all_unique_tags,
        "lookup_sources_successfully_exhausted": lookup_sources_successfully_exhausted,
        "provider_blocked": provider_blocked,
        "lookup_error_count": lookup_error_count,
        "request_budget_exhausted": request_budget_exhausted,
        "automated_paths_exhausted": automated_paths_exhausted,
        "resolved_unique_tag_count": len(resolved_keys),
        "resolved_unique_tag_coverage": round(unique_coverage, 4),
        "ambiguous_unknown_reduction_vs_original_f3": round(ambiguous_reduction, 4),
        "high_value_proper_noun_like_tag_count": len(high_value_keys),
        "high_value_proper_noun_like_resolved_count": len(high_value_resolved),
        "high_value_proper_noun_like_resolution_rate": round(high_value_resolution_rate, 4),
        "coverage_target_thresholds": {
            "unique_tag_coverage": 0.6,
            "ambiguous_unknown_reduction": 0.6,
            "high_value_resolution": 0.8,
        },
        "automated_paths_attempted": [
            "local_db_read_only",
            *attempted,
            "pixiv_parenthetical_pattern_parser",
            "deterministic_fallback",
        ],
        "additional_source_evaluation": {
            "gelbooru_dapi": "rejected_for_this_pilot_live_probe_returned_http_401_without_credentials",
            "safebooru_dapi": "implemented_public_xml_tag_type_lookup",
        },
    }


def future_route_recommendation(
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    normalized_records: Sequence[PixivNormalizedMetadataCandidate],
    rows: Sequence[PixivCandidateRow],
    *,
    entrypoint: GalleryDlEntrypoint,
    join_summary: Mapping[str, Any],
    lookup_summary: Mapping[str, Any],
    db_cache_summary: Mapping[str, Any],
) -> dict[str, str]:
    if not records:
        return {
            "decision": "E_stop_or_reroute",
            "reason": "gallery-dl did not produce usable metadata records for normalization",
            "db_persistence": "not_recommended",
            "local_source_hint_pixiv_metadata": "not_recommended",
            "tag_classification_cache": "not_ready",
            "entity_candidate_persistence": "blocked",
        }
    status_counts = dict(join_summary.get("status_counts") or {})
    eligible_join_count = int(status_counts.get("metadata_matches_eligible_anime_local_prior", 0) or 0)
    command_ready = entrypoint.reproducibility_status in {
        "stable_project_python_module",
        "external_executable_discovered",
    }
    unique_tag_count = int(lookup_summary.get("unique_normalized_tag_count", 0) or 0)
    lookup_hit_count = int(lookup_summary.get("hit_count", 0) or 0)
    lookup_coverage = lookup_hit_count / max(unique_tag_count, 1)
    cache_available = bool(
        db_cache_summary.get("table_available")
        and db_cache_summary.get("cache_write_mode") in {"db_cache_enabled", "db_cache_skipped_no_tags"}
    )
    records_with_tags = sum(1 for record in records if record.tags)
    artist_coverage = sum(1 for record in records if record.artist_name or record.artist_id) / max(len(records), 1)
    unresolved = sum(1 for row in rows if row.candidate_kind in {"unknown_or_unresolved_pixiv_tag", "ambiguous_proper_noun_candidate"})
    lookup_coverage_ready = lookup_coverage >= 0.5
    if not command_ready or eligible_join_count <= 0 or not cache_available or not lookup_coverage_ready:
        blockers = []
        if not command_ready:
            blockers.append("gallery_dl_command_boundary_is_conditional_or_dry_run")
        if eligible_join_count <= 0:
            blockers.append("eligible_local_join_count_is_zero")
        if not cache_available:
            blockers.append("external_tag_category_lookup_cache_not_available")
        if not lookup_coverage_ready:
            blockers.append("automated_category_lookup_coverage_below_threshold")
        return {
            "decision": "taxonomy_alias_knowledge_base_stage_required",
            "reason": ",".join(blockers),
            "db_persistence": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "local_source_hint_pixiv_metadata": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "tag_classification_cache": "multisource_lookup_cache_proof_completed_coverage_not_reached"
            if cache_available
            else "needs_cache_availability",
            "entity_candidate_persistence": "blocked_until_category_coverage_and_provenance_improve",
        }
    if records_with_tags and artist_coverage >= 0.8 and lookup_coverage >= 0.5:
        return {
            "decision": "A_proceed_to_LocalSourceHint_PixivMetadata_persistence_design",
            "reason": "metadata fields are rich, eligible local joins exist, gallery-dl command boundary is reproducible, and automated tag category lookup/cache coverage passed the persistence-design gate",
            "db_persistence": "LocalSourceHint_and_PixivMetadata_design_may_be_next_cache_table_can_be_promoted_with_review_EntityCandidate_should_wait",
            "local_source_hint_pixiv_metadata": "recommended_for_design_only",
            "tag_classification_cache": "ready_for_persistence_design_review",
            "entity_candidate_persistence": "blocked_until_future_candidate_lifecycle_design",
        }
    if records_with_tags and normalized_records and unresolved < len(rows) and lookup_coverage >= 0.25:
        return {
            "decision": "taxonomy_alias_knowledge_base_stage_required",
            "reason": "automated lookup classified a subset, but coverage is below the persistence-design threshold",
            "db_persistence": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "local_source_hint_pixiv_metadata": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "tag_classification_cache": "multisource_lookup_cache_proof_completed_coverage_not_reached",
            "entity_candidate_persistence": "blocked_until_category_coverage_and_provenance_improve",
        }
    if records_with_tags:
        return {
            "decision": "taxonomy_alias_knowledge_base_stage_required",
            "reason": "raw tags are available and safely preserved, but automated category lookup coverage is too low for persistence design",
            "db_persistence": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "local_source_hint_pixiv_metadata": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet",
            "tag_classification_cache": "multisource_lookup_cache_proof_completed_coverage_not_reached",
            "entity_candidate_persistence": "blocked_until_category_coverage_and_provenance_improve",
        }
    return {
        "decision": "E_stop_or_reroute",
        "reason": "Pixiv metadata did not provide enough raw tags for the middleware route",
        "db_persistence": "not_recommended",
        "local_source_hint_pixiv_metadata": "not_recommended",
        "tag_classification_cache": "not_ready",
        "entity_candidate_persistence": "blocked",
    }


def _private_markers(records: Sequence[f2.PixivGalleryDlAdapterRecord], samples: Sequence[SelectedSample]) -> list[str]:
    markers: set[str] = set()
    for sample in samples:
        markers.add(sample.work_id)
        markers.update(sample.local_basenames_private)
    for record in records:
        for value in (
            record.work_id,
            record.canonical_url,
            record.gallery_dl_filename,
        ):
            if value not in (None, ""):
                markers.add(str(value))
    return sorted(markers, key=len, reverse=True)


def _git_context(*, pr_number: int | None = None, pr_head_sha: str | None = None) -> dict[str, Any]:
    return f2._git_context(pr_number=pr_number, pr_head_sha=pr_head_sha)


def build_public_summary(
    *,
    generated_at: str,
    pr_context: Mapping[str, Any],
    git_context: Mapping[str, Any],
    entrypoint: GalleryDlEntrypoint,
    db_identity: Mapping[str, Any] | None,
    sample_public: Mapping[str, Any],
    parse_result: f1.ParseResult,
    records: Sequence[f2.PixivGalleryDlAdapterRecord],
    normalized_records: Sequence[PixivNormalizedMetadataCandidate],
    candidate_rows: Sequence[PixivCandidateRow],
    media_summaries: Sequence[Mapping[str, Any]],
    join_summary: Mapping[str, Any],
    command_public: Mapping[str, Any],
    raw_scope: Mapping[str, Any],
    local_index_summary: Mapping[str, Any],
    lookup_summary: Mapping[str, Any],
    db_cache_summary: Mapping[str, Any],
    containment: Mapping[str, Any],
    manual_review_guide: Path,
) -> dict[str, Any]:
    richness_counts = Counter(record.metadata_richness for record in records)
    original_counts = Counter(record.original_work_status for record in normalized_records)
    candidate_dist = candidate_distribution(candidate_rows)
    namespace_counts = Counter(row.candidate_namespace for row in candidate_rows)
    kind_counts = Counter(row.candidate_kind for row in candidate_rows)
    lookup_counts = Counter(row.lookup_source or "none" for row in candidate_rows)
    total_tag_occurrences = sum(len(record.tags) for record in records)
    coverage_summary = coverage_target_summary(records, candidate_rows, lookup_summary)
    public = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": generated_at,
        "artifact_lifecycle": "phase_scoped_operational_runner",
        "why_this_stage_exists": (
            "PR #89 and F2 validated real gallery-dl metadata retrieval and local Pixiv filename-prior "
            "correspondence. F3 tests whether those raw Pixiv metadata fields can become a structured, "
            "tag/entity candidate middleware view backed by automated category lookup/cache evidence without "
            "treating raw Pixiv tags as confirmed truth."
        ),
        "pr89_merge_confirmation": dict(pr_context),
        "git_context": dict(git_context),
        "gallery_dl_command_mode": entrypoint.public_dict(),
        "db_identity": dict(db_identity or {"db_read": False}),
        "sample_selection": dict(sample_public),
        "sample_gate": {
            "default_sample_size": DEFAULT_SAMPLE_SIZE,
            "max_sample_size_without_renewed_approval": MAX_SAMPLE_SIZE,
            "max_normalized_media_page_records_without_renewed_approval": MAX_RECORDS_WITHOUT_RENEWED_APPROVAL,
            "enforced_before_local_join": True,
            "enforced_before_private_mapping_artifacts": True,
            "status": "passed",
        },
        "command_summary": dict(command_public),
        "input_summary": {
            "raw_file_count": len(parse_result.files),
            "current_run_raw_file_count": raw_scope.get("current_run_raw_file_count", len(parse_result.files)),
            "stale_raw_files_ignored_count": raw_scope.get("stale_raw_files_ignored_count", 0),
            "raw_input_scope": raw_scope.get("raw_input_scope", "current_run_only"),
            "raw_record_count": len(parse_result.records),
            "raw_event_count": parse_result.raw_event_count,
            "directory_context_event_count": parse_result.directory_context_event_count,
            "url_media_event_count": parse_result.url_media_event_count,
            "normalized_media_record_count": len(records),
            "normalized_candidate_media_record_count": len(normalized_records),
            "invalid_json_count": parse_result.invalid_json_count,
            "skipped_invalid_count": parse_result.skipped_invalid_count,
            "unsupported_shape_count": parse_result.unsupported_shape_count,
        },
        "metadata_richness_summary": {
            "schema_field_availability": field_availability(records),
            "metadata_richness_distribution": dict(sorted(richness_counts.items())),
            "artist_candidate_coverage_count": namespace_counts.get("artist", 0),
            "records_with_title": sum(1 for record in records if record.title),
            "records_with_page_count": sum(1 for record in records if record.page_count is not None),
        },
        "raw_pixiv_tag_availability_summary": {
            "records_with_raw_pixiv_tags": sum(1 for record in records if record.tags),
            "total_raw_pixiv_tag_occurrences": total_tag_occurrences,
            "normalized_unicode_tag_occurrences": total_tag_occurrences,
            "raw_tag_order_preserved": True,
            "exact_raw_tags_public": False,
        },
        "local_source_prior_join": dict(join_summary),
        "classification_middleware": {
            "normalized_dto": "PixivNormalizedMetadataCandidate",
            "candidate_row_count": len(candidate_rows),
            "candidate_classification_distribution": candidate_dist,
            "lookup_source_coverage": {
                "local_db_read_only": lookup_counts.get("local_db_read_only", 0),
                "external_category_lookup_sources": list(DEFAULT_LOOKUP_SOURCES),
                "source_candidate_counts": {
                    source: lookup_counts.get(source, 0)
                    for source in DEFAULT_LOOKUP_SOURCES
                    if lookup_counts.get(source, 0)
                },
                "legacy_source_candidate_count": lookup_counts.get(EXTERNAL_TAG_LOOKUP_SOURCE, 0),
                "deterministic_fallback": lookup_counts.get("deterministic_fallback", 0),
                LOOKUP_SOURCE_EXTERNAL_DISABLED: lookup_counts.get(LOOKUP_SOURCE_EXTERNAL_DISABLED, 0),
                "external_category_lookup_enabled": True,
                "external_category_lookup_public_docs": {
                    "danbooru": EXTERNAL_TAG_LOOKUP_DOC_URL,
                    "safebooru": SAFEBOORU_TAG_LOOKUP_BASE_URL,
                },
            },
            "artist_candidate_count": namespace_counts.get("artist", 0),
            "copyright_series_candidate_count": namespace_counts.get("copyright", 0),
            "character_candidate_count": namespace_counts.get("character", 0),
            "general_descriptive_tag_candidate_count": namespace_counts.get("general", 0),
            "ambiguous_unknown_candidate_count": namespace_counts.get("ambiguous", 0)
            + namespace_counts.get("unknown", 0),
            "sensitive_meta_candidate_count": namespace_counts.get("meta", 0),
            "alias_group_candidate_count": kind_counts.get("candidate_alias_group", 0),
            "confirmed_assignment_count": 0,
            "automatic_entity_count": 0,
            "db_write_allowed_count": sum(1 for row in candidate_rows if row.db_write_allowed),
        },
        "automated_tag_category_lookup": dict(lookup_summary),
        "tag_category_lookup_cache": dict(db_cache_summary),
        "classification_improvement_vs_previous_f3": classification_improvement_summary(
            candidate_rows=candidate_rows,
            lookup_summary=lookup_summary,
        ),
        "before_after_comparison": {
            "original_f3_baseline": dict(PREVIOUS_F3_BASELINE),
            "previous_danbooru_only_f3b": dict(PREVIOUS_DANBOORU_ONLY_F3B),
            "current_multisource_pattern_f3b": {
                "ambiguous_unknown_candidate_count": namespace_counts.get("ambiguous", 0)
                + namespace_counts.get("unknown", 0),
                "copyright_series_candidate_count": namespace_counts.get("copyright", 0),
                "character_candidate_count": namespace_counts.get("character", 0),
                "unique_tag_lookup_hit_count": int(lookup_summary.get("hit_count", 0) or 0),
                "unique_tag_lookup_total_count": int(lookup_summary.get("unique_normalized_tag_count", 0) or 0),
            },
        },
        "coverage_target": coverage_summary,
        "unresolved_reason_buckets": unresolved_reason_buckets(candidate_rows),
        "parenthetical_pattern_summary": parenthetical_pattern_summary(candidate_rows),
        "local_classification_index": dict(local_index_summary),
        "original_unknown_handling": {
            "original_work_status_distribution": dict(sorted(original_counts.items())),
            "records_without_forced_copyright": sum(
                1
                for record in normalized_records
                if record.original_work_status != "known_or_candidate_work_context"
            ),
            "raw_pixiv_original_tag_is_not_confirmed_truth": True,
        },
        "manual_review_needs": {
            "candidate_rows_requiring_manual_review": sum(1 for row in candidate_rows if row.requires_manual_review),
            "media_records_requiring_manual_review": sum(
                1 for summary in media_summaries if int(summary.get("requires_manual_review_count", 0)) > 0
            ),
            "private_manual_review_guide": _rel(resolve_repo_path(manual_review_guide)),
            "manual_review_is_sparse_correction_oriented": True,
        },
        "output_containment": dict(containment),
        "future_route_recommendation": future_route_recommendation(
            records,
            normalized_records,
            candidate_rows,
            entrypoint=entrypoint,
            join_summary=join_summary,
            lookup_summary=lookup_summary,
            db_cache_summary=db_cache_summary,
        ),
        "privacy_and_safety_confirmation": {
            "public_report_contains_exact_pixiv_ids": False,
            "public_report_contains_exact_media_ids": False,
            "public_report_contains_exact_local_filenames": False,
            "public_report_contains_exact_local_paths": False,
            "public_report_contains_raw_gallery_dl_json": False,
            "public_report_contains_raw_image_urls": False,
            "public_report_contains_raw_pixiv_tags": False,
            "db_write": bool(db_cache_summary.get("cache_write_count", 0)),
            "db_write_limited_to_external_tag_category_lookup_cache": True,
            "db_migration": bool(db_cache_summary.get("cache_migration_ran")),
            "db_migration_limited_to_external_tag_category_lookup_cache": True,
            "local_source_hint_write": False,
            "provider_cache_write": False,
            "external_tag_category_lookup_cache_write": int(db_cache_summary.get("cache_write_count", 0)),
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "negative_lookup_cache_write": False,
            "confirmed_assignment": False,
            "automatic_entity": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "localization_execution": False,
            "entity_resolver": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "reference_download": False,
            "image_download": False,
            "broad_gallery_dl_run": False,
            "full_library_gallery_dl_run": False,
            "local_manifests_committed": False,
            "push_main": False,
            "merge": False,
            "next_phase_started": False,
        },
    }
    f1.assert_public_payload_safe(public, private_markers=[])
    return public


def build_markdown_report(summary: Mapping[str, Any], *, private_markers: Iterable[str]) -> str:
    lines = [
        f"# Phase {PHASE} - {TITLE}",
        "",
        "## Why This Stage Exists",
        "",
        str(summary["why_this_stage_exists"]),
        "",
        "## PR #89 Merge Confirmation",
        "",
        f"- PR #89 state: `{summary['pr89_merge_confirmation'].get('state')}`.",
        f"- PR #89 merged at: `{summary['pr89_merge_confirmation'].get('merged_at')}`.",
        f"- PR #89 merge commit: `{summary['pr89_merge_confirmation'].get('merge_commit')}`.",
        f"- PR #89 URL: `{summary['pr89_merge_confirmation'].get('url')}`.",
        "",
        "## Sample / Record Scope",
        "",
        f"- Sample selection: `{json.dumps(summary['sample_selection'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Input summary: `{json.dumps(summary['input_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## gallery-dl Command Mode / Version",
        "",
        f"- Mode: `{summary['gallery_dl_command_mode'].get('mode')}`.",
        f"- Version: `{summary['gallery_dl_command_mode'].get('version')}`.",
        f"- Reproducibility status: `{summary['gallery_dl_command_mode'].get('reproducibility_status')}`.",
        f"- Command label: `{summary['gallery_dl_command_mode'].get('command_path_public')}`.",
        "",
        "## Metadata Richness",
        "",
        f"- Metadata richness: `{json.dumps(summary['metadata_richness_summary'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Raw Pixiv tag availability: `{json.dumps(summary['raw_pixiv_tag_availability_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Candidate Classification",
        "",
        f"- Distribution: `{json.dumps(summary['classification_middleware']['candidate_classification_distribution'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Artist candidates: `{summary['classification_middleware']['artist_candidate_count']}`.",
        f"- Copyright/series candidates: `{summary['classification_middleware']['copyright_series_candidate_count']}`.",
        f"- Character candidates: `{summary['classification_middleware']['character_candidate_count']}`.",
        f"- General descriptive candidates: `{summary['classification_middleware']['general_descriptive_tag_candidate_count']}`.",
        f"- Ambiguous/unknown candidates: `{summary['classification_middleware']['ambiguous_unknown_candidate_count']}`.",
        f"- Sensitive/meta candidates: `{summary['classification_middleware']['sensitive_meta_candidate_count']}`.",
        f"- Alias-group candidates: `{summary['classification_middleware']['alias_group_candidate_count']}`.",
        f"- Lookup source coverage: `{json.dumps(summary['classification_middleware']['lookup_source_coverage'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Automated category lookup: `{json.dumps(summary['automated_tag_category_lookup'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Classification improvement vs previous F3: `{json.dumps(summary['classification_improvement_vs_previous_f3'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Before/after comparison: `{json.dumps(summary['before_after_comparison'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Coverage target: `{json.dumps(summary['coverage_target'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Unresolved reason buckets: `{json.dumps(summary['unresolved_reason_buckets'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Parenthetical pattern summary: `{json.dumps(summary['parenthetical_pattern_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Lookup Cache",
        "",
        f"- Cache/mapping table: `{json.dumps(summary['tag_category_lookup_cache'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Original / Ambiguous Handling",
        "",
        f"- Original/unknown handling: `{json.dumps(summary['original_unknown_handling'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Manual Review Needs",
        "",
        f"- Manual review: `{json.dumps(summary['manual_review_needs'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Output Containment",
        "",
        f"- Containment: `{json.dumps(summary['output_containment'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Recommended Next Phase",
        "",
        f"- Decision: `{summary['future_route_recommendation']['decision']}`.",
        f"- Reason: `{summary['future_route_recommendation']['reason']}`.",
        f"- DB persistence: `{summary['future_route_recommendation']['db_persistence']}`.",
        "",
        "## Safety Confirmation",
        "",
    ]
    for key, value in summary["privacy_and_safety_confirmation"].items():
        lines.append(f"- `{key}`: `{value}`.")
    lines.append("")
    report = "\n".join(lines)
    f1.assert_public_payload_safe(report, private_markers=private_markers)
    return report


def _candidate_csv(rows: Sequence[PixivCandidateRow]) -> str:
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(asdict(PixivCandidateRow(None, None, None, None, None, None, "", "", "", "", 0.0, "")).keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_private_dict())
    return buffer.getvalue()


def _lookup_cache_csv(results: Mapping[str, ExternalTagLookupResult]) -> str:
    rows = [result.to_private_dict() for result in results.values()]
    fieldnames = [
        "raw_tag",
        "normalized_tag",
        "lookup_source",
        "source_tag_id",
        "source_tag_name",
        "source_category_raw",
        "mapped_candidate_namespace",
        "confidence",
        "provenance_url_or_key",
        "status",
        "cache_status",
        "lookup_error",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    return buffer.getvalue()


def _rows_markdown(rows: Sequence[Mapping[str, Any]], *, max_rows: int = 200) -> str:
    if not rows:
        return "_No rows._\n"
    fieldnames = list(rows[0].keys())
    lines = [
        "| " + " | ".join(fieldnames) + " |",
        "| " + " | ".join("---" for _ in fieldnames) + " |",
    ]
    for row in rows[:max_rows]:
        values = [str(row.get(field, "")).replace("|", "\\|").replace("\n", " ") for field in fieldnames]
        lines.append("| " + " | ".join(values) + " |")
    if len(rows) > max_rows:
        lines.append("")
        lines.append(f"_Truncated to {max_rows} rows; see CSV/details JSON for full private output._")
    lines.append("")
    return "\n".join(lines)


def build_media_summary_csv(media_summaries: Sequence[Mapping[str, Any]]) -> str:
    fieldnames = [
        "media_id",
        "work_id",
        "page_index",
        "title",
        "artist_name",
        "artist_id",
        "tag_count",
        "artist_present",
        "title_present",
        "page_count_present",
        "likely_original",
        "copyright_candidates_count",
        "character_candidates_count",
        "general_candidates_count",
        "ambiguous_candidates_count",
        "sensitive_meta_candidates_count",
        "unresolved_candidates_count",
        "requires_manual_review_count",
        "db_write_allowed",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in media_summaries:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    return buffer.getvalue()


def build_manual_review_guide() -> str:
    return "\n".join(
        [
            "# Phase 4.4-P2R-F3 私有人工复核指南",
            "",
            "本文件是 ignored private artifact，只用于操作者抽样复核，不应提交。",
            "",
            "## 先看 media-summary",
            "",
            "- `media-summary.csv` 每行对应一个本地匹配的 Pixiv media/page record。",
            "- 先按 `likely_original`、`requires_manual_review_count` 和各类候选数量排序。",
            "- 如果 `copyright_candidates_count` 和 `character_candidates_count` 都是 0，不要强行补版权；按 original/unknown 处理。",
            "",
            "## 再看 tag-candidates",
            "",
            "- `raw_tag` 是 Pixiv 原始 tag，`normalized_tag` 是 Unicode NFKC/空白归一化后的 tag。",
            "- `candidate_kind=tag_candidate` 表示普通 tag 建议，不是 entity truth。",
            "- `sensitive_or_meta_tag_candidate` 不应进入 EntityCandidate。",
            "- `ambiguous_proper_noun_candidate` 和 `unknown_or_unresolved_pixiv_tag` 需要人工或后续外部 category evidence。",
            "",
            "## 再看 entity-candidates",
            "",
            "- `pixiv_user_metadata` 产生 artist candidate，来源是 Pixiv user metadata field，不是 raw tag。",
            "- `existing_entity_or_alias_match` 只说明本地已有 entity/alias 命中；本阶段不创建、不更新、不确认 entity。",
            "- `candidate_alias_group` 只是候选 alias group，不是别名事实。",
            "",
            "## 如何判断分类",
            "",
            "- artist：优先检查 Pixiv user metadata 是否对应作者。",
            "- copyright/series：只有现有本地 entity/tag category 或未来 category lookup 支持时才可升级。",
            "- character：同上，不要凭 CodeX 或人工常识直接把 raw tag 当角色。",
            "- general：只能作为描述性 tag suggestion，不进入 EntityCandidate。",
            "- original：没有明确 series/copyright/character evidence 时保持 original_or_unknown，不强行造版权。",
            "",
            "## 回报给 ChatGPT",
            "",
            "请反馈：1) artist candidate 是否整体可靠；2) local DB/category 命中是否有明显误分；3) unresolved tags 是否主要是原作/别名/角色；4) 下一步应进入 LocalSourceHint 设计、再跑 no-DB pilot，还是先补 deterministic alias/category matching。",
            "",
        ]
    )


def _empty_parse_result(raw_dir: Path) -> f1.ParseResult:
    _ = raw_dir
    return f1.ParseResult(records=[], files=[])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    parser.add_argument("--tag-lookup-limit", type=int, default=DEFAULT_TAG_LOOKUP_LIMIT)
    parser.add_argument("--tag-lookup-delay-seconds", type=float, default=DEFAULT_TAG_LOOKUP_DELAY_SECONDS)
    parser.add_argument("--tag-lookup-timeout", type=int, default=DEFAULT_TAG_LOOKUP_TIMEOUT_SECONDS)
    parser.add_argument("--disable-db-cache-writes", action="store_true")
    parser.add_argument("--output-dir", default=str(PHASE_OUTPUT_DIR))
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
    parser.add_argument("--skip-network", action="store_true", help="Testing-only: select samples and write a dry report without network calls.")
    parser.add_argument("--dry-run", action="store_true", help="Select samples and show command templates without executing gallery-dl.")
    parser.add_argument("--no-db", action="store_true", help="Testing-only: skip DB identity and local prior sample selection.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--media-summary-csv", default=str(PRIVATE_MEDIA_SUMMARY_CSV))
    parser.add_argument("--media-summary-md", default=str(PRIVATE_MEDIA_SUMMARY_MD))
    parser.add_argument("--tag-candidates-csv", default=str(PRIVATE_TAG_CANDIDATES_CSV))
    parser.add_argument("--tag-candidates-md", default=str(PRIVATE_TAG_CANDIDATES_MD))
    parser.add_argument("--entity-candidates-csv", default=str(PRIVATE_ENTITY_CANDIDATES_CSV))
    parser.add_argument("--entity-candidates-md", default=str(PRIVATE_ENTITY_CANDIDATES_MD))
    parser.add_argument("--lookup-cache-csv", default=str(PRIVATE_LOOKUP_CACHE_CSV))
    parser.add_argument("--lookup-cache-md", default=str(PRIVATE_LOOKUP_CACHE_MD))
    parser.add_argument("--unresolved-tags-csv", default=str(PRIVATE_UNRESOLVED_TAGS_CSV))
    parser.add_argument("--unresolved-tags-md", default=str(PRIVATE_UNRESOLVED_TAGS_MD))
    parser.add_argument("--parenthetical-candidates-csv", default=str(PRIVATE_PARENTHETICAL_CANDIDATES_CSV))
    parser.add_argument("--parenthetical-candidates-md", default=str(PRIVATE_PARENTHETICAL_CANDIDATES_MD))
    parser.add_argument("--manual-review-guide", default=str(PRIVATE_MANUAL_REVIEW_GUIDE))
    parser.add_argument("--raw-dir", default=str(PRIVATE_RAW_DIR))
    parser.add_argument("--pr-number", type=int, default=0)
    parser.add_argument("--pr-head-sha", default="")
    parser.add_argument("--pr89-state", default="MERGED")
    parser.add_argument("--pr89-merged-at", default="2026-06-01T10:31:55Z")
    parser.add_argument("--pr89-merge-commit", default="1d123172fd0e064e38e0ffe01e13611aa8bcc8e6")
    parser.add_argument("--pr89-url", default="https://github.com/kyloris0660/VIOLET/pull/89")
    return parser


def entrypoint_for_args(args: argparse.Namespace) -> GalleryDlEntrypoint:
    if args.dry_run or args.skip_network:
        command = f2.split_operator_command(args.gallery_dl_command or "") if args.gallery_dl_command else ()
        return GalleryDlEntrypoint(
            mode="dry_run_no_gallery_dl_probe",
            command=command,
            version=None,
            available=False,
            reproducibility_status="dry_run_no_gallery_dl_probe",
        )
    return probe_gallery_dl_entrypoint(args.gallery_dl_command or None)


def cache_migration_allowed(args: argparse.Namespace) -> bool:
    return not (args.no_db or args.dry_run or args.skip_network)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = resolve_repo_path(args.output_dir)
    f1.require_under_path(output_dir, ROOT / ".local_manifests", code="f3_output_path_violation")
    if PHASE_SLUG not in output_dir.as_posix():
        raise OutputPathError("f3_output_path_violation")

    raw_dir = resolve_repo_path(args.raw_dir)
    details_json = resolve_repo_path(args.details_json)
    media_summary_csv = resolve_repo_path(args.media_summary_csv)
    media_summary_md = resolve_repo_path(args.media_summary_md)
    tag_candidates_csv = resolve_repo_path(args.tag_candidates_csv)
    tag_candidates_md = resolve_repo_path(args.tag_candidates_md)
    entity_candidates_csv = resolve_repo_path(args.entity_candidates_csv)
    entity_candidates_md = resolve_repo_path(args.entity_candidates_md)
    lookup_cache_csv = resolve_repo_path(args.lookup_cache_csv)
    lookup_cache_md = resolve_repo_path(args.lookup_cache_md)
    unresolved_tags_csv = resolve_repo_path(args.unresolved_tags_csv)
    unresolved_tags_md = resolve_repo_path(args.unresolved_tags_md)
    parenthetical_candidates_csv = resolve_repo_path(args.parenthetical_candidates_csv)
    parenthetical_candidates_md = resolve_repo_path(args.parenthetical_candidates_md)
    manual_review_guide = resolve_repo_path(args.manual_review_guide)

    private_paths = [
        details_json,
        media_summary_csv,
        media_summary_md,
        tag_candidates_csv,
        tag_candidates_md,
        entity_candidates_csv,
        entity_candidates_md,
        lookup_cache_csv,
        lookup_cache_md,
        unresolved_tags_csv,
        unresolved_tags_md,
        parenthetical_candidates_csv,
        parenthetical_candidates_md,
        manual_review_guide,
        raw_dir,
    ]
    validate_private_output_paths_before_effects(output_dir, private_paths=private_paths)
    output_dir.mkdir(parents=True, exist_ok=True)

    enforce_sample_size(args.sample_size)
    enforce_record_count(0, args.max_records)
    enforce_tag_lookup_limit(args.tag_lookup_limit)

    config = f2.load_project_config(ROOT)
    db_identity: dict[str, Any] | None = None
    prior_index: f1.LocalPriorIndex | None = None
    local_index = LocalClassificationIndex()
    db_cache_table_available = False
    cache_migration_ran = False
    samples: list[SelectedSample] = []
    sample_public: dict[str, Any]
    if args.no_db:
        sample_public = {
            "selected_count": 0,
            "requested_sample_size": args.sample_size,
            "sample_gate_status": "db_skipped",
            "exact_work_ids_public": False,
            "exact_media_ids_public": False,
            "exact_filenames_public": False,
        }
    else:
        engine = create_engine(config.database_url)
        if cache_migration_allowed(args):
            migrate_add_external_tag_category_lookup_cache(engine, inspect(engine))
            cache_migration_ran = True
        db_cache_table_available = (
            "blombooru_external_tag_category_lookup_cache" in inspect(engine).get_table_names()
        )
        install_external_lookup_cache_write_guard(engine)
        SessionLocal = sessionmaker(bind=engine)
        session: Session = SessionLocal()
        try:
            db_identity = f1.prove_db_identity(session, config)
            prior_index = f1.build_local_prior_index(session)
            local_index = build_local_classification_index(session)
            sample_public, samples = select_local_pixiv_prior_samples(prior_index, sample_size=args.sample_size)
        finally:
            session.close()
            engine.dispose()

    entrypoint = entrypoint_for_args(args)

    metadata_results: list[CommandResult] = []
    parse_result = _empty_parse_result(raw_dir)
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
        metadata_results, parse_result = run_metadata_commands(
            samples,
            entrypoint,
            raw_dir,
            max_records=args.max_records,
            timeout=args.timeout,
        )
        if any(result.blocked_over_limit for result in metadata_results):
            raise SampleGateError("sample_or_record_cap_exceeded")
        if metadata_results and not any(result.success for result in metadata_results):
            if any(result.error_is_auth_or_config for result in metadata_results):
                raise GalleryDlAuthBlocked("gallery_dl_auth_or_config_blocked")
        records = f2.normalize_adapter_records(parse_result, entrypoint=entrypoint)
        enforce_record_count(len(records), args.max_records)
        records, join_summary = f2.join_records_to_local_priors(records, prior_index)
        records = f2._finalize_joined_records(
            records,
            reference_download_enabled=False,
            downloaded_file_count=0,
        )

    lookup_results: dict[str, ExternalTagLookupResult] = {}
    lookup_summary = ExternalLookupSummary(
        enabled=bool(records),
        unique_normalized_tag_count=len(build_tag_lookup_inputs(records)),
        lookup_limit=args.tag_lookup_limit,
        lookup_delay_seconds=args.tag_lookup_delay_seconds,
        lookup_timeout_seconds=args.tag_lookup_timeout,
        cache_write_enabled=bool(
            records and not args.no_db and not args.disable_db_cache_writes and db_cache_table_available
        ),
    )
    if records:
        if args.no_db:
            lookup_results, lookup_summary = lookup_external_tag_categories(
                records,
                session=None,
                lookup_limit=args.tag_lookup_limit,
                delay_seconds=args.tag_lookup_delay_seconds,
                timeout_seconds=args.tag_lookup_timeout,
                cache_writes_enabled=False,
            )
        else:
            engine = create_engine(config.database_url)
            install_external_lookup_cache_write_guard(engine)
            SessionLocal = sessionmaker(bind=engine)
            session = SessionLocal()
            try:
                lookup_results, lookup_summary = lookup_external_tag_categories(
                    records,
                    session=session,
                    lookup_limit=args.tag_lookup_limit,
                    delay_seconds=args.tag_lookup_delay_seconds,
                    timeout_seconds=args.tag_lookup_timeout,
                    cache_writes_enabled=not args.disable_db_cache_writes,
                )
            finally:
                session.close()
                engine.dispose()

    lookup_summary_public = lookup_summary.public_dict()
    db_cache_public = tag_category_lookup_cache_summary(
        db_enabled=not args.no_db,
        table_available=db_cache_table_available,
        cache_migration_ran=cache_migration_ran,
        cache_writes_enabled=not args.disable_db_cache_writes,
        lookup_summary=lookup_summary_public,
    )

    normalized_records, candidate_rows = normalize_metadata_candidates(
        records,
        local_index,
        external_lookup_results=lookup_results,
    )
    media_summaries = build_media_summaries(normalized_records, candidate_rows)
    tag_rows = [
        row
        for row in candidate_rows
        if row.candidate_kind
        in {
            "tag_candidate",
            "sensitive_or_meta_tag_candidate",
            "ambiguous_proper_noun_candidate",
            "unknown_or_unresolved_pixiv_tag",
            "original_work_context",
        }
    ]
    entity_rows = [
        row
        for row in candidate_rows
        if row.candidate_kind in {"entity_candidate", "candidate_alias_group"}
    ]
    parenthetical_rows = [
        row for row in candidate_rows if row.reason == "pixiv_parenthetical_character_work_pattern"
    ]
    unresolved_rows = [
        row
        for row in candidate_rows
        if row.candidate_namespace in {"ambiguous", "unknown"}
        or row.candidate_kind in {"unknown_or_unresolved_pixiv_tag", "ambiguous_proper_noun_candidate"}
    ]

    successful_raw_files = [
        result.stdout_path_private
        for result in metadata_results
        if result.success and result.stdout_path_private
    ]
    raw_scope = current_run_raw_scope_summary(raw_dir, successful_raw_files)
    containment = output_containment_summary(
        output_dir,
        private_paths=private_paths,
    )
    public_command = command_summary(metadata_results, max_record_limit=args.max_records)
    generated_at = _now_iso()
    pr_context = {
        "number": 89,
        "state": args.pr89_state,
        "merged_at": args.pr89_merged_at,
        "merge_commit": args.pr89_merge_commit,
        "url": args.pr89_url,
    }
    summary = build_public_summary(
        generated_at=generated_at,
        pr_context=pr_context,
        git_context=_git_context(pr_number=args.pr_number or None, pr_head_sha=args.pr_head_sha or None),
        entrypoint=entrypoint,
        db_identity=db_identity,
        sample_public=sample_public,
        parse_result=parse_result,
        records=records,
        normalized_records=normalized_records,
        candidate_rows=candidate_rows,
        media_summaries=media_summaries,
        join_summary=join_summary,
        command_public=public_command,
        raw_scope=raw_scope,
        local_index_summary=local_index.public_summary(),
        lookup_summary=lookup_summary_public,
        db_cache_summary=db_cache_public,
        containment=containment,
        manual_review_guide=manual_review_guide,
    )
    markers = _private_markers(records, samples)
    f1.assert_public_payload_safe(summary, private_markers=markers)
    report = build_markdown_report(summary, private_markers=markers)
    write_public_json(resolve_repo_path(args.report_json), summary)
    write_public_text(resolve_repo_path(args.report_md), report, private_markers=markers)

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
            "parenthetical_candidate_rows_private": [row.to_private_dict() for row in parenthetical_rows],
            "unresolved_rows_private": [row.to_private_dict() for row in unresolved_rows],
            "lookup_results_private": [result.to_private_dict() for result in lookup_results.values()],
            "lookup_summary_public": lookup_summary_public,
            "tag_category_lookup_cache_public": db_cache_public,
            "media_summaries_private": list(media_summaries),
            "raw_scope_private": raw_scope,
            "db_identity_public": db_identity,
            "sample_summary_public": sample_public,
            "local_classification_index_public": local_index.public_summary(),
        },
    )
    write_private_text(media_summary_csv, build_media_summary_csv(media_summaries))
    write_private_text(media_summary_md, _rows_markdown(media_summaries))
    write_private_text(tag_candidates_csv, _candidate_csv(tag_rows))
    write_private_text(tag_candidates_md, _rows_markdown([row.to_private_dict() for row in tag_rows]))
    write_private_text(entity_candidates_csv, _candidate_csv(entity_rows))
    write_private_text(entity_candidates_md, _rows_markdown([row.to_private_dict() for row in entity_rows]))
    write_private_text(lookup_cache_csv, _lookup_cache_csv(lookup_results))
    write_private_text(
        lookup_cache_md,
        _rows_markdown([result.to_private_dict() for result in lookup_results.values()]),
    )
    write_private_text(unresolved_tags_csv, _candidate_csv(unresolved_rows))
    write_private_text(unresolved_tags_md, _rows_markdown([row.to_private_dict() for row in unresolved_rows]))
    write_private_text(parenthetical_candidates_csv, _candidate_csv(parenthetical_rows))
    write_private_text(
        parenthetical_candidates_md,
        _rows_markdown([row.to_private_dict() for row in parenthetical_rows]),
    )
    write_private_text(manual_review_guide, build_manual_review_guide())

    return {
        "ok": True,
        "phase": PHASE,
        "report_md": _rel(resolve_repo_path(args.report_md)),
        "report_json": _rel(resolve_repo_path(args.report_json)),
        "private_details": _rel(details_json),
        "manual_review_guide": _rel(manual_review_guide),
        "sample_selected_count": sample_public.get("selected_count"),
        "metadata_success_count": public_command.get("metadata_success_count"),
        "metadata_failure_count": public_command.get("metadata_failure_count"),
        "normalized_media_record_count": summary["input_summary"]["normalized_media_record_count"],
        "candidate_row_count": summary["classification_middleware"]["candidate_row_count"],
        "future_route_recommendation": summary["future_route_recommendation"]["decision"],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = run(args)
    except Phase44P2RF3Error as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
