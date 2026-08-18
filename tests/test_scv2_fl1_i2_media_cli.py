from __future__ import annotations

import binascii
import json
import subprocess
import sys
import time
import zlib
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
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + _png_chunk(b"IEND", b"")


def _gif() -> bytes:
    return b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"


def _webp_vp8() -> bytes:
    frame = b"\x30\x00\x00\x9d\x01\x2a\x01\x00\x01\x00\x00"
    chunk = b"VP8 " + len(frame).to_bytes(4, "little") + frame + b"\x00"
    return b"RIFF" + (4 + len(chunk)).to_bytes(4, "little") + b"WEBP" + chunk


def _avif() -> bytes:
    ftyp = (20).to_bytes(4, "big") + b"ftypavif\x00\x00\x00\x00avif"
    pitm_payload = b"\x00\x00\x00\x00\x00\x01"
    pitm = (8 + len(pitm_payload)).to_bytes(4, "big") + b"pitm" + pitm_payload
    infe_payload = b"\x02\x00\x00\x00\x00\x01\x00\x00av01\x00"
    infe = (8 + len(infe_payload)).to_bytes(4, "big") + b"infe" + infe_payload
    iinf_payload = b"\x00\x00\x00\x00\x00\x01" + infe
    iinf = (8 + len(iinf_payload)).to_bytes(4, "big") + b"iinf" + iinf_payload
    placeholder_iloc = b"\x00" * 22
    meta_size = 12 + len(pitm) + (8 + len(placeholder_iloc)) + len(iinf)
    mdat_payload_offset = len(ftyp) + meta_size + 8
    iloc_payload = (
        b"\x00\x00\x00\x00\x44\x00\x00\x01\x00\x01\x00\x00\x00\x01"
        + mdat_payload_offset.to_bytes(4, "big")
        + (1).to_bytes(4, "big")
    )
    iloc = (8 + len(iloc_payload)).to_bytes(4, "big") + b"iloc" + iloc_payload
    children = pitm + iloc + iinf
    meta = (12 + len(children)).to_bytes(4, "big") + b"meta\x00\x00\x00\x00" + children
    mdat = (9).to_bytes(4, "big") + b"mdatx"
    return ftyp + meta + mdat


@pytest.mark.parametrize(
    ("payload", "format_name"),
    [
        (_jpeg(), "jpeg"),
        (_png(), "png"),
        (_gif(), "gif"),
        (_webp_vp8(), "webp"),
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


def _webp_chunk(kind: bytes, payload: bytes) -> bytes:
    chunk = kind + len(payload).to_bytes(4, "little") + payload + (b"\x00" if len(payload) & 1 else b"")
    return b"RIFF" + (4 + len(chunk)).to_bytes(4, "little") + b"WEBP" + chunk


@pytest.mark.parametrize(
    "payload",
    [
        _webp_chunk(b"VP8 ", b"random-random"),
        _webp_chunk(b"VP8L", b"xx"),
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00")
        + _png_chunk(b"IDAT", b"random")
        + _png_chunk(b"IEND", b""),
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x00\x00\x00\x3b",
        _jpeg().replace(b"\xff\xda\x00\x08\x01\x01", b"\xff\xda\x00\x08\x01\x02"),
    ],
)
def test_random_or_mutated_codec_payloads_never_validate(payload: bytes) -> None:
    result = validate_media_stream([payload], max_bytes=4096, max_depth=50, deadline_monotonic=_deadline())
    assert not result.valid
    assert result.disposition in {"corrupt_media", "unsupported"}


def test_well_formed_vp8l_header_is_explicitly_unsupported_without_full_bitstream_validation() -> None:
    packed = (0).to_bytes(4, "little")
    result = validate_media_stream(
        [_webp_chunk(b"VP8L", b"\x2f" + packed + b"\x01")],
        max_bytes=4096,
        max_depth=20,
        deadline_monotonic=_deadline(),
    )
    assert not result.valid
    assert result.disposition == "unsupported"
    assert result.safe_code == "webp_vp8l_bitstream_unsupported"


def test_avif_item_extent_must_match_the_mdat_payload() -> None:
    payload = bytearray(_avif())
    iloc_kind = payload.index(b"iloc")
    extent_offset = iloc_kind + 4 + 14
    payload[extent_offset : extent_offset + 4] = (1).to_bytes(4, "big")
    result = validate_media_stream([bytes(payload)], max_bytes=4096, max_depth=50, deadline_monotonic=_deadline())
    assert not result.valid
    assert result.safe_code == "avif_item_mapping_inconsistent"


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
