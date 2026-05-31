"""Phase 4.4-P0 Pixiv filename source-prior auto-verification design.

Lifecycle: phase-scoped operational runner with a reusable parser/helper
surface. It reads DB/app-managed metadata only, writes no DB rows, performs no
Pixiv/provider request, and keeps exact Pixiv mappings in ignored local
artifacts.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Media  # noqa: E402

PHASE = "4.4-P0"
REPORT_MD = Path("docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification.md")
REPORT_JSON = Path("docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification-summary.json")
LOCAL_DETAILS_JSON = Path(".local_manifests/phase-4.4p0-pixiv-source-prior-details.json")
LOCAL_VERIFY_JSON = Path(".local_manifests/phase-4.4p0-pixiv-auto-verify-details.json")
LOCAL_SHEET_MD = Path(".local_manifests/phase-4.4p0-pixiv-auto-verify-sheet.md")
LOCAL_SHEET_CSV = Path(".local_manifests/phase-4.4p0-pixiv-auto-verify-sheet.csv")

PIXIV_PRIOR_PATTERN = r"(?<!\d)(?P<pixiv_work_id>[1-9]\d{5,11})_p(?P<page_index>\d+)(?!\d)"
PIXIV_PRIOR_RE = re.compile(PIXIV_PRIOR_PATTERN)
PIXIV_CANDIDATE_RE = re.compile(r"(?<!\d)(?P<pixiv_work_id>\d+)_p(?P<page_index>\d+)(?!\d)", re.IGNORECASE)
WRITE_SQL_RE = re.compile(
    r"^\s*(insert|update|delete|merge|alter|drop|truncate|create|replace|grant|revoke|copy\s+.+\s+from|vacuum)\b",
    re.IGNORECASE | re.DOTALL,
)
LOCAL_PATH_RE = re.compile(
    r"(?i)((?<![a-z])[a-z]:[\\/]|\\\\[A-Za-z0-9_.-]+[\\/]|file://|/(users|home|root|mnt|volumes|workspace|tmp|var)(/|$))"
)
SECRET_TEXT_RE = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._~+\-/]{8,}|access[_-]?token\s*[=:]|api[_-]?key\s*[=:]|sk-[A-Za-z0-9_-]{16,})")
APPROVED_D1G_SAMPLE_IDS = (2690, 2687, 2670, 2654, 2647)


class Phase44P0Error(RuntimeError):
    pass


class EnvBlockedError(Phase44P0Error):
    pass


class IdentityBlockedError(Phase44P0Error):
    pass


class OutputPathError(Phase44P0Error):
    pass


class PrivacyBlocked(Phase44P0Error):
    pass


class ReadOnlyViolation(Phase44P0Error):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    violet_env: str
    db_user: str
    db_password: str
    db_host: str
    db_port: int
    db_name: str

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@dataclass(frozen=True)
class ImageSignature:
    width: int
    height: int
    aspect_ratio: float
    average_color: tuple[int, int, int]
    ahash: int
    dhash: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values

        values = dotenv_values(path)
        return {str(k): str(v) for k, v in values.items() if k and v is not None}
    except Exception:
        parsed: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                parsed[key.strip()] = value.strip().strip('"').strip("'")
        return parsed


def _env_value(dotenv_values: dict[str, str], key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ.get(key, default)
    return dotenv_values.get(key, default)


def _load_file_settings(project_root: Path) -> dict[str, Any]:
    settings_file = project_root / "data" / "settings.json"
    if not settings_file.exists():
        return {}
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IdentityBlockedError("identity_blocked: data/settings.json is not valid JSON") from exc
    return data if isinstance(data, dict) else {}


def load_project_config(project_root: Path = ROOT) -> ProjectConfig:
    dotenv_values = _read_dotenv(project_root / ".env")
    violet_env_raw = _env_value(dotenv_values, "VIOLET_ENV", "").strip()
    violet_env = (violet_env_raw or "development").lower()
    if violet_env != "development":
        reported_env = violet_env_raw or "unset"
        raise EnvBlockedError(f"env_blocked: VIOLET_ENV must be 'development' for {PHASE}; got {reported_env!r}")
    if _env_value(dotenv_values, "TEST_DATABASE_URL", "").strip():
        raise EnvBlockedError("env_blocked: TEST_DATABASE_URL is set; refusing development DB audit")

    file_settings = _load_file_settings(project_root)
    db_settings = file_settings.get("database", {}) if isinstance(file_settings.get("database"), dict) else {}
    db_name = str(db_settings.get("name") or "").strip() or _env_value(dotenv_values, "POSTGRES_DB", "").strip() or "blombooru"
    if db_name == "blombooru_test":
        raise IdentityBlockedError("identity_blocked: target DB is blombooru_test, not blombooru")
    db_host = str(db_settings.get("host") or "").strip() or _env_value(dotenv_values, "POSTGRES_HOST", "").strip() or "localhost"
    db_port = int(str(db_settings.get("port") or "").strip() or _env_value(dotenv_values, "POSTGRES_PORT", "").strip() or "5432")
    db_user = str(db_settings.get("user") or "").strip() or _env_value(dotenv_values, "POSTGRES_USER", "").strip() or "postgres"
    db_password = str(db_settings.get("password") or "") or _env_value(dotenv_values, "POSTGRES_PASSWORD", "")
    return ProjectConfig(
        project_root=project_root.resolve(),
        violet_env=violet_env,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
    )


def install_read_only_guard(engine: Any) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _guard(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: bool) -> None:
        if WRITE_SQL_RE.search(statement or ""):
            raise ReadOnlyViolation("read_only_guard_blocked_write_sql")


def prove_db_identity(session: Session, config: ProjectConfig) -> dict[str, Any]:
    actual_db = session.execute(text("SELECT current_database()")).scalar()
    if str(actual_db) != "blombooru" or config.db_name != "blombooru":
        raise IdentityBlockedError(f"identity_blocked: expected DB blombooru, got {config.db_name!r}/{actual_db!r}")
    return {
        "violet_env": config.violet_env,
        "configured_db_host": config.db_host,
        "configured_db_port": config.db_port,
        "configured_db_user": config.db_user,
        "configured_db_name": config.db_name,
        "actual_db_name": str(actual_db),
        "db_identity_result": "development_blombooru_confirmed",
        "db_password_included": False,
        "local_paths_redacted": True,
    }


def _basename_from_metadata(value: str | None) -> str | None:
    if not value:
        return None
    text_value = str(value).replace("\\", "/")
    return text_value.rsplit("/", 1)[-1]


def metadata_fields_for_pixiv(media: Media) -> list[tuple[str, str, str | None]]:
    return [
        ("stored_filename", "stored_filename", media.filename),
        ("stored_path_basename", "stored_path_basename", _basename_from_metadata(media.path)),
        ("app_managed_thumbnail_basename", "app_managed_basename", _basename_from_metadata(media.thumbnail_path)),
        ("other_source_basename", "other_available_metadata", _basename_from_metadata(media.source)),
    ]


def extract_pixiv_filename_prior_from_text(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    matches: list[dict[str, Any]] = []
    for match in PIXIV_PRIOR_RE.finditer(str(value)):
        matches.append(
            {
                "pixiv_work_id": match.group("pixiv_work_id"),
                "page_index": int(match.group("page_index")),
                "token": match.group(0),
                "span": [match.start(), match.end()],
            }
        )
    return matches


def detect_pixiv_filename_variants(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    text_value = str(value)
    accepted_spans = {tuple(item["span"]) for item in extract_pixiv_filename_prior_from_text(text_value)}
    variants: list[dict[str, Any]] = []
    for match in PIXIV_CANDIDATE_RE.finditer(text_value):
        if (match.start(), match.end()) in accepted_spans:
            continue
        work_id = match.group("pixiv_work_id")
        marker = text_value[match.start() : match.end()]
        if "_P" in marker:
            reason = "uppercase_page_marker_possible_variant"
        elif len(work_id) < 6:
            reason = "work_id_too_short"
        elif len(work_id) > 12:
            reason = "work_id_too_long"
        elif work_id.startswith("0"):
            reason = "leading_zero_work_id"
        else:
            reason = "non_canonical_candidate"
        variants.append(
            {
                "token": marker,
                "work_id_length": len(work_id),
                "page_index_text": match.group("page_index"),
                "reason": reason,
            }
        )
    return variants


def _match_contexts(basename: str | None, token: str, page_index: int) -> list[str]:
    if not basename:
        return ["metadata_value_without_basename"]
    contexts: set[str] = set()
    token_index = basename.find(token)
    if token_index < 0:
        return ["token_not_in_basename"]
    before = basename[:token_index]
    after = basename[token_index + len(token) :]
    if not before:
        contexts.add("token_at_basename_start")
    else:
        contexts.add("prefixed_token")
    lower_after = after.lower()
    if lower_after in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        contexts.add("simple_exact_token_basename")
    if re.match(r"^-\d{8,}", after):
        contexts.add("suffix_timestamp_case")
    if re.match(r"^\(\d+\)", after):
        contexts.add("duplicate_marker_case")
    if page_index > 0:
        contexts.add("non_p0_page")
    else:
        contexts.add("p0_page")
    return sorted(contexts)


def audit_pixiv_source_priors(db: Session, *, approved_ids: Iterable[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = db.query(Media).order_by(Media.id.asc()).all()
    approved_set = {int(item) for item in approved_ids}
    media_with_prior = 0
    total_token_occurrences = 0
    media_with_multiple_unique_tokens = 0
    approved_with_prior = 0
    work_id_media_counts: Counter[str] = Counter()
    page_indexes: Counter[str] = Counter()
    by_content_class: Counter[str] = Counter()
    by_field_kind: Counter[str] = Counter()
    by_source_field: Counter[str] = Counter()
    fields_present: Counter[str] = Counter()
    possible_variants: Counter[str] = Counter()
    details: list[dict[str, Any]] = []

    for media in rows:
        media_matches: list[dict[str, Any]] = []
        media_variants: list[dict[str, Any]] = []
        content_class = _enum_label(media.content_class) or "unset"
        for source_field, field_kind, value in metadata_fields_for_pixiv(media):
            if value:
                fields_present[source_field] += 1
            basename = _basename_from_metadata(value)
            for match in extract_pixiv_filename_prior_from_text(value):
                contexts = _match_contexts(basename, match["token"], int(match["page_index"]))
                media_matches.append(
                    {
                        "source_field": source_field,
                        "field_kind": field_kind,
                        "pixiv_work_id": match["pixiv_work_id"],
                        "page_index": match["page_index"],
                        "token": match["token"],
                        "sanitized_basename": basename,
                        "contexts": contexts,
                    }
                )
            for variant in detect_pixiv_filename_variants(value):
                possible_variants[variant["reason"]] += 1
                media_variants.append(
                    {
                        "source_field": source_field,
                        "field_kind": field_kind,
                        "sanitized_basename": basename,
                        **variant,
                    }
                )
        if media_matches:
            media_with_prior += 1
            by_content_class[content_class] += 1
            if int(media.id) in approved_set:
                approved_with_prior += 1
            unique_work_ids = {item["pixiv_work_id"] for item in media_matches}
            unique_token_pages = {(item["pixiv_work_id"], int(item["page_index"])) for item in media_matches}
            if len(unique_token_pages) > 1:
                media_with_multiple_unique_tokens += 1
            for work_id in unique_work_ids:
                work_id_media_counts[work_id] += 1
            for _work_id, page_index in unique_token_pages:
                page_indexes[str(page_index)] += 1
            for item in media_matches:
                total_token_occurrences += 1
                by_field_kind[item["field_kind"]] += 1
                by_source_field[item["source_field"]] += 1
            details.append(
                {
                    "media_id": int(media.id),
                    "content_class": content_class,
                    "file_type": _enum_label(media.file_type) or "unknown",
                    "matches": media_matches,
                    "possible_variants": media_variants,
                    "classification": "pixiv_filename_source_prior_token_only_unverified",
                }
            )
        elif media_variants:
            details.append(
                {
                    "media_id": int(media.id),
                    "content_class": content_class,
                    "file_type": _enum_label(media.file_type) or "unknown",
                    "matches": [],
                    "possible_variants": media_variants,
                    "classification": "possible_variant_without_accepted_token",
                }
            )

    duplicate_work_id_count = sum(1 for count in work_id_media_counts.values() if count > 1)
    total = len(rows)
    summary = {
        "audit_scope": "development_db_app_managed_metadata_only",
        "db_write_allowed": False,
        "source_roots_scanned": False,
        "icloud_touched": False,
        "cloud_files_hydrated": False,
        "original_source_files_read": False,
        "app_managed_storage_mutated": False,
        "total_media_inspected": total,
        "total_candidate_filename_source_priors": total_token_occurrences,
        "media_with_one_or_more_pixiv_like_tokens": media_with_prior,
        "coverage_percent": round((media_with_prior / total * 100), 2) if total else 0.0,
        "distinct_candidate_pixiv_work_ids": len(work_id_media_counts),
        "duplicate_work_id_count": duplicate_work_id_count,
        "page_index_distribution": dict(sorted(page_indexes.items(), key=lambda item: int(item[0]))),
        "content_class_distribution": dict(sorted(by_content_class.items())),
        "field_kind_distribution": dict(sorted(by_field_kind.items())),
        "source_field_distribution": dict(sorted(by_source_field.items())),
        "metadata_fields_present_counts": dict(sorted(fields_present.items())),
        "invalid_or_variant_token_count": sum(possible_variants.values()),
        "invalid_or_variant_reasons": dict(sorted(possible_variants.items())),
        "multiple_token_in_one_media_count": media_with_multiple_unique_tokens,
        "approved_five_sample_pixiv_prior_count": approved_with_prior,
        "approved_samples_are_representative_for_pixiv_prior": approved_with_prior > 0,
        "metadata_retention_assessment": "filename_and_app_managed_basenames_available_but_no_dedicated_original_basename_or_source_prior_ledger",
        "metadata_retention_gap": True,
        "exact_pixiv_ids_in_public_report": False,
    }
    private = {
        "phase": PHASE,
        "summary": summary,
        "details": details,
        "distinct_pixiv_work_ids": sorted(work_id_media_counts),
        "contains_exact_pixiv_ids": True,
        "contains_exact_filenames_or_basenames": True,
        "contains_local_absolute_paths": False,
    }
    return summary, private


def _detail_categories(detail: dict[str, Any], duplicate_work_ids: set[str]) -> list[str]:
    categories: set[str] = set()
    if detail.get("content_class") == "anime":
        categories.add("content_class_anime")
    for match in detail.get("matches", []):
        categories.update(match.get("contexts", []))
        if match.get("pixiv_work_id") in duplicate_work_ids:
            categories.add("duplicate_work_id_case")
    return sorted(categories)


def select_feasibility_sample(private_details: dict[str, Any], *, max_items: int = 30) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    details = [item for item in private_details.get("details", []) if item.get("matches")]
    work_counts: Counter[str] = Counter()
    for detail in details:
        for work_id in {match["pixiv_work_id"] for match in detail.get("matches", [])}:
            work_counts[work_id] += 1
    duplicate_work_ids = {work_id for work_id, count in work_counts.items() if count > 1}
    enriched = [
        {
            **detail,
            "selection_categories": _detail_categories(detail, duplicate_work_ids),
        }
        for detail in details
    ]
    required_categories = [
        "simple_exact_token_basename",
        "suffix_timestamp_case",
        "duplicate_marker_case",
        "prefixed_token",
        "non_p0_page",
        "duplicate_work_id_case",
        "content_class_anime",
    ]
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()

    def add_if_new(detail: dict[str, Any]) -> None:
        media_id = int(detail["media_id"])
        if media_id not in selected_ids and len(selected) < max_items:
            selected.append(detail)
            selected_ids.add(media_id)

    for category in required_categories:
        for detail in enriched:
            if category in detail["selection_categories"]:
                add_if_new(detail)
                break
    for detail in sorted(enriched, key=lambda item: (item.get("content_class") != "anime", int(item["media_id"]))):
        add_if_new(detail)
        if len(selected) >= max_items:
            break

    category_counts: Counter[str] = Counter()
    content_counts: Counter[str] = Counter()
    for detail in selected:
        content_counts[detail.get("content_class") or "unset"] += 1
        category_counts.update(detail.get("selection_categories", []))
    public = {
        "sample_scope": "real_extracted_pixiv_prior_candidates",
        "selected_count": len(selected),
        "max_items": max_items,
        "selection_strategy": "cover_simple_suffix_duplicate_marker_prefix_non_p0_duplicate_work_id_then_fill_anime_first",
        "category_counts": dict(sorted(category_counts.items())),
        "content_class_distribution": dict(sorted(content_counts.items())),
        "exact_media_ids_public": False,
        "exact_pixiv_ids_public": False,
        "manual_validation_role": "stage_outcome_review_only_not_long_term_per_item_validation",
    }
    return public, selected


def reference_lookup_policy_result() -> dict[str, Any]:
    return {
        "status": "reference_lookup_policy_blocked",
        "safe_reference_route_found": False,
        "live_reference_lookup_allowed": False,
        "requests_attempted": 0,
        "concurrency": 0,
        "blocker": (
            "No official, documented, unauthenticated Pixiv metadata or preview endpoint was accepted for P0. "
            "Pixiv artwork HTML pages, cookies/login, browser automation, scraping, hotlink bypasses, and unofficial "
            "authenticated APIs remain forbidden."
        ),
        "researched_route_summary": [
            {
                "route": "public Pixiv artwork page",
                "decision": "blocked",
                "reason": "page HTML is not an accepted metadata/reference-image API and scraping/browser automation is forbidden",
            },
            {
                "route": "embed_or_oembed_style_metadata",
                "decision": "blocked",
                "reason": "P0 did not confirm a Pixiv-published provider scheme and endpoint suitable for automated lookup",
            },
            {
                "route": "unofficial_or_authenticated_pixiv_api",
                "decision": "forbidden",
                "reason": "requires login/OAuth/cookies or unofficial behavior outside this approval",
            },
        ],
        "policy_references": [
            {
                "label": "Pixiv Help Center",
                "url": "https://www.pixiv.help/hc/en-us",
                "use": "checked for public documented support surface",
            },
            {
                "label": "oEmbed specification",
                "url": "https://oembed.com/",
                "use": "used only to define what a documented provider endpoint would need to publish",
            },
        ],
    }


def hamming_distance(left: int, right: int) -> int:
    return int((left ^ right).bit_count())


def _average_hash(image: Image.Image, *, hash_size: int = 8) -> int:
    gray = ImageOps.grayscale(image).resize((hash_size, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    average = sum(pixels) / len(pixels)
    value = 0
    for index, pixel in enumerate(pixels):
        if pixel >= average:
            value |= 1 << index
    return value


def _difference_hash(image: Image.Image, *, hash_size: int = 8) -> int:
    gray = ImageOps.grayscale(image).resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0
    bit = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            if pixels[offset + col] > pixels[offset + col + 1]:
                value |= 1 << bit
            bit += 1
    return value


def build_image_signature(image: Image.Image) -> ImageSignature:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    width, height = normalized.size
    if width <= 0 or height <= 0:
        raise ValueError("image_dimensions_invalid")
    average_color = tuple(int(channel) for channel in normalized.resize((1, 1)).getpixel((0, 0)))
    return ImageSignature(
        width=width,
        height=height,
        aspect_ratio=width / height,
        average_color=average_color,
        ahash=_average_hash(normalized),
        dhash=_difference_hash(normalized),
    )


def build_image_signature_from_path(path: Path) -> ImageSignature:
    with Image.open(path) as image:
        return build_image_signature(image)


def compare_image_signatures(local: ImageSignature, reference: ImageSignature) -> dict[str, Any]:
    aspect_delta = abs(local.aspect_ratio - reference.aspect_ratio) / max(local.aspect_ratio, reference.aspect_ratio)
    ahash_distance = hamming_distance(local.ahash, reference.ahash)
    dhash_distance = hamming_distance(local.dhash, reference.dhash)
    color_distance = math.sqrt(
        sum((left - right) ** 2 for left, right in zip(local.average_color, reference.average_color, strict=True))
    )
    if aspect_delta <= 0.02 and ahash_distance <= 6 and dhash_distance <= 8 and color_distance <= 35:
        status = "auto_verified_high_confidence"
    elif aspect_delta >= 0.15 or ahash_distance >= 24 or dhash_distance >= 24 or color_distance >= 120:
        status = "auto_rejected_mismatch"
    else:
        status = "uncertain_needs_manual_or_lookup"
    return {
        "auto_verification_status": status,
        "aspect_ratio_delta": round(aspect_delta, 4),
        "ahash_distance": ahash_distance,
        "dhash_distance": dhash_distance,
        "average_color_distance": round(color_distance, 2),
        "threshold_policy_version": "phase44p0-proposed-v1-not-production",
    }


def verification_gate_design() -> dict[str, Any]:
    return {
        "status_values": [
            "auto_verified_high_confidence",
            "auto_rejected_mismatch",
            "uncertain_needs_manual_or_lookup",
            "reference_unavailable",
            "policy_blocked",
            "unsupported_media_type",
        ],
        "local_input_policy": "prefer app-managed thumbnail_or_resized_derived_image; raw originals are not required",
        "reference_input_policy": "low_resolution_reference_preview_only_after_safe_documented_route_approval",
        "implemented_local_similarity_helpers": [
            "orientation_normalization",
            "aspect_ratio_delta",
            "average_hash_distance",
            "difference_hash_distance",
            "average_color_distance",
        ],
        "optional_future_signals": [
            "perceptual_hash",
            "ssim_after_resize",
            "local_clip_embedding_if_already_available",
            "page_index_metadata_match_if_reference_metadata_exposes_pages",
        ],
        "proposed_high_confidence_thresholds": {
            "aspect_ratio_delta_max": 0.02,
            "ahash_distance_max": 6,
            "dhash_distance_max": 8,
            "average_color_distance_max": 35,
            "requires_multiple_agreeing_signals": True,
        },
        "proposed_reject_thresholds": {
            "aspect_ratio_delta_min": 0.15,
            "ahash_distance_min": 24,
            "dhash_distance_min": 24,
            "average_color_distance_min": 120,
        },
        "threshold_readiness": "design_only_not_production_ready_until_safe_reference_sample_results_exist",
        "future_persistence_precondition": "only auto_verified_high_confidence items may become eligible_for_persistence in a later approved DB-write phase",
    }


def source_prior_design() -> dict[str, Any]:
    return {
        "recommended_name": "LocalSourceHint",
        "acceptable_aliases": ["SourcePrior", "FilenameSourcePrior"],
        "source_prior_type": "pixiv_filename",
        "p0_db_write_allowed": False,
        "lifecycle": [
            "extracted",
            "reference_lookup_attempted",
            "auto_verified_high_confidence_or_rejected_or_uncertain",
            "eligible_for_persistence_only_if_high_confidence",
        ],
        "recommended_fields": [
            "media_id",
            "source_prior_type",
            "extracted_work_id",
            "page_index",
            "source_field_kind",
            "extraction_pattern_version",
            "reference_lookup_status",
            "auto_verification_status",
            "verification_method",
            "verification_score_summary",
            "confidence",
            "validation_status",
            "privacy_level",
            "provider_relation",
            "db_write_allowed",
        ],
        "confidence_values": [
            "filename_token_only",
            "auto_verified_high_confidence",
            "rejected_mismatch",
            "uncertain",
        ],
        "validation_values": [
            "unvalidated",
            "auto_verified",
            "manually_validated_correct",
            "manually_rejected",
            "ambiguous",
        ],
        "contract_relationship": {
            "ProviderCache": "not_a_provider_result_and_not_suitable_for_provider_cache_without_a_separate_lookup_result",
            "EntityEvidence": "not_confirmed_evidence_until_auto_verified_and_future_persistence_policy_approves",
            "MediaEntityCandidate": "may_seed_future_metadata_lookup_only_after_verification_or_separate_policy",
            "MediaEntityAssignment": "must_not_create_confirmed_assignment",
            "Entity": "must_not_auto_create_entity",
            "localization_pipeline": "not_a_tag_translation_or_localization_input_in_P0",
        },
    }


def build_verification_details(selected: list[dict[str, Any]], lookup_policy: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for detail in selected:
        rows.append(
            {
                "media_id": detail["media_id"],
                "matches": detail["matches"],
                "selection_categories": detail["selection_categories"],
                "reference_lookup_status": lookup_policy["status"],
                "auto_verification_status": "policy_blocked",
                "eligible_for_db_import": False,
                "db_write_allowed": False,
            }
        )
    return {
        "phase": PHASE,
        "reference_lookup_policy": lookup_policy,
        "verification_rows": rows,
        "request_count": 0,
        "reference_images_available": 0,
        "contains_exact_pixiv_ids": True,
        "contains_reference_urls": False,
        "contains_local_absolute_paths": False,
    }


def build_public_verification_summary(selected_count: int, lookup_policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "not_run_reference_lookup_policy_blocked",
        "reason": lookup_policy["blocker"],
        "sample_size": selected_count,
        "live_reference_lookup_sample_size": 0,
        "request_count": 0,
        "reference_images_available": 0,
        "result_distribution": {"policy_blocked": selected_count},
        "auto_verified_high_confidence_count": 0,
        "auto_rejected_mismatch_count": 0,
        "uncertain_count": 0,
    }


def assert_public_payload_safe(payload: Any, *, private_markers: Iterable[str] = ()) -> None:
    text_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if LOCAL_PATH_RE.search(text_payload):
        raise PrivacyBlocked("public_payload_contains_local_path")
    if SECRET_TEXT_RE.search(text_payload):
        raise PrivacyBlocked("public_payload_contains_secret_like_text")
    for marker in private_markers:
        if marker and marker in text_payload:
            raise PrivacyBlocked(f"public_payload_contains_private_marker:{marker[:8]}")


def build_public_summary(
    *,
    generated_at: str,
    identity: dict[str, Any],
    extraction_summary: dict[str, Any],
    sample_summary: dict[str, Any],
    lookup_policy: dict[str, Any],
    verification_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "title": "Pixiv Filename Source-Prior Automated Verification Design and Feasibility Test",
        "generated_at": generated_at,
        "stage_goal": "design_and_test_the_pre_persistence_gate_for_pixiv_filename_source_priors_without_DB_writes",
        "route_correction": "manual_per_image_validation_is_not_the_long_term_route; future trust requires automated_correspondence_gate",
        "db_identity": identity,
        "source_prior_design": source_prior_design(),
        "parser_policy": {
            "stable_token": "<numeric_pixiv_work_id>_p<page_index>",
            "regex": PIXIV_PRIOR_PATTERN,
            "work_id_length_policy": "6_to_12_digits_positive_integer_no_leading_zero",
            "lowercase_p_required": True,
            "uppercase_variant_policy": "detect_and_report_possible_variant_do_not_accept",
            "pattern_version": "phase44p0-pixiv-filename-v1",
        },
        "aggregate_extraction_metrics": extraction_summary,
        "feasibility_sample_summary": sample_summary,
        "safe_reference_lookup_policy_result": lookup_policy,
        "automated_verification_gate_design": verification_gate_design(),
        "verification_sample_summary": verification_summary,
        "future_persistence_design_recommendation": {
            "recommended_next_phase": "Phase 4.4-P1 - Pixiv Source-Prior Persistence for Auto-Verified High-Confidence Items",
            "p1_scope": [
                "persist LocalSourceHint rows or equivalent only after auto_verified_high_confidence",
                "no confirmed assignment",
                "no automatic Entity",
                "no Pixiv metadata lookup unless separately approved",
                "exact Pixiv ID remains local/private by default",
                "rollback_and_idempotency_required",
            ],
            "schema_gap": "no current dedicated original_basename_or_source_prior_ledger",
            "p0_implements_migration": False,
        },
        "local_artifacts": {
            "source_prior_details_json": str(LOCAL_DETAILS_JSON).replace("\\", "/"),
            "auto_verify_details_json": str(LOCAL_VERIFY_JSON).replace("\\", "/"),
            "auto_verify_sheet_md": str(LOCAL_SHEET_MD).replace("\\", "/"),
            "auto_verify_sheet_csv": str(LOCAL_SHEET_CSV).replace("\\", "/"),
            "artifacts_are_gitignored": True,
            "public_report_contains_exact_mappings": False,
        },
        "safety_confirmation": {
            "db_write": False,
            "db_migration": False,
            "provider_cache_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "confirmed_assignment": False,
            "automatic_entity_creation": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "localization_execution": False,
            "entity_resolver": False,
            "broad_similarity_or_clustering": False,
            "pixiv_scraping": False,
            "browser_automation": False,
            "cookies_or_login": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "public_exact_pixiv_mapping": False,
        },
    }


def build_markdown_report(summary: dict[str, Any]) -> str:
    metrics = summary["aggregate_extraction_metrics"]
    lookup = summary["safe_reference_lookup_policy_result"]
    verification = summary["verification_sample_summary"]
    sample = summary["feasibility_sample_summary"]
    lines = [
        "# Phase 4.4-P0 - Pixiv Filename Source-Prior Auto-Verification Design",
        "",
        "## Why P0 Exists",
        "",
        "PR #84 showed that Pixiv-like filename tokens are a non-trivial local source prior. P0 corrects the route from manual per-image validation toward an automated pre-persistence correspondence gate.",
        "",
        "The user-facing strategy is: human review may validate the stage outcome and threshold direction, but future filename-ID pairs must not rely on long-term per-item manual review.",
        "",
        "## Source-Prior Concept",
        "",
        "- Recommended concept name: `LocalSourceHint` (`SourcePrior` / `FilenameSourcePrior` remain acceptable aliases).",
        "- `pixiv_filename` is local deterministic source-prior data, not a provider result, not `ProviderCache`, not confirmed `EntityEvidence`, not a confirmed assignment, and not an automatic `Entity`.",
        "- Lifecycle: `extracted -> reference_lookup_attempted -> auto_verified_high_confidence / rejected / uncertain -> eligible_for_persistence`.",
        "- P0 sets `db_write_allowed=false`; future P1 should persist only `auto_verified_high_confidence` hints after a separate DB-write approval.",
        "",
        "## Parser Policy",
        "",
        f"- Stable token regex: `{summary['parser_policy']['regex']}`.",
        "- Work ID policy: positive integer, 6-12 digits, no leading zero.",
        "- `_p` must be literal lowercase; uppercase variants are detected as possible variants and not silently accepted.",
        "- Prefixes and suffixes around the token are allowed; no character may appear between numeric ID and `_pN`.",
        "",
        "## Aggregate Extraction Metrics",
        "",
        f"- Total media inspected: `{metrics['total_media_inspected']}`.",
        f"- Total candidate filename source-prior occurrences: `{metrics['total_candidate_filename_source_priors']}`.",
        f"- Media with one or more Pixiv-like tokens: `{metrics['media_with_one_or_more_pixiv_like_tokens']}` (`{metrics['coverage_percent']}`%).",
        f"- Distinct candidate Pixiv work IDs: `{metrics['distinct_candidate_pixiv_work_ids']}`.",
        f"- Duplicate work ID count: `{metrics['duplicate_work_id_count']}`.",
        f"- Page index distribution: `{json.dumps(metrics['page_index_distribution'], sort_keys=True)}`.",
        f"- Content class distribution: `{json.dumps(metrics['content_class_distribution'], sort_keys=True)}`.",
        f"- Field kind distribution: `{json.dumps(metrics['field_kind_distribution'], sort_keys=True)}`.",
        f"- Invalid / variant token count: `{metrics['invalid_or_variant_token_count']}`.",
        f"- Multiple-token-in-one-media count: `{metrics['multiple_token_in_one_media_count']}`.",
        f"- Approved five-sample Pixiv-prior count: `{metrics['approved_five_sample_pixiv_prior_count']}`.",
        "",
        "## Metadata Retention Assessment",
        "",
        f"- Assessment: `{metrics['metadata_retention_assessment']}`.",
        "- Current DB/app-managed metadata retains enough filename/basename signal to recover many Pixiv-style priors.",
        "- There is still no dedicated `original_basename` or source-prior ledger, so missing tokens remain a metadata retention gap rather than proof the Pixiv route is weak.",
        "",
        "## Safe Reference Lookup Policy",
        "",
        f"- Result: `{lookup['status']}`.",
        f"- Request count: `{lookup['requests_attempted']}`.",
        f"- Blocker: {lookup['blocker']}",
        "- Researched public Pixiv artwork pages, embed/oEmbed-style possibilities, and unofficial/authenticated API paths.",
        "- No live Pixiv lookup, browser automation, cookies, login session, scraping, hotlink bypass, unofficial authenticated API, or reference-image download was performed.",
        "- Policy references used for route assessment: [Pixiv Help Center](https://www.pixiv.help/hc/en-us), [oEmbed specification](https://oembed.com/).",
        "",
        "## Automated Verification Gate",
        "",
        "- Intended input: local app-managed thumbnail or derived/resized image plus safe low-resolution reference preview, if a future documented route is approved.",
        "- Implemented local helper signals: orientation normalization, aspect ratio delta, average hash distance, difference hash distance, and average color distance.",
        "- Proposed high-confidence policy requires multiple agreeing signals; thresholds are design-only until safe reference samples exist.",
        "- Proposed statuses: `auto_verified_high_confidence`, `auto_rejected_mismatch`, `uncertain_needs_manual_or_lookup`, `reference_unavailable`, `policy_blocked`, `unsupported_media_type`.",
        "",
        "## Feasibility Sample",
        "",
        f"- Selected local sample count: `{sample['selected_count']}`.",
        f"- Selection strategy: `{sample['selection_strategy']}`.",
        f"- Sample category counts: `{json.dumps(sample['category_counts'], sort_keys=True)}`.",
        "- Exact sample details are stored only in ignored local artifacts.",
        "",
        "## Verification Result",
        "",
        f"- Status: `{verification['status']}`.",
        f"- Live reference lookup sample size: `{verification['live_reference_lookup_sample_size']}`.",
        f"- Reference images available: `{verification['reference_images_available']}`.",
        f"- Auto-verified high-confidence count: `{verification['auto_verified_high_confidence_count']}`.",
        f"- Result distribution: `{json.dumps(verification['result_distribution'], sort_keys=True)}`.",
        "- Because no safe reference route was accepted, P0 did not test live correspondence against Pixiv reference images. The automated gate is designed and locally unit-tested, but production thresholds remain future work.",
        "",
        "## Future DB Persistence Recommendation",
        "",
        "- Recommended next phase: `Phase 4.4-P1 - Pixiv Source-Prior Persistence for Auto-Verified High-Confidence Items`.",
        "- P1 should persist `LocalSourceHint` / `SourcePrior` rows only for `auto_verified_high_confidence` items.",
        "- P1 must not create confirmed assignments, automatic `Entity` rows, Pixiv metadata lookups, or public exact ID exposure unless separately approved.",
        "- If a schema gap remains, use an additive schema or JSON payload design with rollback/idempotency; P0 does not implement a migration.",
        "",
        "## Privacy Policy",
        "",
        "- Public report includes aggregate metrics, design decisions, policy status, and safety confirmation only.",
        "- Public report excludes exact local filenames, exact local paths, source/iCloud paths, exact media-to-Pixiv mappings, raw Pixiv ID lists, image bytes, credentials, and raw private artifact details.",
        "- Exact mappings and verification rows are kept in ignored `.local_manifests` artifacts.",
        "",
        "## Safety Confirmation",
        "",
    ]
    for key, value in summary["safety_confirmation"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Local Artifacts", ""])
    for key, value in summary["local_artifacts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def build_sheet_markdown(selected: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 4.4-P0 Pixiv Auto-Verification Local Sheet",
        "",
        "Local ignored artifact. Exact Pixiv IDs and media mappings must not be copied into public reports.",
        "",
        "| media_id | content_class | pixiv_work_id | page_index | source_field | categories | reference_lookup_status | auto_verification_status | stage_outcome_notes |",
        "|---:|---|---|---:|---|---|---|---|---|",
    ]
    for detail in selected:
        for match in detail.get("matches", []):
            lines.append(
                "| {media_id} | {content_class} | {work_id} | {page_index} | {field} | {categories} | policy_blocked | policy_blocked | |".format(
                    media_id=detail["media_id"],
                    content_class=detail.get("content_class") or "",
                    work_id=match["pixiv_work_id"],
                    page_index=match["page_index"],
                    field=match["source_field"],
                    categories=", ".join(detail.get("selection_categories", [])),
                )
            )
    lines.append("")
    return "\n".join(lines)


def build_sheet_csv(selected: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "media_id",
            "content_class",
            "pixiv_work_id",
            "page_index",
            "source_field",
            "selection_categories",
            "reference_lookup_status",
            "auto_verification_status",
            "stage_outcome_notes",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for detail in selected:
        for match in detail.get("matches", []):
            writer.writerow(
                {
                    "media_id": detail["media_id"],
                    "content_class": detail.get("content_class") or "",
                    "pixiv_work_id": match["pixiv_work_id"],
                    "page_index": match["page_index"],
                    "source_field": match["source_field"],
                    "selection_categories": ";".join(detail.get("selection_categories", [])),
                    "reference_lookup_status": "policy_blocked",
                    "auto_verification_status": "policy_blocked",
                    "stage_outcome_notes": "",
                }
            )
    return output.getvalue()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def resolve_output_path(path_text: str | Path, *, expected_parent: Path) -> Path:
    path = Path(path_text)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    expected = (ROOT / expected_parent).resolve()
    try:
        resolved.relative_to(expected)
    except ValueError as exc:
        raise OutputPathError(f"output_path_outside_expected_parent: {resolved}") from exc
    return resolved


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--details-json", default=str(LOCAL_DETAILS_JSON))
    parser.add_argument("--verify-json", default=str(LOCAL_VERIFY_JSON))
    parser.add_argument("--sheet-md", default=str(LOCAL_SHEET_MD))
    parser.add_argument("--sheet-csv", default=str(LOCAL_SHEET_CSV))
    parser.add_argument("--sample-size", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report_md = resolve_output_path(args.report_md, expected_parent=Path("docs/reports"))
    report_json = resolve_output_path(args.report_json, expected_parent=Path("docs/reports"))
    details_json = resolve_output_path(args.details_json, expected_parent=Path(".local_manifests"))
    verify_json = resolve_output_path(args.verify_json, expected_parent=Path(".local_manifests"))
    sheet_md = resolve_output_path(args.sheet_md, expected_parent=Path(".local_manifests"))
    sheet_csv = resolve_output_path(args.sheet_csv, expected_parent=Path(".local_manifests"))

    config = load_project_config()
    engine = create_engine(config.database_url)
    install_read_only_guard(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        identity = prove_db_identity(session, config)
        extraction_summary, private_details = audit_pixiv_source_priors(session, approved_ids=APPROVED_D1G_SAMPLE_IDS)
    finally:
        session.close()
        engine.dispose()

    sample_summary, selected = select_feasibility_sample(private_details, max_items=args.sample_size)
    lookup_policy = reference_lookup_policy_result()
    verify_details = build_verification_details(selected, lookup_policy)
    verification_summary = build_public_verification_summary(len(selected), lookup_policy)
    public_summary = build_public_summary(
        generated_at=_now_iso(),
        identity=identity,
        extraction_summary=extraction_summary,
        sample_summary=sample_summary,
        lookup_policy=lookup_policy,
        verification_summary=verification_summary,
    )

    private_markers = private_details.get("distinct_pixiv_work_ids", [])
    assert_public_payload_safe(public_summary, private_markers=private_markers)
    assert_public_payload_safe(build_markdown_report(public_summary), private_markers=private_markers)

    local_details_payload = {
        **private_details,
        "selected_feasibility_sample": selected,
        "sample_summary_public": sample_summary,
    }
    write_json(details_json, local_details_payload)
    write_json(verify_json, verify_details)
    write_text(sheet_md, build_sheet_markdown(selected))
    write_text(sheet_csv, build_sheet_csv(selected))
    write_json(report_json, public_summary)
    write_text(report_md, build_markdown_report(public_summary))
    print(
        json.dumps(
            {
                "status": "completed",
                "report_json": str(report_json.relative_to(ROOT)).replace("\\", "/"),
                "report_md": str(report_md.relative_to(ROOT)).replace("\\", "/"),
                "reference_lookup_status": lookup_policy["status"],
                "db_write_allowed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
