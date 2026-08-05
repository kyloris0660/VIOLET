"""Build and serve the immutable SCV2-SV1B static manual-acceptance packet.

This presentation-only tool deliberately has no database or application imports.  A build
validates the already accepted v5-r2 manifest/binding, copies the local thumbnails exposed
by that already-running harness, and atomically publishes a new Git-bound v5-r3 packet.
Browsing only reads packet files.  The sole write route is the exclusive result export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping


PACKET_VERSION = "sv1b_static_manual_acceptance_packet_v5_r3"
BINDING_VERSION = "sv1b_static_manual_acceptance_binding_v5_r3"
BINDING_NAME = "manual-acceptance-static-final-binding-v5-r3-proof.json"
SOURCE_BINDING_NAME = "manual-acceptance-harness-final-binding-v5-proof.json"
SOURCE_AUDIT_NAME = "manual-acceptance-repair-v5-validation-proof.json"
SOURCE_MANIFEST = "manual-acceptance/case-manifest-private.json"
STATIC_DIR = "manual-acceptance-static"
RESULT_RELATIVE = "manual-acceptance/manual-acceptance-result.json"
EXPECTED_CASE_IDS = tuple(
    [f"A{i:02d}" for i in range(1, 13)]
    + [f"B{i:02d}" for i in range(1, 9)]
    + [f"C{i:02d}" for i in range(1, 7)]
    + [f"D{i:02d}" for i in range(1, 9)]
    + [f"E{i:02d}" for i in range(1, 7)]
)
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class StaticAcceptanceError(RuntimeError):
    """Fail-closed static-packet contract error."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def payload_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StaticAcceptanceError(f"static_packet_json_invalid:{path.name}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().casefold()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise StaticAcceptanceError("static_packet_git_head_invalid")
    return value


def _validate_self_fingerprint(
    payload: Mapping[str, Any], field: str, error: str
) -> str:
    declared = str(payload.get(field) or "")
    body = dict(payload)
    body.pop(field, None)
    if declared != payload_fingerprint(body):
        raise StaticAcceptanceError(error)
    return declared


def _validate_cases(cases: Any) -> list[dict[str, Any]]:
    if not isinstance(cases, list) or len(cases) != len(EXPECTED_CASE_IDS):
        raise StaticAcceptanceError("static_packet_case_count_invalid")
    ids = [str(row.get("case_id") or "") for row in cases if isinstance(row, dict)]
    if tuple(ids) != EXPECTED_CASE_IDS or len(set(ids)) != len(ids):
        raise StaticAcceptanceError("static_packet_case_membership_invalid")
    required = {
        "case_id",
        "category",
        "title",
        "expected_behavior",
        "actual_result",
        "provenance",
        "safe_media_label",
    }
    for row in cases:
        if not required.issubset(row):
            raise StaticAcceptanceError(
                f"static_packet_case_shape_invalid:{row.get('case_id')}"
            )
        label = str(row["safe_media_label"])
        if len(label) != 64 or any(ch not in "0123456789abcdef" for ch in label):
            raise StaticAcceptanceError(
                f"static_packet_media_label_invalid:{row['case_id']}"
            )
    return cases


