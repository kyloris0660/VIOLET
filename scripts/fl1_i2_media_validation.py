"""Bounded, streaming media-structure validation without pixel decoding."""

from __future__ import annotations

import binascii
import struct
import time
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class MediaValidationResult:
    format: str
    valid: bool
    disposition: str
    safe_code: str
    bytes_examined: int
    structure_depth: int

    def to_private_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "valid": self.valid,
            "disposition": self.disposition,
            "safe_code": self.safe_code,
            "bytes_examined": self.bytes_examined,
            "structure_depth": self.structure_depth,
        }


class MediaValidationError(RuntimeError):
    pass


def _invalid(format_name: str, code: str, length: int, depth: int = 0) -> MediaValidationResult:
    return MediaValidationResult(format_name, False, "corrupt_media", code, length, depth)


def _valid(format_name: str, length: int, depth: int) -> MediaValidationResult:
    return MediaValidationResult(format_name, True, "structure_valid", "media_structure_valid", length, depth)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise MediaValidationError("media_validation_deadline_exceeded")


def _jpeg(data: bytes, deadline: float, max_depth: int) -> MediaValidationResult:
    if not data.startswith(b"\xff\xd8"):
        return _invalid("jpeg", "jpeg_soi_missing", len(data))
    offset = 2
    segments = 0
    in_scan = False
    while offset < len(data):
        _check_deadline(deadline)
        if data[offset] != 0xFF:
            if in_scan:
                offset += 1
                continue
            return _invalid("jpeg", "jpeg_marker_invalid", len(data), segments)
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return _invalid("jpeg", "jpeg_truncated", len(data), segments)
        marker = data[offset]
        offset += 1
        if in_scan and marker == 0x00:
            continue
        if marker == 0xD9:
            return _valid("jpeg", len(data), segments)
        if marker in {0xD8, *range(0xD0, 0xD8), 0x01}:
            continue
        if offset + 2 > len(data):
            return _invalid("jpeg", "jpeg_segment_truncated", len(data), segments)
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            return _invalid("jpeg", "jpeg_segment_bounds_invalid", len(data), segments)
        segments += 1
        if segments > max_depth:
            return _invalid("jpeg", "media_structure_depth_exceeded", len(data), segments)
        in_scan = marker == 0xDA
        offset += length
    return _invalid("jpeg", "jpeg_eoi_missing", len(data), segments)


def _png(data: bytes, deadline: float, max_depth: int) -> MediaValidationResult:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _invalid("png", "png_signature_missing", len(data))
    offset = 8
    chunks = 0
    saw_header = False
    while offset < len(data):
        _check_deadline(deadline)
        if offset + 12 > len(data):
            return _invalid("png", "png_chunk_truncated", len(data), chunks)
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            return _invalid("png", "png_chunk_bounds_invalid", len(data), chunks)
        chunks += 1
        if chunks > max_depth:
            return _invalid("png", "media_structure_depth_exceeded", len(data), chunks)
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length : end], "big")
        if binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return _invalid("png", "png_crc_invalid", len(data), chunks)
        if chunks == 1:
            if chunk_type != b"IHDR" or length != 13:
                return _invalid("png", "png_ihdr_invalid", len(data), chunks)
            saw_header = True
        if chunk_type == b"IEND":
            if length != 0 or not saw_header:
                return _invalid("png", "png_iend_invalid", len(data), chunks)
            return _valid("png", len(data), chunks)
        offset = end
    return _invalid("png", "png_iend_missing", len(data), chunks)


def _subblocks(data: bytes, offset: int, deadline: float) -> int | None:
    while offset < len(data):
        _check_deadline(deadline)
        size = data[offset]
        offset += 1
        if size == 0:
            return offset
        if offset + size > len(data):
            return None
        offset += size
    return None


def _gif(data: bytes, deadline: float, max_depth: int) -> MediaValidationResult:
    if data[:6] not in {b"GIF87a", b"GIF89a"} or len(data) < 13:
        return _invalid("gif", "gif_header_invalid", len(data))
    packed = data[10]
    offset = 13 + (3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0)
    blocks = 0
    while offset < len(data):
        _check_deadline(deadline)
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            return _valid("gif", len(data), blocks)
        blocks += 1
        if blocks > max_depth:
            return _invalid("gif", "media_structure_depth_exceeded", len(data), blocks)
        if marker == 0x21:
            if offset >= len(data):
                return _invalid("gif", "gif_extension_truncated", len(data), blocks)
            offset += 1
            next_offset = _subblocks(data, offset, deadline)
        elif marker == 0x2C:
            if offset + 9 > len(data):
                return _invalid("gif", "gif_image_descriptor_truncated", len(data), blocks)
            packed = data[offset + 8]
            offset += 9 + (3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0)
            if offset >= len(data):
                return _invalid("gif", "gif_image_data_truncated", len(data), blocks)
            offset += 1
            next_offset = _subblocks(data, offset, deadline)
        else:
            return _invalid("gif", "gif_block_marker_invalid", len(data), blocks)
        if next_offset is None:
            return _invalid("gif", "gif_subblock_truncated", len(data), blocks)
        offset = next_offset
    return _invalid("gif", "gif_trailer_missing", len(data), blocks)


