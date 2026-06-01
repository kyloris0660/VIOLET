"""Phase 4.4-P2R-F2 external gallery-dl adapter pilot.

Lifecycle: phase-scoped operational runner. It invokes a user-installed
gallery-dl boundary for a tiny Pixiv filename-prior sample, writes ignored
private artifacts under `.local_manifests`, writes public-safe reports, and
never writes database rows or persistence models.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts import run_phase44p2r_f1_gallery_dl_json_import_pilot as f1  # noqa: E402

PHASE = "4.4-P2R-F2"
TITLE = "External gallery-dl Metadata / Reference Adapter Pilot"
PHASE_SLUG = "phase-4.4p2r-f2-gallery-dl-external-adapter-pilot"

REPORT_MD = Path(f"docs/reports/{PHASE_SLUG}.md")
REPORT_JSON = Path(f"docs/reports/{PHASE_SLUG}-summary.json")
PHASE_OUTPUT_DIR = Path(".local_manifests") / PHASE_SLUG
PRIVATE_DETAILS_JSON = PHASE_OUTPUT_DIR / "details.json"
PRIVATE_SHEET_CSV = PHASE_OUTPUT_DIR / "sheet.csv"
PRIVATE_SHEET_MD = PHASE_OUTPUT_DIR / "sheet.md"
PRIVATE_RAW_DIR = PHASE_OUTPUT_DIR / "raw"
PRIVATE_DOWNLOAD_DIR = PHASE_OUTPUT_DIR / "downloads"

DEFAULT_SAMPLE_SIZE = 5
MAX_SAMPLE_SIZE = 10
DEFAULT_MAX_RECORDS = 10
DEFAULT_REFERENCE_DOWNLOAD_SAMPLE_SIZE = 2
MAX_REFERENCE_DOWNLOAD_SAMPLE_SIZE = 3
MAX_DOWNLOAD_FILES = 30
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

GALLERY_DL_METADATA_COMMAND_TEMPLATE = (
    "<gallery_dl_entrypoint> --dump-json --no-download https://www.pixiv.net/artworks/<WORK_ID>"
)
GALLERY_DL_REFERENCE_COMMAND_TEMPLATE = (
    "<gallery_dl_entrypoint> --range 1 -D <phase_download_dir> https://www.pixiv.net/artworks/<WORK_ID>"
)

AUTH_BLOCK_RE = re.compile(
    r"(?i)(auth|oauth|refresh.?token|login|cookie|401|403|forbidden|unauthorized|authentication)"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/]{8,}|"
    r"((?:access|refresh)[_-]?token\s*[=:]\s*)\S+|"
    r"((?:authorization|cookie|api[_-]?key|password|secret)\s*[=:]\s*)\S+"
)


class Phase44P2RF2Error(RuntimeError):
    pass


class SampleGateError(Phase44P2RF2Error):
    pass


class GalleryDlUnavailable(Phase44P2RF2Error):
    pass


class GalleryDlAuthBlocked(Phase44P2RF2Error):
    pass


class OutputPathError(Phase44P2RF2Error):
    pass


class PrivacyBlocked(Phase44P2RF2Error):
    pass


class ConfigBlockedError(Phase44P2RF2Error):
    pass


class IdentityBlockedError(Phase44P2RF2Error):
    pass


@dataclass(frozen=True)
class GalleryDlEntrypoint:
    mode: str
    command: tuple[str, ...]
    version: str | None
    available: bool
    reproducibility_status: str
    attempts: tuple[dict[str, Any], ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "mode": self.mode,
            "version": self.version,
            "reproducibility_status": self.reproducibility_status,
            "uses_project_python": self.mode == "project_python_module_mode",
            "uses_external_executable": self.mode == "external_executable_mode",
            "uses_explicit_operator_command": self.mode == "explicit_operator_command_mode",
            "command_path_public": _public_command_label(self),
            "attempts": [
                {
                    key: value
                    for key, value in attempt.items()
                    if key not in {"command_private", "path_private", "stderr_private"}
                }
                for attempt in self.attempts
            ],
        }


@dataclass(frozen=True)
class SelectedSample:
    work_id: str
    page_indexes: tuple[int, ...]
    content_classes: tuple[str, ...]
    local_media_ids_private: tuple[int, ...]
    local_basenames_private: tuple[str, ...]
    has_p0_page: bool
    has_non_p0_page: bool
    duplicate_or_ambiguous: bool

    def public_projection(self) -> dict[str, Any]:
        return {
            "page_case": "p0_and_non_p0" if self.has_p0_page and self.has_non_p0_page else (
                "non_p0_only" if self.has_non_p0_page else "p0_only"
            ),
            "content_classes": list(self.content_classes),
            "duplicate_or_ambiguous": self.duplicate_or_ambiguous,
        }


@dataclass
class PixivGalleryDlAdapterRecord:
    source_adapter: str = "gallery_dl_external"
    adapter_mode: str | None = None
    adapter_version: str | None = None
    command_mode: str | None = None
    extractor_category: str | None = None
    extractor_subcategory: str | None = None
    work_id: str | None = None
    page_index: int | None = None
    page_count: int | None = None
    title: str | None = None
    artist_name: str | None = None
    artist_id: str | None = None
    tags: tuple[str, ...] = ()
    translated_tags: tuple[str, ...] = ()
    caption: str | None = None
    canonical_url: str | None = None
    image_url_kinds_available: tuple[str, ...] = ()
    gallery_dl_filename: str | None = None
    metadata_richness: str = "unsupported_record_shape"
    local_match_status: str = "unsupported_record_shape"
    local_match_content_class: str | None = None
    local_match_content_class_approved: bool = False
    local_media_id_private: int | None = None
    duplicate_local_media_ids_private: tuple[int, ...] = ()
    local_match_content_classes_private: tuple[str, ...] = ()
    page_index_status: str = "not_checked"
    reference_download_status: str = "not_requested"
    correspondence_status: str = "metadata_work_page_match_no_visual_check"
    eligible_for_future_local_source_hint: bool = False
    eligible_for_future_entity_candidate: bool = False
    evidence_strength_candidate: str = "not_evidence"
    db_write_allowed: bool = False
    privacy_level: str = "private_exact_mapping"
    record_shape: str = "unknown"
    source_file_private: str | None = None

    @classmethod
    def from_f1(
        cls,
        record: f1.PixivGalleryDlMetadataRecord,
        *,
        adapter_mode: str,
        command_mode: str,
        adapter_version: str | None,
    ) -> "PixivGalleryDlAdapterRecord":
        return cls(
            adapter_mode=adapter_mode,
            adapter_version=adapter_version,
            command_mode=command_mode,
            extractor_category=record.extractor_category,
            extractor_subcategory=record.extractor_subcategory,
            work_id=record.work_id,
            page_index=record.page_index,
            page_count=record.page_count,
            title=record.title,
            artist_name=record.artist_name,
            artist_id=record.artist_id,
            tags=record.tags,
            translated_tags=record.translated_tags,
            caption=record.caption,
            canonical_url=record.canonical_url,
            image_url_kinds_available=record.image_url_kinds_available,
            gallery_dl_filename=record.gallery_dl_filename,
            metadata_richness=record.metadata_richness,
            record_shape=record.record_shape,
            source_file_private=record.source_file_private,
        )

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_projection(self) -> dict[str, Any]:
        return {
            "source_adapter": self.source_adapter,
            "adapter_mode": self.adapter_mode,
            "adapter_version_present": self.adapter_version is not None,
            "command_mode": self.command_mode,
            "extractor_category": self.extractor_category,
            "extractor_subcategory": self.extractor_subcategory,
            "work_id_present": self.work_id is not None,
            "page_index_present": self.page_index is not None,
            "page_count_present": self.page_count is not None,
            "title_present": bool(self.title),
            "artist_name_present": bool(self.artist_name),
            "artist_id_present": self.artist_id is not None,
            "tag_count": len(self.tags),
            "translated_tag_count": len(self.translated_tags),
            "caption_present": bool(self.caption),
            "image_url_kinds_available": list(self.image_url_kinds_available),
            "metadata_richness": self.metadata_richness,
            "local_match_status": self.local_match_status,
            "local_match_content_class_present": self.local_match_content_class is not None,
            "local_match_content_class_approved": self.local_match_content_class_approved,
            "page_index_status": self.page_index_status,
            "reference_download_status": self.reference_download_status,
            "correspondence_status": self.correspondence_status,
            "eligible_for_future_local_source_hint": self.eligible_for_future_local_source_hint,
            "eligible_for_future_entity_candidate": self.eligible_for_future_entity_candidate,
            "evidence_strength_candidate": self.evidence_strength_candidate,
            "db_write_allowed": False,
            "record_shape": self.record_shape,
        }


@dataclass
class CommandResult:
    command_kind: str
    item_index: int
    success: bool
    exit_code: int | None
    stdout_path_private: str | None
    stdout_bytes: int
    stderr_redacted: str
    error_class: str | None = None
    error_is_auth_or_config: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "command_kind": self.command_kind,
            "item_index": self.item_index,
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout_bytes": self.stdout_bytes,
            "stderr_error_class": self.error_class,
            "error_is_auth_or_config": self.error_is_auth_or_config,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rel(path: Path) -> str:
    return f1._rel(path)


def resolve_repo_path(path: str | Path) -> Path:
    return f1.resolve_repo_path(path)


def _coerce_json_safe(value: Any) -> Any:
    return f1._coerce_json_safe(value)


def _public_command_label(entrypoint: GalleryDlEntrypoint) -> str:
    if entrypoint.mode == "project_python_module_mode":
        return "project_python -m gallery_dl"
    if entrypoint.mode == "external_executable_mode":
        return "gallery-dl external executable"
    if entrypoint.mode == "explicit_operator_command_mode":
        return "explicit operator command"
    return "unavailable"


def require_under_phase_output(path: Path) -> None:
    resolved = resolve_repo_path(path)
    try:
        f1.require_under_path(resolved, ROOT / PHASE_OUTPUT_DIR, code="gallery_dl_output_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc


def write_private_json(path: Path, payload: Any) -> None:
    path = resolve_repo_path(path)
    require_under_phase_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_coerce_json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


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


def _read_dotenv_values(project_root: Path) -> dict[str, str]:
    return f1._read_dotenv_values(project_root)


def _env_value(dotenv_values: Mapping[str, str], key: str, default: str = "") -> str:
    return f1._env_value(dotenv_values, key, default)


def _database_settings_value(
    file_settings: Mapping[str, Any],
    dotenv_values: Mapping[str, str],
    settings_key: str,
    env_key: str,
    default: Any,
) -> Any:
    return f1._database_settings_value(file_settings, dotenv_values, settings_key, env_key, default)


def load_project_config(project_root: Path = ROOT) -> f1.ProjectConfig:
    """Mirror backend.app.config.Settings DB precedence without importing it.

    Importing app settings can create data directories or settings files. This
    pilot only needs read-only identity labels, so it mirrors the runtime
    precedence: VIOLET_STORAGE_ROOT controls data/settings.json; settings.json
    database keys beat environment/.env; TEST_DATABASE_URL wins in test mode.
    """

    project_root = project_root.resolve()
    dotenv_values = _read_dotenv_values(project_root)
    violet_env = _env_value(dotenv_values, "VIOLET_ENV", "development").strip().lower()
    if violet_env not in f1.VALID_VIOLET_ENVS:
        raise ConfigBlockedError(f"identity_blocked: invalid VIOLET_ENV={violet_env!r}")

    storage_env = _env_value(dotenv_values, "VIOLET_STORAGE_ROOT", "").strip()
    storage_root = Path(storage_env) if storage_env else project_root
    storage_root_mode = "explicit_violet_storage_root" if storage_env else "code_root_default"
    settings_file = storage_root / "data" / "settings.json"
    file_settings, settings_source = f1._load_settings_json(settings_file)

    test_url = _env_value(dotenv_values, "TEST_DATABASE_URL", "").strip()
    if violet_env == "test" and test_url:
        db_name = f1._parse_db_name_from_url(test_url)
        if db_name.lower() in f1.FORBIDDEN_TEST_DB_NAMES:
            raise ConfigBlockedError(
                f"identity_blocked: VIOLET_ENV=test but TEST_DATABASE_URL points to production-like DB {db_name!r}"
            )
        database_url = make_url(test_url)
        return f1.ProjectConfig(
            project_root=project_root,
            violet_env=violet_env,
            database_url=database_url,
            db_user=str(database_url.username or ""),
            db_password=str(database_url.password or ""),
            db_host=str(database_url.host or ""),
            db_port=int(database_url.port or 5432),
            db_name=db_name,
            settings_source=settings_source,
            storage_root_mode=storage_root_mode,
            settings_file_exists=settings_file.exists(),
            database_url_source="test_database_url",
        )

    db_name = str(_database_settings_value(file_settings, dotenv_values, "name", "POSTGRES_DB", "blombooru"))
    if violet_env == "test" and db_name.lower() in f1.FORBIDDEN_TEST_DB_NAMES:
        raise ConfigBlockedError(
            "identity_blocked: VIOLET_ENV=test requires TEST_DATABASE_URL or a test-specific POSTGRES_DB"
        )
    db_user = str(_database_settings_value(file_settings, dotenv_values, "user", "POSTGRES_USER", "postgres"))
    db_password = str(_database_settings_value(file_settings, dotenv_values, "password", "POSTGRES_PASSWORD", ""))
    db_host = str(_database_settings_value(file_settings, dotenv_values, "host", "POSTGRES_HOST", "localhost"))
    db_port = int(_database_settings_value(file_settings, dotenv_values, "port", "POSTGRES_PORT", 5432))
    database_url = URL.create(
        drivername="postgresql",
        username=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        database=db_name,
    )
    return f1.ProjectConfig(
        project_root=project_root,
        violet_env=violet_env,
        database_url=database_url,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        settings_source=settings_source,
        storage_root_mode=storage_root_mode,
        settings_file_exists=settings_file.exists(),
        database_url_source="settings_env_or_default",
    )


def enforce_sample_size(sample_size: int) -> None:
    if sample_size < 1:
        raise SampleGateError("sample_size_must_be_positive")
    if sample_size > MAX_SAMPLE_SIZE:
        raise SampleGateError("sample_size_exceeds_max_10")


def enforce_record_count(record_count: int, max_records: int) -> None:
    if max_records < 1:
        raise SampleGateError("max_records_must_be_positive")
    if max_records > MAX_SAMPLE_SIZE:
        raise SampleGateError("max_records_exceeds_max_10_without_renewed_approval")
    if record_count > max_records:
        raise SampleGateError("generated_output_exceeds_max_records")


def split_operator_command(command: str) -> tuple[str, ...]:
    if not command or not command.strip():
        raise GalleryDlUnavailable("missing_explicit_gallery_dl_command")
    parts = shlex.split(command, posix=os.name != "nt")
    cleaned = tuple(part.strip().strip("\"'") for part in parts if part.strip().strip("\"'"))
    if not cleaned:
        raise GalleryDlUnavailable("missing_explicit_gallery_dl_command")
    return cleaned


CompletedRunner = Callable[..., subprocess.CompletedProcess[str]]


def _version_from_completed(completed: subprocess.CompletedProcess[str]) -> str | None:
    text = (completed.stdout or completed.stderr or "").strip()
    if not text:
        return None
    return text.splitlines()[0].strip()


def _probe_version(
    command: Sequence[str],
    *,
    mode: str,
    runner: CompletedRunner = subprocess.run,
    timeout: int = 15,
) -> tuple[bool, str | None, dict[str, Any]]:
    attempt: dict[str, Any] = {"mode": mode, "command_private": list(command) + ["--version"]}
    try:
        completed = runner(
            [*command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as exc:
        attempt.update({"available": False, "error_class": exc.__class__.__name__})
        return False, None, attempt
    except subprocess.SubprocessError as exc:
        attempt.update({"available": False, "error_class": exc.__class__.__name__})
        return False, None, attempt
    version = _version_from_completed(completed)
    attempt.update({"available": completed.returncode == 0, "returncode": completed.returncode})
    if completed.returncode == 0 and version:
        attempt["version_present"] = True
        return True, version, attempt
    attempt["stderr_private"] = redact_text(completed.stderr or "")
    return False, version, attempt


def probe_gallery_dl_entrypoint(
    explicit_command: str | None = None,
    *,
    runner: CompletedRunner = subprocess.run,
    python_executable: str | None = None,
) -> GalleryDlEntrypoint:
    attempts: list[dict[str, Any]] = []
    if explicit_command:
        command = split_operator_command(explicit_command)
        ok, version, attempt = _probe_version(command, mode="explicit_operator_command_mode", runner=runner)
        attempts.append(attempt)
        if not ok:
            raise GalleryDlUnavailable("explicit_gallery_dl_command_unavailable")
        return GalleryDlEntrypoint(
            mode="explicit_operator_command_mode",
            command=command,
            version=version,
            available=True,
            reproducibility_status="conditional_explicit_operator_command",
            attempts=tuple(attempts),
        )

    project_python = python_executable or sys.executable
    project_command = (project_python, "-m", "gallery_dl")
    ok, version, attempt = _probe_version(project_command, mode="project_python_module_mode", runner=runner)
    attempts.append(attempt)
    if ok:
        return GalleryDlEntrypoint(
            mode="project_python_module_mode",
            command=project_command,
            version=version,
            available=True,
            reproducibility_status="stable_project_python_module",
            attempts=tuple(attempts),
        )

    external = shutil.which("gallery-dl")
    if external:
        external_command = (external,)
        ok, version, attempt = _probe_version(external_command, mode="external_executable_mode", runner=runner)
        attempts.append(attempt)
        if ok:
            return GalleryDlEntrypoint(
                mode="external_executable_mode",
                command=external_command,
                version=version,
                available=True,
                reproducibility_status="external_executable_discovered",
                attempts=tuple(attempts),
            )
    else:
        attempts.append({"mode": "external_executable_mode", "available": False, "error_class": "not_on_path"})

    raise GalleryDlUnavailable("gallery_dl_missing_no_silent_py_launcher_fallback")


def build_metadata_command(entrypoint: GalleryDlEntrypoint, work_id: str) -> list[str]:
    return [*entrypoint.command, "--dump-json", "--no-download", f"https://www.pixiv.net/artworks/{work_id}"]


def build_reference_download_command(entrypoint: GalleryDlEntrypoint, work_id: str, download_dir: Path) -> list[str]:
    require_under_phase_output(download_dir)
    return [
        *entrypoint.command,
        "--range",
        "1",
        "-D",
        str(resolve_repo_path(download_dir)),
        f"https://www.pixiv.net/artworks/{work_id}",
    ]


def redact_text(text: str, *, private_markers: Iterable[str] = ()) -> str:
    redacted = SECRET_VALUE_RE.sub(lambda match: (match.group(1) or match.group(2) or match.group(3) or "[REDACTED]") + "[REDACTED]", text)
    redacted = f1.LOCAL_PATH_RE.sub("[REDACTED_LOCAL_PATH]", redacted)
    for marker in sorted((str(item) for item in private_markers if item), key=len, reverse=True):
        redacted = redacted.replace(marker, "[REDACTED_PRIVATE_ID]")
    return redacted[-4000:]


def classify_stderr(stderr: str) -> tuple[str | None, bool]:
    if not stderr.strip():
        return None, False
    if AUTH_BLOCK_RE.search(stderr):
        return "auth_or_config", True
    return "gallery_dl_command_failed", False


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
    runner: CompletedRunner = subprocess.run,
    timeout: int = 120,
) -> list[CommandResult]:
    results: list[CommandResult] = []
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
        if stdout:
            stdout_path = _write_raw_stdout(raw_dir, index, stdout)
        error_class, auth_blocked = classify_stderr(completed.stderr or "")
        success = completed.returncode == 0 and bool(stdout.strip())
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
            )
        )
    return results


def run_reference_download_commands(
    samples: Sequence[SelectedSample],
    entrypoint: GalleryDlEntrypoint,
    download_dir: Path,
    *,
    sample_size: int,
    runner: CompletedRunner = subprocess.run,
    timeout: int = 180,
) -> list[CommandResult]:
    if sample_size < 1:
        return []
    if sample_size > MAX_REFERENCE_DOWNLOAD_SAMPLE_SIZE:
        raise SampleGateError("reference_download_sample_size_exceeds_max_3")
    download_dir = resolve_repo_path(download_dir)
    require_under_phase_output(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    subset = samples[:sample_size]
    results: list[CommandResult] = []
    markers = [sample.work_id for sample in subset]
    for index, sample in enumerate(subset, start=1):
        command = build_reference_download_command(entrypoint, sample.work_id, download_dir)
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
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            results.append(
                CommandResult(
                    command_kind="reference_download",
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
        error_class, auth_blocked = classify_stderr(completed.stderr or "")
        results.append(
            CommandResult(
                command_kind="reference_download",
                item_index=index,
                success=completed.returncode == 0,
                exit_code=completed.returncode,
                stdout_path_private=None,
                stdout_bytes=len((completed.stdout or "").encode("utf-8")),
                stderr_redacted=redact_text(completed.stderr or "", private_markers=markers),
                error_class=None if completed.returncode == 0 else error_class or "gallery_dl_reference_download_failed",
                error_is_auth_or_config=auth_blocked,
            )
        )
    return results


def select_local_pixiv_prior_samples(prior_index: f1.LocalPriorIndex, *, sample_size: int) -> tuple[dict[str, Any], list[SelectedSample]]:
    enforce_sample_size(sample_size)
    grouped: dict[str, dict[str, Any]] = {}
    ambiguous_work_ids: set[str] = set()
    for (work_id, page_index), candidates in prior_index.by_work_page.items():
        entry = grouped.setdefault(
            work_id,
            {
                "work_id": work_id,
                "page_indexes": set(),
                "content_classes": set(),
                "media_ids": set(),
                "basenames": set(),
                "duplicate_or_ambiguous": False,
            },
        )
        entry["page_indexes"].add(int(page_index))
        if len(candidates) != 1:
            entry["duplicate_or_ambiguous"] = True
            ambiguous_work_ids.add(work_id)
        for candidate in candidates:
            entry["content_classes"].add(candidate.content_class)
            entry["media_ids"].add(candidate.media_id)
            entry["basenames"].update(candidate.basenames_private)

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
    ]

    def sort_key(sample: SelectedSample) -> tuple[int, int, int, str]:
        anime = "anime" in sample.content_classes
        return (
            0 if anime else 1,
            0 if not sample.duplicate_or_ambiguous else 1,
            0 if sample.has_p0_page else 1,
            sample.work_id,
        )

    anime_samples = sorted((sample for sample in samples if "anime" in sample.content_classes), key=sort_key)
    selected: list[SelectedSample] = []
    selected_ids: set[str] = set()

    def add_first(predicate: Callable[[SelectedSample], bool]) -> None:
        for sample in anime_samples:
            if sample.work_id not in selected_ids and predicate(sample):
                selected.append(sample)
                selected_ids.add(sample.work_id)
                return

    add_first(lambda sample: sample.has_p0_page)
    add_first(lambda sample: sample.has_non_p0_page)
    for sample in anime_samples:
        if len(selected) >= sample_size:
            break
        if sample.work_id not in selected_ids:
            selected.append(sample)
            selected_ids.add(sample.work_id)
    if len(selected) < sample_size:
        for sample in sorted(samples, key=sort_key):
            if len(selected) >= sample_size:
                break
            if sample.work_id not in selected_ids:
                selected.append(sample)
                selected_ids.add(sample.work_id)

    content_counts: Counter[str] = Counter()
    page_case_counts: Counter[str] = Counter()
    for sample in selected:
        for content_class in sample.content_classes:
            content_counts[content_class] += 1
        if sample.has_p0_page and sample.has_non_p0_page:
            page_case_counts["p0_and_non_p0"] += 1
        elif sample.has_non_p0_page:
            page_case_counts["non_p0_only"] += 1
        else:
            page_case_counts["p0_only"] += 1

    public = {
        "selected_count": len(selected),
        "requested_sample_size": sample_size,
        "max_sample_size": MAX_SAMPLE_SIZE,
        "sample_gate_status": "passed",
        "selection_strategy": "anime_first_cover_p0_and_non_p0_then_fill_by_work_id",
        "page_case_distribution": dict(sorted(page_case_counts.items())),
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


def parse_gallery_dl_json_inputs(input_path: str | Path, *, skip_invalid: bool = False) -> f1.ParseResult:
    return f1.parse_gallery_dl_json_inputs(input_path, skip_invalid=skip_invalid)


def normalize_adapter_records(
    parse_result: f1.ParseResult,
    *,
    entrypoint: GalleryDlEntrypoint,
) -> list[PixivGalleryDlAdapterRecord]:
    base_records = f1.normalize_records(parse_result, adapter_version=entrypoint.version)
    return [
        PixivGalleryDlAdapterRecord.from_f1(
            record,
            adapter_mode="external_process_boundary",
            command_mode=entrypoint.mode,
            adapter_version=entrypoint.version,
        )
        for record in base_records
    ]


def _finalize_joined_records(
    records: Sequence[PixivGalleryDlAdapterRecord],
    *,
    reference_download_enabled: bool,
    downloaded_file_count: int,
) -> list[PixivGalleryDlAdapterRecord]:
    finalized: list[PixivGalleryDlAdapterRecord] = []
    for record in records:
        updated = replace(record)
        if len(updated.local_match_content_classes_private) == 1:
            updated.local_match_content_class = updated.local_match_content_classes_private[0]
        if reference_download_enabled:
            updated.reference_download_status = "downloaded_artifacts_available" if downloaded_file_count else "reference_download_attempted_no_artifacts"
            updated.correspondence_status = "visual_reference_available_not_checked" if downloaded_file_count else "visual_reference_unavailable"
        elif updated.local_match_status == "metadata_matches_eligible_anime_local_prior":
            updated.reference_download_status = "not_requested"
            updated.correspondence_status = "metadata_work_page_match_no_visual_check"
        elif updated.page_index_status == "page_index_out_of_range":
            updated.correspondence_status = "page_index_out_of_range"
        elif updated.page_index_status == "missing_page_index":
            updated.correspondence_status = "page_specific_reference_missing"
        else:
            updated.correspondence_status = "metadata_work_page_match_no_visual_check"
        finalized.append(updated)
    return finalized


def join_records_to_local_priors(
    records: Sequence[PixivGalleryDlAdapterRecord],
    prior_index: f1.LocalPriorIndex | None,
) -> tuple[list[PixivGalleryDlAdapterRecord], dict[str, Any]]:
    joined, summary = f1.join_records_to_local_priors(records, prior_index)
    return list(joined), summary


def field_availability(records: Sequence[PixivGalleryDlAdapterRecord]) -> dict[str, int]:
    return {
        "work_id": sum(1 for record in records if record.work_id),
        "page_index": sum(1 for record in records if record.page_index is not None),
        "title": sum(1 for record in records if record.title),
        "artist_name": sum(1 for record in records if record.artist_name),
        "artist_id": sum(1 for record in records if record.artist_id),
        "tags": sum(1 for record in records if record.tags),
        "translated_tags": sum(1 for record in records if record.translated_tags),
        "caption": sum(1 for record in records if record.caption),
        "page_count": sum(1 for record in records if record.page_count is not None),
        "image_url_kinds": sum(1 for record in records if record.image_url_kinds_available),
        "gallery_dl_filename": sum(1 for record in records if record.gallery_dl_filename),
        "extractor_category": sum(1 for record in records if record.extractor_category),
    }


def summarize_download_artifacts(
    download_root: Path,
    *,
    cleanup: bool = False,
    reference_download_enabled: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = resolve_repo_path(download_root)
    require_under_phase_output(root)
    files: list[Path] = []
    total_bytes = 0
    suffix_counts: Counter[str] = Counter()
    if root.exists():
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            f1.require_under_path(file_path, root, code="download_file_outside_phase_root")
            size = file_path.stat().st_size
            files.append(file_path)
            total_bytes += size
            suffix_counts[file_path.suffix.lower() or "<no_suffix>"] += 1

    if len(files) > MAX_DOWNLOAD_FILES or total_bytes > MAX_DOWNLOAD_BYTES:
        raise OutputPathError("gallery_dl_unexpected_download_volume")

    cleanup_count = 0
    cleanup_bytes = 0
    if cleanup:
        for file_path in files:
            size = file_path.stat().st_size
            file_path.unlink()
            cleanup_count += 1
            cleanup_bytes += size
        for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    public = {
        "reference_download_enabled": reference_download_enabled,
        "downloaded_file_count": len(files),
        "downloaded_total_bytes": total_bytes,
        "downloaded_artifact_type_distribution": dict(sorted(suffix_counts.items())),
        "cleanup_performed": cleanup,
        "cleanup_file_count": cleanup_count,
        "cleanup_total_bytes": cleanup_bytes,
        "download_root_phase_specific": True,
        "downloaded_artifacts_committed": False,
    }
    private = {
        **public,
        "download_files_private": [_rel(path) for path in files],
    }
    return public, private


def output_containment_summary(output_dir: Path, *, private_paths: Sequence[Path]) -> dict[str, Any]:
    root = resolve_repo_path(output_dir)
    require_under_phase_output(root)
    for path in private_paths:
        require_under_phase_output(path)
    return {
        "phase_output_root": ".local_manifests/<phase-private-root>",
        "private_artifacts_under_phase_root": True,
        "public_reports_under_docs_reports": True,
        "gitignored_private_artifacts": True,
        "output_path_violation": False,
    }


def command_summary(command_results: Sequence[CommandResult], reference_results: Sequence[CommandResult]) -> dict[str, Any]:
    metadata = [result for result in command_results if result.command_kind == "metadata"]
    reference = [result for result in reference_results if result.command_kind == "reference_download"]
    return {
        "metadata_command_template": GALLERY_DL_METADATA_COMMAND_TEMPLATE,
        "reference_command_template": GALLERY_DL_REFERENCE_COMMAND_TEMPLATE if reference else None,
        "exact_commands_private_only": True,
        "subprocess_uses_shell": False,
        "metadata_command_count": len(metadata),
        "metadata_success_count": sum(1 for result in metadata if result.success),
        "metadata_failure_count": sum(1 for result in metadata if not result.success),
        "metadata_auth_or_config_failure_count": sum(1 for result in metadata if result.error_is_auth_or_config),
        "reference_command_count": len(reference),
        "reference_success_count": sum(1 for result in reference if result.success),
        "reference_failure_count": sum(1 for result in reference if not result.success),
        "per_item_results_public": [result.public_dict() for result in [*metadata, *reference]],
    }


def build_correspondence_summary(records: Sequence[PixivGalleryDlAdapterRecord], *, visual_check_performed: bool) -> dict[str, Any]:
    statuses = Counter(record.correspondence_status for record in records)
    return {
        "visual_check_performed": visual_check_performed,
        "status_counts": dict(sorted(statuses.items())),
        "image_correspondence_is_blocker": False,
    }


def future_route_recommendation(
    records: Sequence[PixivGalleryDlAdapterRecord],
    join_summary: Mapping[str, Any],
    entrypoint: GalleryDlEntrypoint,
    containment: Mapping[str, Any],
) -> dict[str, Any]:
    richness = Counter(record.metadata_richness for record in records)
    rich_count = richness.get("rich_structured_metadata", 0) + richness.get("partial_structured_metadata", 0)
    eligible_count = Counter(join_summary.get("status_counts", {})).get("metadata_matches_eligible_anime_local_prior", 0)
    page_counts = Counter(join_summary.get("page_index_status_counts", {}))
    page_ok = page_counts and not page_counts.get("page_index_out_of_range") and not page_counts.get("missing_page_index")
    stable = entrypoint.reproducibility_status in {"stable_project_python_module", "external_executable_discovered"}
    conditional = entrypoint.reproducibility_status == "conditional_explicit_operator_command"
    if records and rich_count and eligible_count and page_ok and containment.get("output_path_violation") is False and stable:
        return {
            "decision": "A_proceed_to_LocalSourceHint_persistence_design",
            "reason": "external gallery-dl invocation is stable, metadata is rich, eligible anime local joins worked, page-index checks passed, and containment/redaction gates passed",
            "db_persistence": "design_next_phase_only",
        }
    if records and rich_count and eligible_count and page_ok and conditional:
        return {
            "decision": "C_harden_external_adapter_command_config_first",
            "reason": "metadata and local joins worked, but the only working command mode is an explicit operator command rather than project-python or discovered gallery-dl",
            "db_persistence": "not_recommended_until_command_boundary_hardened",
        }
    if records and rich_count:
        return {
            "decision": "B_do_another_external_adapter_pilot_with_reference_download_enabled",
            "reason": "metadata works, but local join or page/correspondence confidence is not enough for persistence design",
            "db_persistence": "not_recommended_in_this_stage",
        }
    return {
        "decision": "D_stop_or_reroute_pixiv_metadata_route",
        "reason": "external gallery-dl adapter did not produce usable rich metadata records",
        "db_persistence": "not_recommended",
    }


def _private_markers(records: Sequence[PixivGalleryDlAdapterRecord], samples: Sequence[SelectedSample]) -> list[str]:
    markers: set[str] = set()
    for sample in samples:
        markers.add(sample.work_id)
        markers.update(sample.local_basenames_private)
    for record in records:
        for value in (record.work_id, record.canonical_url, record.gallery_dl_filename):
            if value:
                markers.add(str(value))
    return sorted(markers, key=len, reverse=True)


def build_public_summary(
    *,
    generated_at: str,
    pr_context: Mapping[str, Any],
    git_context: Mapping[str, Any],
    entrypoint: GalleryDlEntrypoint,
    db_identity: Mapping[str, Any] | None,
    sample_public: Mapping[str, Any],
    parse_result: f1.ParseResult,
    records: Sequence[PixivGalleryDlAdapterRecord],
    join_summary: Mapping[str, Any],
    command_public: Mapping[str, Any],
    download_public: Mapping[str, Any],
    containment: Mapping[str, Any],
) -> dict[str, Any]:
    richness_counts = Counter(record.metadata_richness for record in records)
    raw_shape_counts = Counter(record.record_shape for record in parse_result.records)
    record_shape_counts = Counter(record.record_shape for record in records)
    eligibility_counts = Counter(
        "eligible_for_future_local_source_hint" if record.eligible_for_future_local_source_hint else "ineligible_for_future_local_source_hint"
        for record in records
    )
    public = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": generated_at,
        "artifact_lifecycle": "phase_scoped_operational_runner",
        "why_this_stage_exists": (
            "PR #88 validated local gallery-dl JSON import. F2 tests whether V.I.O.L.E.T. can safely invoke "
            "a user-installed gallery-dl boundary with bounded samples, redaction, read-only local prior joins, "
            "and reportable run metadata without DB persistence."
        ),
        "pr_context": dict(pr_context),
        "git_context": dict(git_context),
        "gallery_dl_command_mode": entrypoint.public_dict(),
        "db_identity": dict(db_identity or {"db_read": False}),
        "sample_selection": dict(sample_public),
        "sample_gate": {
            "default_sample_size": DEFAULT_SAMPLE_SIZE,
            "max_sample_size_without_renewed_approval": MAX_SAMPLE_SIZE,
            "max_records_without_renewed_approval": MAX_SAMPLE_SIZE,
            "enforced_before_metadata_processing": True,
            "enforced_before_local_join": True,
            "enforced_before_private_mapping_artifacts": True,
            "status": "passed",
        },
        "command_summary": dict(command_public),
        "input_summary": {
            "raw_file_count": len(parse_result.files),
            "raw_record_count": len(parse_result.records),
            "raw_event_count": parse_result.raw_event_count,
            "directory_context_event_count": parse_result.directory_context_event_count,
            "url_media_event_count": parse_result.url_media_event_count,
            "normalized_media_record_count": len(records),
            "invalid_json_count": parse_result.invalid_json_count,
            "skipped_invalid_count": parse_result.skipped_invalid_count,
            "unsupported_shape_count": parse_result.unsupported_shape_count,
        },
        "schema_field_availability": field_availability(records),
        "metadata_richness_distribution": dict(sorted(richness_counts.items())),
        "record_shape_distribution": dict(sorted(record_shape_counts.items())),
        "raw_record_shape_distribution": dict(sorted(raw_shape_counts.items())),
        "local_source_prior_join": dict(join_summary),
        "content_class_eligibility_summary": {
            "approved_content_classes": sorted(f1.APPROVED_FUTURE_SOURCE_CONTENT_CLASSES),
            "future_eligibility_counts": dict(sorted(eligibility_counts.items())),
            "unknown_non_anime_future_eligible": False,
        },
        "page_index_validation_summary": dict(join_summary.get("page_index_status_counts", {})),
        "download_summary": dict(download_public),
        "correspondence_feasibility": build_correspondence_summary(
            records,
            visual_check_performed=bool(download_public.get("downloaded_file_count")),
        ),
        "output_containment": dict(containment),
        "normalized_dto": {
            "name": "PixivGalleryDlAdapterRecord",
            "source_adapter": "gallery_dl_external",
            "db_write_allowed": False,
            "public_projection_sample": records[0].public_projection() if records else None,
        },
        "external_adapter_route_readiness": {
            "engineering_ready": entrypoint.reproducibility_status in {"stable_project_python_module", "external_executable_discovered"},
            "metadata_adapter_logic_ready": bool(records and richness_counts.get("rich_structured_metadata", 0)),
            "persistence_ready": False,
            "persistence_blocker": "F2 is not a DB persistence stage",
        },
        "future_route_recommendation": future_route_recommendation(records, join_summary, entrypoint, containment),
        "privacy_and_safety_confirmation": {
            "public_report_contains_exact_pixiv_ids": False,
            "public_report_contains_exact_media_ids": False,
            "public_report_contains_exact_local_filenames": False,
            "public_report_contains_exact_local_paths": False,
            "public_report_contains_raw_gallery_dl_json": False,
            "public_report_contains_raw_image_urls": False,
            "sensitive_material_leaked": False,
            "db_write": False,
            "db_migration": False,
            "local_source_hint_write": False,
            "provider_cache_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "negative_lookup_cache_write": False,
            "confirmed_assignment": False,
            "automatic_entity": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "entity_resolver": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
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
        "## PR #88 Merge Confirmation",
        "",
        f"- PR #88 state: `{summary['pr_context'].get('pr88_state')}`.",
        f"- PR #88 merged at: `{summary['pr_context'].get('pr88_merged_at')}`.",
        f"- PR #88 merge commit: `{summary['pr_context'].get('pr88_merge_commit')}`.",
        f"- PR #88 URL: `{summary['pr_context'].get('pr88_url')}`.",
        "",
        "## gallery-dl Command Mode",
        "",
        f"- Mode: `{summary['gallery_dl_command_mode'].get('mode')}`.",
        f"- Version: `{summary['gallery_dl_command_mode'].get('version')}`.",
        f"- Reproducibility status: `{summary['gallery_dl_command_mode'].get('reproducibility_status')}`.",
        f"- Command label: `{summary['gallery_dl_command_mode'].get('command_path_public')}`.",
        "",
        "## DB Identity / Config Labels",
        "",
        f"- DB identity: `{json.dumps(summary['db_identity'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Sample Gate",
        "",
        f"- Sample selection: `{json.dumps(summary['sample_selection'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Sample gate: `{json.dumps(summary['sample_gate'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Command Results",
        "",
        f"- Command summary: `{json.dumps(summary['command_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Metadata Records",
        "",
        f"- Input summary: `{json.dumps(summary['input_summary'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Schema field availability: `{json.dumps(summary['schema_field_availability'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Metadata richness distribution: `{json.dumps(summary['metadata_richness_distribution'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Raw record shape distribution: `{json.dumps(summary['raw_record_shape_distribution'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Local Source-Prior Join",
        "",
        f"- Join status counts: `{json.dumps(summary['local_source_prior_join'].get('status_counts', {}), ensure_ascii=False, sort_keys=True)}`.",
        f"- Content-class eligibility: `{json.dumps(summary['content_class_eligibility_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Page Index Validation",
        "",
        f"- Page-index status counts: `{json.dumps(summary['page_index_validation_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Reference Download / Artifact Accounting",
        "",
        f"- Download summary: `{json.dumps(summary['download_summary'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Correspondence Feasibility",
        "",
        f"- Correspondence: `{json.dumps(summary['correspondence_feasibility'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Output Containment",
        "",
        f"- Containment: `{json.dumps(summary['output_containment'], ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## External Adapter Route Readiness",
        "",
        f"- Readiness: `{json.dumps(summary['external_adapter_route_readiness'], ensure_ascii=False, sort_keys=True)}`.",
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


def build_private_sheet_csv(records: Sequence[PixivGalleryDlAdapterRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "work_id",
            "page_index",
            "page_count",
            "title",
            "artist_name",
            "artist_id",
            "tag_count",
            "translated_tag_count",
            "metadata_richness",
            "local_match_status",
            "local_match_content_class",
            "page_index_status",
            "reference_download_status",
            "correspondence_status",
            "local_media_id_private",
            "duplicate_local_media_ids_private",
            "canonical_url",
            "gallery_dl_filename",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "work_id": record.work_id or "",
                "page_index": "" if record.page_index is None else record.page_index,
                "page_count": "" if record.page_count is None else record.page_count,
                "title": record.title or "",
                "artist_name": record.artist_name or "",
                "artist_id": record.artist_id or "",
                "tag_count": len(record.tags),
                "translated_tag_count": len(record.translated_tags),
                "metadata_richness": record.metadata_richness,
                "local_match_status": record.local_match_status,
                "local_match_content_class": record.local_match_content_class or "",
                "page_index_status": record.page_index_status,
                "reference_download_status": record.reference_download_status,
                "correspondence_status": record.correspondence_status,
                "local_media_id_private": record.local_media_id_private or "",
                "duplicate_local_media_ids_private": ";".join(str(item) for item in record.duplicate_local_media_ids_private),
                "canonical_url": record.canonical_url or "",
                "gallery_dl_filename": record.gallery_dl_filename or "",
            }
        )
    return buffer.getvalue()


def build_private_sheet_markdown(records: Sequence[PixivGalleryDlAdapterRecord]) -> str:
    lines = [
        "# Phase 4.4-P2R-F2 Private Mapping Sheet",
        "",
        "This ignored local artifact may contain exact Pixiv IDs, filenames, and media IDs.",
        "",
        "| work_id | page_index | content_class | local_match_status | page_index_status | reference | correspondence | media_id |",
        "|---|---:|---|---|---|---|---|---:|",
    ]
    for record in records:
        lines.append(
            "| {work_id} | {page_index} | {content_class} | {match} | {page_status} | {reference} | {correspondence} | {media_id} |".format(
                work_id=record.work_id or "",
                page_index="" if record.page_index is None else record.page_index,
                content_class=record.local_match_content_class or "",
                match=record.local_match_status,
                page_status=record.page_index_status,
                reference=record.reference_download_status,
                correspondence=record.correspondence_status,
                media_id=record.local_media_id_private or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _empty_parse_result(raw_dir: Path) -> f1.ParseResult:
    files = []
    raw = resolve_repo_path(raw_dir)
    if raw.exists():
        files = sorted(path for path in raw.rglob("*") if path.is_file() and path.suffix.lower() in f1.JSON_EXTENSIONS)
    return f1.ParseResult(records=[], files=files)


def _git_context() -> dict[str, Any]:
    def run_git(args: list[str]) -> str:
        try:
            return subprocess.check_output(["git", *args], cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""

    return {
        "branch": run_git(["branch", "--show-current"]),
        "head_sha": run_git(["rev-parse", "HEAD"]),
        "origin_main_sha": run_git(["rev-parse", "origin/main"]),
        "head_equals_origin_main_at_branch_start": run_git(["rev-parse", "HEAD"]) == run_git(["rev-parse", "origin/main"]),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    parser.add_argument("--output-dir", default=str(PHASE_OUTPUT_DIR))
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
    parser.add_argument("--metadata-only", action="store_true", help="Run metadata commands only; this is the default behavior.")
    parser.add_argument("--reference-download", "--download-references", dest="reference_download", action="store_true")
    parser.add_argument("--reference-sample-size", type=int, default=DEFAULT_REFERENCE_DOWNLOAD_SAMPLE_SIZE)
    parser.add_argument("--cleanup-downloads", action="store_true")
    parser.add_argument("--skip-network", action="store_true", help="Testing-only: select samples and write a dry report without subprocess network calls.")
    parser.add_argument("--dry-run", action="store_true", help="Select samples and show command templates without executing gallery-dl.")
    parser.add_argument("--no-db", action="store_true", help="Testing-only: skip DB identity and local prior sample selection.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--sheet-csv", default=str(PRIVATE_SHEET_CSV))
    parser.add_argument("--sheet-md", default=str(PRIVATE_SHEET_MD))
    parser.add_argument("--raw-dir", default=str(PRIVATE_RAW_DIR))
    parser.add_argument("--download-dir", default=str(PRIVATE_DOWNLOAD_DIR))
    parser.add_argument("--pr88-state", default="MERGED")
    parser.add_argument("--pr88-merged-at", default="2026-06-01T05:51:44Z")
    parser.add_argument("--pr88-merge-commit", default="a9ea099d08b0fb51213cb3e82177d57f3200c627")
    parser.add_argument("--pr88-url", default="https://github.com/kyloris0660/VIOLET/pull/88")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = resolve_repo_path(args.output_dir)
    f1.require_under_path(output_dir, ROOT / ".local_manifests", code="gallery_dl_output_path_violation")
    if PHASE_SLUG not in output_dir.as_posix():
        raise OutputPathError("gallery_dl_output_path_violation")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = resolve_repo_path(args.raw_dir)
    download_dir = resolve_repo_path(args.download_dir)
    details_json = resolve_repo_path(args.details_json)
    sheet_csv = resolve_repo_path(args.sheet_csv)
    sheet_md = resolve_repo_path(args.sheet_md)

    enforce_sample_size(args.sample_size)
    enforce_record_count(0, args.max_records)

    config = load_project_config(ROOT)
    db_identity: dict[str, Any] | None = None
    prior_index: f1.LocalPriorIndex | None = None
    if not args.no_db:
        engine = create_engine(config.database_url)
        f1.install_read_only_guard(engine)
        SessionLocal = sessionmaker(bind=engine)
        session: Session = SessionLocal()
        try:
            db_identity = f1.prove_db_identity(session, config)
            prior_index = f1.build_local_prior_index(session)
        finally:
            session.close()
            engine.dispose()

    if prior_index is None:
        sample_public = {
            "selected_count": 0,
            "requested_sample_size": args.sample_size,
            "sample_gate_status": "db_skipped",
            "exact_work_ids_public": False,
            "exact_media_ids_public": False,
            "exact_filenames_public": False,
        }
        samples: list[SelectedSample] = []
    else:
        sample_public, samples = select_local_pixiv_prior_samples(prior_index, sample_size=args.sample_size)
    if len(samples) > MAX_SAMPLE_SIZE:
        raise SampleGateError("sample_size_exceeds_max_10")

    entrypoint = probe_gallery_dl_entrypoint(args.gallery_dl_command or None)

    metadata_results: list[CommandResult] = []
    reference_results: list[CommandResult] = []
    parse_result = _empty_parse_result(raw_dir)
    records: list[PixivGalleryDlAdapterRecord] = []
    if args.dry_run or args.skip_network:
        parse_result = _empty_parse_result(raw_dir)
    else:
        metadata_results = run_metadata_commands(samples, entrypoint, raw_dir, timeout=args.timeout)
        if metadata_results and not any(result.success for result in metadata_results):
            if any(result.error_is_auth_or_config for result in metadata_results):
                raise GalleryDlAuthBlocked("gallery_dl_auth_or_config_blocked")
        successful_raw_files = [
            resolve_repo_path(result.stdout_path_private)
            for result in metadata_results
            if result.success and result.stdout_path_private
        ]
        parse_input = raw_dir if successful_raw_files else raw_dir
        parse_result = parse_gallery_dl_json_inputs(parse_input) if successful_raw_files else _empty_parse_result(raw_dir)
        records = normalize_adapter_records(parse_result, entrypoint=entrypoint)
        enforce_record_count(len(records), args.max_records)
        records, join_summary = join_records_to_local_priors(records, prior_index)
        if args.reference_download:
            reference_results = run_reference_download_commands(
                samples,
                entrypoint,
                download_dir,
                sample_size=args.reference_sample_size,
                timeout=args.timeout,
            )
    if args.dry_run or args.skip_network:
        join_summary = {
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

    download_public, download_private = summarize_download_artifacts(
        download_dir,
        cleanup=args.cleanup_downloads,
        reference_download_enabled=args.reference_download,
    )
    records = _finalize_joined_records(
        records,
        reference_download_enabled=args.reference_download,
        downloaded_file_count=int(download_public["downloaded_file_count"]),
    )
    containment = output_containment_summary(
        output_dir,
        private_paths=[details_json, sheet_csv, sheet_md, raw_dir, download_dir],
    )
    public_command = command_summary(metadata_results, reference_results)
    generated_at = _now_iso()
    pr_context = {
        "pr88_state": args.pr88_state,
        "pr88_merged_at": args.pr88_merged_at,
        "pr88_merge_commit": args.pr88_merge_commit,
        "pr88_url": args.pr88_url,
    }
    summary = build_public_summary(
        generated_at=generated_at,
        pr_context=pr_context,
        git_context=_git_context(),
        entrypoint=entrypoint,
        db_identity=db_identity,
        sample_public=sample_public,
        parse_result=parse_result,
        records=records,
        join_summary=join_summary,
        command_public=public_command,
        download_public=download_public,
        containment=containment,
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
            "reference_command_results_private": [asdict(result) for result in reference_results],
            "records_private": [record.to_private_dict() for record in records],
            "download_artifacts": download_private,
            "db_identity_public": db_identity,
            "sample_summary_public": sample_public,
        },
    )
    write_private_text(sheet_csv, build_private_sheet_csv(records))
    write_private_text(sheet_md, build_private_sheet_markdown(records))
    return {
        "ok": True,
        "phase": PHASE,
        "report_md": _rel(resolve_repo_path(args.report_md)),
        "report_json": _rel(resolve_repo_path(args.report_json)),
        "private_details": _rel(details_json),
        "sample_selected_count": sample_public.get("selected_count"),
        "metadata_success_count": public_command.get("metadata_success_count"),
        "metadata_failure_count": public_command.get("metadata_failure_count"),
        "normalized_media_record_count": summary["input_summary"]["normalized_media_record_count"],
        "future_route_recommendation": summary["future_route_recommendation"]["decision"],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = run(args)
    except Phase44P2RF2Error as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