def validate_v5_r2_source(source_root: Path) -> dict[str, Any]:
    """Validate v5-r2 by bytes and canonical proofs without database access."""

    source_root = source_root.resolve()
    binding_path = source_root / SOURCE_BINDING_NAME
    audit_path = source_root / SOURCE_AUDIT_NAME
    manifest_path = source_root / SOURCE_MANIFEST
    result_path = source_root / RESULT_RELATIVE
    if result_path.exists():
        raise StaticAcceptanceError("static_packet_source_owner_result_exists")
    binding = read_json(binding_path)
    audit = read_json(audit_path)
    cases = _validate_cases(read_json(manifest_path))
    if not isinstance(binding, dict) or binding.get("passed") is not True:
        raise StaticAcceptanceError("static_packet_source_binding_invalid")
    if not isinstance(audit, dict) or audit.get("passed") is not True:
        raise StaticAcceptanceError("static_packet_source_audit_invalid")

    bindings = binding.get("bindings")
    if not isinstance(bindings, dict):
        raise StaticAcceptanceError("static_packet_source_bindings_missing")
    source_binding_fingerprint = _validate_self_fingerprint(
        bindings,
        "binding_fingerprint",
        "static_packet_source_binding_fingerprint_invalid",
    )
    source_audit_fingerprint = _validate_self_fingerprint(
        audit,
        "proof_fingerprint",
        "static_packet_source_audit_fingerprint_invalid",
    )
    manifest_sha = file_sha256(manifest_path)
    manifest_fingerprint = payload_fingerprint(cases)
    if manifest_sha != bindings.get("acceptance_case_manifest_file_sha256"):
        raise StaticAcceptanceError("static_packet_source_manifest_sha_invalid")
    if manifest_fingerprint != bindings.get("acceptance_case_manifest_fingerprint"):
        raise StaticAcceptanceError("static_packet_source_manifest_fingerprint_invalid")
    audit_binding = bindings.get("audit_validation") or {}
    if (
        file_sha256(audit_path) != audit_binding.get("proof_file_sha256")
        or source_audit_fingerprint != audit_binding.get("proof_fingerprint")
        or audit.get("git_head") != bindings.get("git_head")
        or audit.get("git_head") != audit_binding.get("git_head")
    ):
        raise StaticAcceptanceError("static_packet_source_audit_binding_invalid")

    protected = bindings.get("protected_source_file_sha256")
    if not isinstance(protected, dict) or not protected:
        raise StaticAcceptanceError("static_packet_source_inventory_missing")
    for relative, expected_sha in protected.items():
        path = (source_root / str(relative)).resolve()
        if source_root != path.parent and source_root not in path.parents:
            raise StaticAcceptanceError("static_packet_source_inventory_escape")
        if not path.is_file() or file_sha256(path) != expected_sha:
            raise StaticAcceptanceError(
                f"static_packet_source_inventory_drift:{relative}"
            )
    return {
        "source_root": source_root,
        "binding": binding,
        "bindings": bindings,
        "cases": cases,
        "manifest_sha256": manifest_sha,
        "manifest_fingerprint": manifest_fingerprint,
        "source_binding_file_sha256": file_sha256(binding_path),
        "source_binding_fingerprint": source_binding_fingerprint,
        "source_audit_file_sha256": file_sha256(audit_path),
        "source_audit_fingerprint": source_audit_fingerprint,
        "protected_source_file_sha256": dict(sorted(protected.items())),
    }


def localhost_asset_loader(base_url: str) -> Callable[[str], tuple[bytes, str]]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise StaticAcceptanceError("static_packet_asset_source_not_localhost")
    clean = base_url.rstrip("/")

    def load(label: str) -> tuple[bytes, str]:
        url = f"{clean}/media/{urllib.parse.quote(label, safe='')}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                response_target = urllib.parse.urlsplit(response.geturl())
                if response_target.hostname not in {"127.0.0.1", "localhost"}:
                    raise StaticAcceptanceError(
                        "static_packet_asset_redirected_off_localhost"
                    )
                content_type = response.headers.get_content_type().casefold()
                data = response.read(25 * 1024 * 1024 + 1)
                if response.status != 200:
                    raise StaticAcceptanceError(
                        f"static_packet_asset_http_status:{response.status}"
                    )
        except (OSError, TimeoutError) as exc:
            raise StaticAcceptanceError("static_packet_local_asset_unavailable") from exc
        if content_type not in IMAGE_TYPES or not data or len(data) > 25 * 1024 * 1024:
            raise StaticAcceptanceError("static_packet_asset_payload_invalid")
        return data, content_type

    return load


def _packet_case(row: Mapping[str, Any], asset_path: str) -> dict[str, Any]:
    return {
        "case_id": row["case_id"],
        "group": str(row["case_id"])[0],
        "category": row["category"],
        "title": row["title"],
        "expected_behavior": row["expected_behavior"],
        "actual_result": row["actual_result"],
        "provenance": row["provenance"],
        "safe_media_label": row["safe_media_label"],
        "asset_path": asset_path,
    }


