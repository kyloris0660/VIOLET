from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts import run_phase45_scv2_sv1b_static_manual_acceptance as static


HEAD = "a" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"violet-static-fixture"


def _case(case_id: str) -> dict[str, object]:
    label = static.payload_fingerprint({"case_id": case_id})
    return {
        "case_id": case_id,
        "category": "fixture",
        "title": f"Case {case_id}",
        "expected_behavior": "Review the immutable fixture.",
        "actual_result": {"passed": True},
        "provenance": {"fixture": True},
        "safe_media_label": label,
    }


def _make_source(root: Path) -> tuple[Path, list[dict[str, object]]]:
    root.mkdir()
    cases = [_case(case_id) for case_id in static.EXPECTED_CASE_IDS]
    manifest = root / static.SOURCE_MANIFEST
    static.write_json(manifest, cases)
    protected = root / "protected-evidence.json"
    static.write_json(protected, {"immutable": True})
    audit = {
        "proof_version": "fixture-audit",
        "git_head": HEAD,
        "passed": True,
    }
    audit["proof_fingerprint"] = static.payload_fingerprint(audit)
    audit_path = root / static.SOURCE_AUDIT_NAME
    static.write_json(audit_path, audit)
    bindings = {
        "git_head": HEAD,
        "acceptance_case_manifest_file_sha256": static.file_sha256(manifest),
        "acceptance_case_manifest_fingerprint": static.payload_fingerprint(cases),
        "audit_validation": {
            "git_head": HEAD,
            "proof_file_sha256": static.file_sha256(audit_path),
            "proof_fingerprint": audit["proof_fingerprint"],
        },
        "protected_source_file_sha256": {
            "protected-evidence.json": static.file_sha256(protected)
        },
    }
    bindings["binding_fingerprint"] = static.payload_fingerprint(bindings)
    static.write_json(
        root / static.SOURCE_BINDING_NAME,
        {"passed": True, "bindings": bindings},
    )
    return root, cases


def _build(source: Path, output: Path) -> dict[str, object]:
    return static.build_static_packet(
        source,
        output,
        exact_git_head=HEAD,
        asset_loader=lambda _label: (PNG, "image/png"),
    )


def test_builds_non_overwriting_static_packet_and_preserves_source_bytes(
    tmp_path: Path,
) -> None:
    source, _cases = _make_source(tmp_path / "source")
    before = {
        path.relative_to(source).as_posix(): static.file_sha256(path)
        for path in source.rglob("*")
        if path.is_file()
    }
    output = tmp_path / "v5-r3"
    result = _build(source, output)
    after = {
        path.relative_to(source).as_posix(): static.file_sha256(path)
        for path in source.rglob("*")
        if path.is_file()
    }

    assert result["passed"] is True
    assert result["case_count"] == 40
    assert result["image_count"] == 40
    assert before == after
    assert (output / static.SOURCE_MANIFEST).read_bytes() == (
        source / static.SOURCE_MANIFEST
    ).read_bytes()
    assert (output / static.STATIC_DIR / "index.html").is_file()
    assert (output / static.STATIC_DIR / "review-packet.json").is_file()
    assert (output / static.STATIC_DIR / "checksums.json").is_file()
    assert len(list((output / static.STATIC_DIR / "assets").iterdir())) == 40
    with pytest.raises(static.StaticAcceptanceError, match="output_already_exists"):
        _build(source, output)


def test_source_manifest_duplicate_or_drift_fails_closed(tmp_path: Path) -> None:
    source, cases = _make_source(tmp_path / "source")
    cases[-1]["case_id"] = "A01"
    static.write_json(source / static.SOURCE_MANIFEST, cases)
    with pytest.raises(static.StaticAcceptanceError, match="case_membership_invalid"):
        static.validate_v5_r2_source(source)


def test_source_binding_and_protected_inventory_tamper_fail_closed(
    tmp_path: Path,
) -> None:
    source, _cases = _make_source(tmp_path / "source")
    (source / "protected-evidence.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(static.StaticAcceptanceError, match="source_inventory_drift"):
        static.validate_v5_r2_source(source)


def test_static_packet_tamper_fails_closed(tmp_path: Path) -> None:
    source, _cases = _make_source(tmp_path / "source")
    output = tmp_path / "v5-r3"
    _build(source, output)
    asset = next((output / static.STATIC_DIR / "assets").iterdir())
    asset.write_bytes(asset.read_bytes() + b"tamper")
    with pytest.raises(static.StaticAcceptanceError, match="static_packet_file_drift"):
        static.validate_static_packet(output, expected_git_head=HEAD)


def test_static_html_has_fast_packet_render_controls_and_no_about_blank_injection() -> None:
    html = static.STATIC_HTML
    assert "fetch('/review-packet.json'" in html
    assert "loading='lazy'" in html
    assert "localStorage" in html
    assert "已完成 ${done}/${packet.case_count}" in html
    assert "done!==packet.case_count" in html
    assert "document.write" not in html
    assert "window.open" not in html
    assert "about:blank" not in html
    assert all(f"{group} ·" in html for group in "ABCDE")


def _request(url: str, *, payload: dict[str, object] | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_server_is_static_and_export_is_complete_exclusive_and_atomic(
    tmp_path: Path,
) -> None:
    source, _cases = _make_source(tmp_path / "source")
    output = tmp_path / "v5-r3"
    _build(source, output)
    server = ThreadingHTTPServer(("127.0.0.1", 0), static.StaticAcceptanceHandler)
    server.packet_root = output / static.STATIC_DIR  # type: ignore[attr-defined]
    server.output_root = output  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert "40-case 静态人工验收" in html
        pending = {
            "results": [
                {"case_id": case_id, "decision": "pending", "comment": ""}
                for case_id in static.EXPECTED_CASE_IDS
            ],
            "overall_comment": "fixture",
        }
        status, body = _request(f"{base}/api/export", payload=pending)
        assert status == 400
        assert body["error"] == "static_export_pending_cases"
        assert not (output / static.RESULT_RELATIVE).exists()

        complete = dict(pending)
        complete["results"] = [
            {"case_id": case_id, "decision": "pass", "comment": case_id}
            for case_id in static.EXPECTED_CASE_IDS
        ]
        status, body = _request(f"{base}/api/export", payload=complete)
        assert status == 200
        assert body["saved"] is True
        result_path = output / static.RESULT_RELATIVE
        result = static.read_json(result_path)
        assert result["git_head"] == HEAD
        assert result["case_ids"] == list(static.EXPECTED_CASE_IDS)
        assert len(result["per_case_result"]) == 40
        first_sha = static.file_sha256(result_path)
        status, body = _request(f"{base}/api/export", payload=complete)
        assert status == 409
        assert body["error"] == "static_export_result_already_exists"
        assert static.file_sha256(result_path) == first_sha
        assert not list(result_path.parent.glob("*.tmp-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_asset_loader_rejects_external_host() -> None:
    with pytest.raises(static.StaticAcceptanceError, match="not_localhost"):
        static.localhost_asset_loader("https://example.com")


def test_static_server_module_has_no_database_or_provider_imports() -> None:
    source = Path(static.__file__).read_text(encoding="utf-8")
    forbidden = (
        "sqlalchemy",
        "psycopg",
        "gallery_dl",
        "openai",
        "backend.app",
    )
    assert all(f"import {name}" not in source for name in forbidden)
