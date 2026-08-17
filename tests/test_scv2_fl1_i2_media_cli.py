from __future__ import annotations

import binascii
import struct
import time

import pytest

from scripts.fl1_i2_cli import public_error_envelope, public_success_envelope, render_public_json
from scripts.fl1_i2_media_validation import MediaValidationError, validate_media_stream


def _deadline() -> float:
    return time.monotonic() + 5


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")


@pytest.mark.parametrize(
    ("payload", "format_name"),
    [
        (b"\xff\xd8\xff\xe0\x00\x02\xff\xd9", "jpeg"),
        (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", b"\x00" * 13) + _png_chunk(b"IEND", b""), "png"),
        (b"GIF89a" + b"\x01\x00\x01\x00\x00\x00\x00" + b"\x3b", "gif"),
        (b"RIFF" + (14).to_bytes(4, "little") + b"WEBP" + b"VP8L" + (2).to_bytes(4, "little") + b"xx", "webp"),
        ((20).to_bytes(4, "big") + b"ftyp" + b"avif" + b"\x00\x00\x00\x00" + b"avif" + (8).to_bytes(4, "big") + b"mdat", "avif"),
    ],
)
def test_supported_media_structures_validate_without_decode(payload: bytes, format_name: str) -> None:
    chunks = [payload[:3], payload[3:]]
    result = validate_media_stream(chunks, max_bytes=1024, max_depth=20, deadline_monotonic=_deadline())
    assert result.valid
    assert result.format == format_name
    assert result.disposition == "structure_valid"


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