def build_static_packet(
    source_root: Path,
    output_root: Path,
    *,
    exact_git_head: str,
    asset_loader: Callable[[str], tuple[bytes, str]],
) -> dict[str, Any]:
    """Atomically create one non-overwriting v5-r3 packet."""

    source = validate_v5_r2_source(source_root)
    output_root = output_root.resolve()
    if output_root.exists():
        raise StaticAcceptanceError("static_packet_output_already_exists")
    if len(exact_git_head) != 40 or any(
        ch not in "0123456789abcdef" for ch in exact_git_head
    ):
        raise StaticAcceptanceError("static_packet_git_head_invalid")
    temporary = output_root.with_name(f".{output_root.name}.tmp-{uuid.uuid4().hex}")
    if temporary.exists():
        raise StaticAcceptanceError("static_packet_temporary_exists")
    static_root = temporary / STATIC_DIR
    assets_root = static_root / "assets"
    assets_root.mkdir(parents=True)
    try:
        cases = source["cases"]
        copied_manifest = temporary / SOURCE_MANIFEST
        copied_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source["source_root"] / SOURCE_MANIFEST, copied_manifest)
        copied_manifest_sha = file_sha256(copied_manifest)
        if copied_manifest_sha != source["manifest_sha256"]:
            raise StaticAcceptanceError("static_packet_manifest_copy_drift")

        asset_paths: dict[str, str] = {}
        asset_file_sha256: dict[str, str] = {}
        for label in dict.fromkeys(str(row["safe_media_label"]) for row in cases):
            data, content_type = asset_loader(label)
            suffix = IMAGE_TYPES.get(content_type.casefold())
            if suffix is None:
                raise StaticAcceptanceError("static_packet_asset_type_invalid")
            relative = f"assets/{label}{suffix}"
            target = static_root / relative
            target.write_bytes(data)
            asset_paths[label] = relative
            asset_file_sha256[relative] = file_sha256(target)

        packet_cases = [
            _packet_case(row, asset_paths[str(row["safe_media_label"])])
            for row in cases
        ]
        packet_payload = {
            "schema_version": PACKET_VERSION,
            "git_head": exact_git_head,
            "case_manifest_sha256": copied_manifest_sha,
            "case_manifest_fingerprint": source["manifest_fingerprint"],
            "case_count": len(packet_cases),
            "case_ids": list(EXPECTED_CASE_IDS),
            "group_counts": {group: sum(1 for row in packet_cases if row["group"] == group) for group in "ABCDE"},
            "cases": packet_cases,
            "result_path_relative": RESULT_RELATIVE,
            "source_v5_r2_binding_fingerprint": source["source_binding_fingerprint"],
        }
        packet_payload_fingerprint = payload_fingerprint(packet_payload)
        binding_fields = {
            "binding_version": BINDING_VERSION,
            "git_head": exact_git_head,
            "case_manifest_sha256": copied_manifest_sha,
            "case_manifest_fingerprint": source["manifest_fingerprint"],
            "case_count": len(packet_cases),
            "case_ids_fingerprint": payload_fingerprint(list(EXPECTED_CASE_IDS)),
            "review_packet_payload_fingerprint": packet_payload_fingerprint,
            "asset_file_sha256": dict(sorted(asset_file_sha256.items())),
            "asset_membership_fingerprint": payload_fingerprint(
                dict(sorted(asset_file_sha256.items()))
            ),
            "source_v5_r2_binding_fingerprint": source["source_binding_fingerprint"],
            "source_v5_r2_binding_file_sha256": source["source_binding_file_sha256"],
            "source_v5_r2_audit_fingerprint": source["source_audit_fingerprint"],
            "source_v5_r2_audit_file_sha256": source["source_audit_file_sha256"],
            "source_v5_r2_protected_file_sha256": source[
                "protected_source_file_sha256"
            ],
            "database_access_count": 0,
            "provider_request_count": 0,
            "llm_request_count": 0,
            "external_media_download_count": 0,
        }
        binding_fingerprint = payload_fingerprint(binding_fields)
        packet = dict(packet_payload)
        packet["binding_fingerprint"] = binding_fingerprint
        write_json(static_root / "review-packet.json", packet)
        (static_root / "index.html").write_text(STATIC_HTML, encoding="utf-8")

        checksummed = [
            "index.html",
            "review-packet.json",
            *sorted(asset_file_sha256),
        ]
        checksums = {
            "schema_version": "sv1b_static_manual_acceptance_checksums_v1",
            "files": {
                relative: file_sha256(static_root / relative)
                for relative in checksummed
            },
        }
        write_json(static_root / "checksums.json", checksums)
        binding = dict(binding_fields)
        binding.update(
            {
                "binding_fingerprint": binding_fingerprint,
                "review_packet_file_sha256": file_sha256(
                    static_root / "review-packet.json"
                ),
                "checksums_file_sha256": file_sha256(
                    static_root / "checksums.json"
                ),
                "static_index_file_sha256": file_sha256(static_root / "index.html"),
                "result_path_relative": RESULT_RELATIVE,
                "manual_acceptance_status": "pending_user",
                "passed": True,
            }
        )
        binding["proof_fingerprint"] = payload_fingerprint(binding)
        write_json(temporary / BINDING_NAME, binding)

        # Re-check protected source bytes after every local asset was copied.
        source_after = validate_v5_r2_source(source_root)
        for field in (
            "source_binding_file_sha256",
            "source_audit_file_sha256",
            "manifest_sha256",
            "protected_source_file_sha256",
        ):
            if source_after[field] != source[field]:
                raise StaticAcceptanceError("static_packet_source_changed_during_build")
        output_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, output_root)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return validate_static_packet(output_root, expected_git_head=exact_git_head)