def _webp(data: bytes, deadline: float, max_depth: int) -> MediaValidationResult:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return _invalid("webp", "webp_header_invalid", len(data))
    declared = int.from_bytes(data[4:8], "little") + 8
    if declared != len(data):
        return _invalid("webp", "webp_riff_size_invalid", len(data))
    offset = 12
    chunks = 0
    image_chunk = False
    while offset < len(data):
        _check_deadline(deadline)
        if offset + 8 > len(data):
            return _invalid("webp", "webp_chunk_truncated", len(data), chunks)
        kind = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "little")
        end = offset + 8 + length
        if end > len(data):
            return _invalid("webp", "webp_chunk_bounds_invalid", len(data), chunks)
        chunks += 1
        if chunks > max_depth:
            return _invalid("webp", "media_structure_depth_exceeded", len(data), chunks)
        image_chunk |= kind in {b"VP8 ", b"VP8L", b"VP8X"}
        offset = end + (length & 1)
    return _valid("webp", len(data), chunks) if image_chunk and offset == len(data) else _invalid("webp", "webp_image_chunk_missing", len(data), chunks)


def _avif_boxes(data: bytes, start: int, end: int, deadline: float, max_depth: int, depth: int = 1) -> tuple[bool, set[bytes], int, str]:
    if depth > max_depth:
        return False, set(), depth, "media_structure_depth_exceeded"
    offset = start
    kinds: set[bytes] = set()
    max_seen = depth
    while offset < end:
        _check_deadline(deadline)
        if offset + 8 > end:
            return False, kinds, max_seen, "avif_box_truncated"
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                return False, kinds, max_seen, "avif_extended_box_truncated"
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            return False, kinds, max_seen, "avif_box_bounds_invalid"
        kinds.add(kind)
        if kind == b"meta":
            if size < header + 4:
                return False, kinds, max_seen, "avif_meta_truncated"
            valid, nested, nested_depth, code = _avif_boxes(data, offset + header + 4, offset + size, deadline, max_depth, depth + 1)
            kinds |= nested
            max_seen = max(max_seen, nested_depth)
            if not valid:
                return False, kinds, max_seen, code
        offset += size
    return offset == end, kinds, max_seen, "media_structure_valid"


def _avif(data: bytes, deadline: float, max_depth: int) -> MediaValidationResult:
    valid, kinds, depth, code = _avif_boxes(data, 0, len(data), deadline, max_depth)
    if not valid:
        return _invalid("avif", code, len(data), depth)
    ftyp = data.find(b"ftyp")
    if ftyp < 4 or not any(brand in data[ftyp + 4 : min(len(data), ftyp + 64)] for brand in (b"avif", b"avis")):
        return _invalid("avif", "avif_brand_missing", len(data), depth)
    if b"ftyp" not in kinds or not ({b"meta", b"mdat"} & kinds):
        return _invalid("avif", "avif_required_box_missing", len(data), depth)
    return _valid("avif", len(data), depth)


def validate_media_stream(chunks: Iterable[bytes], *, max_bytes: int, max_depth: int, deadline_monotonic: float) -> MediaValidationResult:
    if max_bytes <= 0 or max_depth <= 0:
        raise MediaValidationError("media_validation_budget_invalid")
    buffer = bytearray()
    for chunk in chunks:
        _check_deadline(deadline_monotonic)
        if not isinstance(chunk, bytes):
            raise MediaValidationError("media_stream_chunk_invalid")
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise MediaValidationError("media_validation_byte_budget_exceeded")
    data = bytes(buffer)
    if data.startswith(b"\xff\xd8"):
        return _jpeg(data, deadline_monotonic, max_depth)
    if data.startswith(b"\x89PNG"):
        return _png(data, deadline_monotonic, max_depth)
    if data.startswith((b"GIF87a", b"GIF89a")):
        return _gif(data, deadline_monotonic, max_depth)
    if data.startswith(b"RIFF"):
        return _webp(data, deadline_monotonic, max_depth)
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return _avif(data, deadline_monotonic, max_depth)
    return _invalid("unknown", "media_format_unsupported", len(data))
