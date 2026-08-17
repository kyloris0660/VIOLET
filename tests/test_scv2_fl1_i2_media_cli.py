from __future__ import annotations

import binascii
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.fl1_i2_cli import public_error_envelope, public_success_envelope, render_public_json
from scripts.fl1_i2_media_validation import MediaValidationError, validate_media_stream


def _deadline() -> float:
    return time.monotonic() + 5


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")


def _jpeg() -> bytes:
    return (
        b"\xff\xd8"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
        b"\x01\xff\xd9"
    )


def _png() -> bytes:
    ihdr = b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", b"x") + _png_chunk(b"IEND", b"")


def _gif() -> bytes:
    return b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01\x00\x00\x3b"


def _avif() -> bytes:
    ftyp = (20).to_bytes(4, "big") + b"ftypavif\x00\x00\x00\x00avif"
    children = b"".join((8).to_bytes(4, "big") + kind for kind in (b"pitm", b"iloc", b"iinf"))
    meta = (12 + len(children)).to_bytes(4, "big") + b"meta\x00\x00\x00\x00" + children
    mdat = (9).to_bytes(4, "big") + b"mdatx"
    return ftyp + meta + mdat


@pytest.mark.parametrize(
    ("payload", "format_name"),
    [
        (_jpeg(), "jpeg"),
        (_png(), "png"),
        (_gif(), "gif"),
        (b"RIFF" + (14).to_bytes(4, "little") + b"WEBP" + b"VP8L" + (2).to_bytes(4, "little") + b"xx", "webp"),
        (_avif(), "avif"),
    ],
)
def test_supported_media_structures_validate_without_decode(payload: bytes, format_name: str) -> None:
    chunks = [payload[:3], payload[3:]]
    result = validate_media_stream(chunks, max_bytes=1024, max_depth=20, deadline_monotonic=_deadline())
    assert result.valid
    assert result.format == format_name
    assert result.disposition == "structure_valid"
    assert result.bytes_examined == len(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xd8\xff\xe0\x00\x02\xff\xd9",
        b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00") + _png_chunk(b"IEND", b""),
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x3b",
        b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + b"\x00" * 10,
        (20).to_bytes(4, "big") + b"ftypavif\x00\x00\x00\x00avif",
    ],
)
def test_header_only_shells_are_not_structure_valid(payload: bytes) -> None:
    result = validate_media_stream([payload], max_bytes=4096, max_depth=20, deadline_monotonic=_deadline())
    assert not result.valid
    assert result.disposition in {"corrupt_media", "unsupported"}


def test_advanced_legal_shape_is_explicitly_unsupported() -> None:
    animation = b"RIFF" + (22).to_bytes(4, "little") + b"WEBP" + b"ANIM" + (10).to_bytes(4, "little") + b"\x00" * 10
    result = validate_media_stream([animation], max_bytes=4096, max_depth=20, deadline_monotonic=_deadline())
    assert result.disposition == "unsupported"
    assert not result.valid


@pytest.mark.parametrize(
    "payload",
    [
        _png() + b"trailing",
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00")
        + _png_chunk(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00")
        + _png_chunk(b"IDAT", b"x")
        + _png_chunk(b"IEND", b""),
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00")
        + _png_chunk(b"IDAT", b"x")
        + _png_chunk(b"tEXt", b"x")
        + _png_chunk(b"IDAT", b"y")
        + _png_chunk(b"IEND", b""),
    ],
)
def test_trailing_duplicate_and_order_violations_fail_closed(payload: bytes) -> None:
    result = validate_media_stream([payload], max_bytes=4096, max_depth=20, deadline_monotonic=_deadline())
    assert not result.valid
    assert result.disposition == "corrupt_media"


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xd8\xff\xe0\xff\xff",
        b"\x89PNG\r\n\x1a\n" + b"\x7f\xff\xff\xffIHDR",
        b"GIF89a" + b"\x00" * 7 + b"\x2c",
        b"RIFF\xff\xff\xff\xffWEBP",
        (1).to_bytes(4, "big") + b"ftyp",
    ],
)
def test_malformed_truncated_or_size_bomb_fails_closed(payload: bytes) -> None:
    result = validate_media_stream([payload], max_bytes=1024, max_depth=5, deadline_monotonic=_deadline())
    assert not result.valid
    assert result.disposition == "corrupt_media"


def test_byte_depth_and_time_budgets_fail_closed() -> None:
    with pytest.raises(MediaValidationError, match="byte_budget"):
        validate_media_stream([b"x" * 11], max_bytes=10, max_depth=5, deadline_monotonic=_deadline())
    with pytest.raises(MediaValidationError, match="deadline"):
        validate_media_stream([b"x"], max_bytes=10, max_depth=5, deadline_monotonic=time.monotonic() - 1)


def test_cli_unknown_error_exposes_only_correlation_token() -> None:
    private = RuntimeError(r"C:\private\real-name.jpg secret-object-id content-hash")
    rendered = render_public_json(public_error_envelope(private))
    assert "internal_error" in rendered and "correlation_token" in rendered
    assert "private" not in rendered and "real-name" not in rendered and "content-hash" not in rendered
    assert "Traceback" not in rendered


def test_cli_success_projection_rejects_private_fields() -> None:
    with pytest.raises(ValueError, match="field_forbidden"):
        public_success_envelope({"status": "ok", "source_path": r"C:\private"})


@pytest.mark.parametrize(
    "arguments",
    [
        ("--unknown", r"C:\private\real-name.jpg"),
        ("--config", r"C:\private\missing-config.json"),
    ],
)
def test_direct_script_invocation_bootstraps_before_import_and_redacts_errors(arguments: tuple[str, ...]) -> None:
    runner = Path(__file__).resolve().parents[1] / "scripts" / "fl1_i2_runner.py"
    completed = subprocess.run(
        [sys.executable, "-B", "-I", "-s", str(runner), *arguments],
        cwd=runner.parent.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert payload["paths_redacted"] is True
    rendered = completed.stdout + completed.stderr
    assert "Traceback" not in rendered
    assert "real-name" not in rendered and "missing-config" not in rendered and "C:\\private" not in rendered