def validate_static_packet(
    output_root: Path, *, expected_git_head: str | None = None
) -> dict[str, Any]:
    output_root = output_root.resolve()
    static_root = output_root / STATIC_DIR
    binding_path = output_root / BINDING_NAME
    binding = read_json(binding_path)
    if not isinstance(binding, dict) or binding.get("passed") is not True:
        raise StaticAcceptanceError("static_packet_binding_invalid")
    proof_fingerprint = _validate_self_fingerprint(
        binding, "proof_fingerprint", "static_packet_binding_proof_invalid"
    )
    binding_fingerprint = str(binding.get("binding_fingerprint") or "")
    binding_fields = {
        key: value
        for key, value in binding.items()
        if key
        not in {
            "binding_fingerprint",
            "review_packet_file_sha256",
            "checksums_file_sha256",
            "static_index_file_sha256",
            "result_path_relative",
            "manual_acceptance_status",
            "passed",
            "proof_fingerprint",
        }
    }
    if payload_fingerprint(binding_fields) != binding_fingerprint:
        raise StaticAcceptanceError("static_packet_binding_fingerprint_invalid")
    if expected_git_head is not None and binding.get("git_head") != expected_git_head:
        raise StaticAcceptanceError("static_packet_binding_head_mismatch")

    checksums_path = static_root / "checksums.json"
    packet_path = static_root / "review-packet.json"
    index_path = static_root / "index.html"
    if (
        file_sha256(checksums_path) != binding.get("checksums_file_sha256")
        or file_sha256(packet_path) != binding.get("review_packet_file_sha256")
        or file_sha256(index_path) != binding.get("static_index_file_sha256")
    ):
        raise StaticAcceptanceError("static_packet_top_level_file_drift")
    checksums = read_json(checksums_path)
    files = checksums.get("files") if isinstance(checksums, dict) else None
    if not isinstance(files, dict) or not files:
        raise StaticAcceptanceError("static_packet_checksums_invalid")
    for relative, expected_sha in files.items():
        path = (static_root / str(relative)).resolve()
        if static_root != path.parent and static_root not in path.parents:
            raise StaticAcceptanceError("static_packet_checksum_path_escape")
        if not path.is_file() or file_sha256(path) != expected_sha:
            raise StaticAcceptanceError(f"static_packet_file_drift:{relative}")

    packet = read_json(packet_path)
    cases = _validate_cases(packet.get("cases") if isinstance(packet, dict) else None)
    packet_payload = dict(packet)
    packet_payload.pop("binding_fingerprint", None)
    if (
        packet.get("schema_version") != PACKET_VERSION
        or packet.get("git_head") != binding.get("git_head")
        or packet.get("binding_fingerprint") != binding_fingerprint
        or packet.get("case_manifest_sha256") != binding.get("case_manifest_sha256")
        or payload_fingerprint(packet_payload)
        != binding.get("review_packet_payload_fingerprint")
    ):
        raise StaticAcceptanceError("static_packet_review_packet_invalid")
    manifest_path = output_root / SOURCE_MANIFEST
    if (
        file_sha256(manifest_path) != binding.get("case_manifest_sha256")
        or payload_fingerprint(read_json(manifest_path))
        != binding.get("case_manifest_fingerprint")
    ):
        raise StaticAcceptanceError("static_packet_manifest_invalid")
    return {
        "passed": True,
        "git_head": binding["git_head"],
        "binding_fingerprint": binding_fingerprint,
        "binding_proof_fingerprint": proof_fingerprint,
        "binding_file_sha256": file_sha256(binding_path),
        "case_manifest_sha256": binding["case_manifest_sha256"],
        "review_packet_file_sha256": binding["review_packet_file_sha256"],
        "checksums_file_sha256": binding["checksums_file_sha256"],
        "case_count": len(cases),
        "image_count": len(cases),
        "unique_asset_count": len(binding["asset_file_sha256"]),
        "result_path": str(output_root / RESULT_RELATIVE),
    }


def normalize_submission(payload: Any, packet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StaticAcceptanceError("static_export_payload_invalid")
    submitted = payload.get("results")
    if not isinstance(submitted, list) or len(submitted) != len(EXPECTED_CASE_IDS):
        raise StaticAcceptanceError("static_export_result_count_invalid")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in submitted:
        if not isinstance(row, dict):
            raise StaticAcceptanceError("static_export_result_shape_invalid")
        case_id = str(row.get("case_id") or "")
        if case_id in by_id:
            raise StaticAcceptanceError("static_export_duplicate_case")
        by_id[case_id] = row
    if tuple(sorted(by_id)) != tuple(sorted(EXPECTED_CASE_IDS)):
        raise StaticAcceptanceError("static_export_case_membership_invalid")
    results: list[dict[str, str]] = []
    for case_id in EXPECTED_CASE_IDS:
        row = by_id[case_id]
        decision = str(row.get("decision") or "pending").casefold()
        if decision not in {"pass", "fail"}:
            raise StaticAcceptanceError("static_export_pending_cases")
        results.append(
            {
                "case_id": case_id,
                "decision": decision,
                "comment": str(row.get("comment") or "")[:2000],
            }
        )
    return {
        "schema_version": "sv1b_static_manual_acceptance_result_v1",
        "git_head": packet["git_head"],
        "binding_fingerprint": packet["binding_fingerprint"],
        "case_manifest_sha256": packet["case_manifest_sha256"],
        "case_ids": list(EXPECTED_CASE_IDS),
        "per_case_result": results,
        "overall_comment": str(payload.get("overall_comment") or "")[:5000],
        "manual_acceptance_status": "submitted_user_review",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def write_result_exclusive_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    temp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    lock_fd: int | None = None
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        if path.exists():
            raise StaticAcceptanceError("static_export_result_already_exists")
        data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ) + b"\n"
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except FileExistsError as exc:
        raise StaticAcceptanceError("static_export_locked") from exc
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
            lock.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)


class StaticAcceptanceHandler(BaseHTTPRequestHandler):
    server_version = "VioletStaticAcceptance/1"

    @property
    def packet_root(self) -> Path:
        return self.server.packet_root  # type: ignore[attr-defined]

    @property
    def output_root(self) -> Path:
        return self.server.output_root  # type: ignore[attr-defined]

    def _json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, relative: str) -> None:
        path = (self.packet_root / relative).resolve()
        if self.packet_root != path.parent and self.packet_root not in path.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if relative.startswith("assets/") else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._file("index.html")
        elif path in {"/review-packet.json", "/checksums.json"}:
            self._file(path[1:])
        elif path.startswith("/assets/"):
            self._file(path[1:])
        elif path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "static_only": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlsplit(self.path).path != "/api/export":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 512 * 1024:
                raise StaticAcceptanceError("static_export_payload_size_invalid")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            validated = validate_static_packet(self.output_root)
            packet = read_json(self.packet_root / "review-packet.json")
            result = normalize_submission(payload, packet)
            result_path = self.output_root / RESULT_RELATIVE
            write_result_exclusive_atomic(result_path, result)
            self._json(
                HTTPStatus.OK,
                {
                    "saved": True,
                    "relative_path": RESULT_RELATIVE,
                    "binding_fingerprint": validated["binding_fingerprint"],
                },
            )
        except (StaticAcceptanceError, UnicodeError, json.JSONDecodeError) as exc:
            code = HTTPStatus.CONFLICT if "already_exists" in str(exc) else HTTPStatus.BAD_REQUEST
            self._json(code, {"saved": False, "error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"static-manual-acceptance {self.address_string()} {format % args}")


def serve(output_root: Path, host: str, port: int) -> None:
    validated = validate_static_packet(output_root, expected_git_head=git_head(Path.cwd()))
    server = ThreadingHTTPServer((host, port), StaticAcceptanceHandler)
    server.packet_root = (output_root.resolve() / STATIC_DIR)  # type: ignore[attr-defined]
    server.output_root = output_root.resolve()  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "status": "STATIC_MANUAL_ACCEPTANCE_READY",
                "url": f"http://{host}:{port}/",
                **validated,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()


STATIC_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCV2-SV1B 静态人工验收</title>
<style>
:root{color-scheme:dark;--bg:#0b1120;--card:#172033;--line:#334155;--text:#e5e7eb;--muted:#94a3b8;--accent:#60a5fa;--ok:#34d399;--bad:#fb7185}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}button,input,textarea{font:inherit}
.top{position:sticky;top:0;z-index:5;background:#0b1120ed;border-bottom:1px solid var(--line);padding:12px 20px}.toprow,.tabs,.filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toprow{justify-content:space-between}.progress{font-weight:700;color:var(--accent)}
main{max-width:1180px;margin:auto;padding:18px}.intro{color:var(--muted);margin:6px 0 14px}.binding{font:12px ui-monospace,monospace;word-break:break-all;color:var(--muted)}
button{border:1px solid var(--line);border-radius:8px;background:#1e293b;color:var(--text);padding:7px 11px;cursor:pointer}button[aria-pressed=true]{border-color:var(--accent);color:#fff;background:#1d4ed8}button:disabled{opacity:.45;cursor:not-allowed}
.case{display:grid;grid-template-columns:minmax(220px,310px) 1fr;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}.case img{width:100%;height:280px;object-fit:contain;background:#050914;border-radius:8px}.case h2{margin:0 0 8px}.case h3{margin:14px 0 5px;font-size:14px;color:#cbd5e1}.meta{white-space:pre-wrap;overflow:auto;max-height:220px;background:#0b1120;padding:10px;border-radius:8px;font:12px/1.45 ui-monospace,monospace}.controls{display:flex;gap:10px;margin:12px 0}.choice{padding:7px 10px;border:1px solid var(--line);border-radius:7px}.comment,#overall{width:100%;min-height:64px;background:#0b1120;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:8px}.footer{border-top:1px solid var(--line);margin-top:24px;padding-top:18px}.status{margin-left:8px}.error{color:var(--bad)}.ok{color:var(--ok)}.hidden{display:none!important}
@media(max-width:720px){.case{grid-template-columns:1fr}.case img{height:240px}.top{position:static}}
</style></head>
<body><header class="top"><div class="toprow"><strong>SCV2-SV1B · 40-case 静态人工验收</strong><span id="progress" class="progress">已完成 0/40</span></div><div class="tabs" id="tabs" aria-label="Case 分组"></div><div class="filters" id="filters" aria-label="裁决筛选"></div></header>
<main><p class="intro">按组检查图片、关键证据与预期行为；未导出的选择只保存在此浏览器。40 项全部 PASS/FAIL 后才能导出。</p><div id="binding" class="binding">正在读取静态验收包…</div><div id="cases" aria-live="polite"></div><section class="footer"><h2>总体备注</h2><textarea id="overall" placeholder="可选总体备注"></textarea><p><button id="export" disabled>导出结果</button><span id="status" class="status"></span></p></section></main>
<script>
'use strict';
const GROUPS={A:'A · Pixiv metadata',B:'B · Creator identity',C:'C · Shared-name safety',D:'D · Localization',E:'E · Search'};
const FILTERS={all:'全部',pending:'待处理',pass:'PASS',fail:'FAIL'};
let packet,currentGroup='A',currentFilter='all',state={},storageKey='';
const $=id=>document.getElementById(id), node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
function save(){localStorage.setItem(storageKey,JSON.stringify({state,overall:$('overall').value}));}
function load(){try{const x=JSON.parse(localStorage.getItem(storageKey)||'{}');if(x&&x.state)state=x.state;if(typeof x.overall==='string')$('overall').value=x.overall;}catch(_){localStorage.removeItem(storageKey);}}
function updateProgress(){const done=Object.values(state).filter(x=>x.decision!=='pending').length;$('progress').textContent=`已完成 ${done}/${packet.case_count}`;$('export').disabled=done!==packet.case_count;}
function buttons(){const tabs=$('tabs');Object.entries(GROUPS).forEach(([k,label])=>{const b=node('button',label);b.dataset.group=k;b.setAttribute('aria-pressed',k===currentGroup);b.onclick=()=>{currentGroup=k;render();};tabs.appendChild(b)});const filters=$('filters');Object.entries(FILTERS).forEach(([k,label])=>{const b=node('button',label);b.dataset.filter=k;b.setAttribute('aria-pressed',k===currentFilter);b.onclick=()=>{currentFilter=k;render();};filters.appendChild(b)});}
function render(){document.querySelectorAll('[data-group]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.group===currentGroup));document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.filter===currentFilter));const root=$('cases');root.replaceChildren();packet.cases.filter(c=>c.group===currentGroup&&(currentFilter==='all'||state[c.case_id].decision===currentFilter)).forEach(c=>{const box=node('article');box.className='case';box.dataset.caseId=c.case_id;const img=node('img');img.src=c.asset_path;img.alt=`验收媒体 ${c.case_id}`;img.loading='lazy';img.decoding='async';box.appendChild(img);const body=node('div');body.appendChild(node('h2',`${c.case_id} · ${c.title}`));body.appendChild(node('h3','预期行为'));body.appendChild(node('p',c.expected_behavior));body.appendChild(node('h3','实际结果'));const actual=node('pre',JSON.stringify(c.actual_result,null,2));actual.className='meta';body.appendChild(actual);const details=node('details');details.appendChild(node('summary','证据来源'));const provenance=node('pre',JSON.stringify(c.provenance,null,2));provenance.className='meta';details.appendChild(provenance);body.appendChild(details);const controls=node('div');controls.className='controls';['pass','fail','pending'].forEach(v=>{const label=node('label');label.className='choice';const radio=node('input');radio.type='radio';radio.name=`d_${c.case_id}`;radio.value=v;radio.checked=state[c.case_id].decision===v;radio.onchange=()=>{state[c.case_id].decision=v;save();updateProgress();if(currentFilter!=='all')render();};label.append(radio,document.createTextNode(` ${v.toUpperCase()}`));controls.appendChild(label)});body.appendChild(controls);const comment=node('textarea');comment.className='comment';comment.placeholder='Comment';comment.value=state[c.case_id].comment;comment.oninput=()=>{state[c.case_id].comment=comment.value;save();};body.appendChild(comment);box.appendChild(body);root.appendChild(box)});updateProgress();}
async function init(){try{const started=performance.now();const response=await fetch('/review-packet.json',{cache:'no-store'});if(!response.ok)throw Error(`packet HTTP ${response.status}`);packet=await response.json();if(packet.case_count!==40||new Set(packet.case_ids).size!==40)throw Error('Case membership invalid');storageKey=`violet-sv1b-manual:${packet.binding_fingerprint}`;packet.case_ids.forEach(id=>state[id]={case_id:id,decision:'pending',comment:''});load();packet.case_ids.forEach(id=>{if(!state[id]||!['pass','fail','pending'].includes(state[id].decision))state[id]={case_id:id,decision:'pending',comment:''};});$('binding').textContent=`HEAD ${packet.git_head} · Binding ${packet.binding_fingerprint} · Manifest ${packet.case_manifest_sha256}`;buttons();render();$('overall').oninput=save;$('cases').dataset.firstRenderMs=Math.round(performance.now()-started);}catch(e){$('binding').textContent=`验收包加载失败：${e.message}`;$('binding').className='binding error';}}
$('export').onclick=async()=>{if($('export').disabled)return;$('status').textContent='正在原子写出…';try{const response=await fetch('/api/export',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({results:packet.case_ids.map(id=>state[id]),overall_comment:$('overall').value})});const result=await response.json();if(!response.ok||!result.saved)throw Error(result.error||`HTTP ${response.status}`);$('status').textContent=`已保存：${result.relative_path}`;$('status').className='status ok';$('export').disabled=true;}catch(e){$('status').textContent=`导出失败：${e.message}`;$('status').className='status error';}};
init();
</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="operation", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--asset-base-url", required=True)
    build.add_argument("--git-head")
    validate = sub.add_parser("validate")
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--git-head")
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--output", type=Path, required=True)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8024)
    args = parser.parse_args()
    if args.operation == "build":
        head = args.git_head or git_head(Path.cwd())
        result = build_static_packet(
            args.source_root,
            args.output,
            exact_git_head=head,
            asset_loader=localhost_asset_loader(args.asset_base_url),
        )
    elif args.operation == "validate":
        result = validate_static_packet(args.output, expected_git_head=args.git_head)
    else:
        serve(args.output, args.host, args.port)
        return 0
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
