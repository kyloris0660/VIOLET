"""Bounded incremental media-structure validation without pixel decoding."""

from __future__ import annotations

import binascii
import time
from dataclasses import dataclass
from typing import Iterable, Iterator


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


@dataclass(frozen=True)
class _Outcome:
    format: str
    valid: bool
    disposition: str
    safe_code: str
    depth: int


class MediaValidationError(RuntimeError):
    pass


class _Truncated(RuntimeError):
    pass


class _BoundedStream:
    """Small-buffer reader over chunks; it never accumulates the full object."""

    MATERIALIZE_LIMIT = 64 * 1024

    def __init__(self, chunks: Iterable[bytes], *, max_bytes: int, deadline: float) -> None:
        self._chunks = iter(chunks)
        self._buffer = bytearray()
        self._eof = False
        self.max_bytes = max_bytes
        self.deadline = deadline
        self.bytes_received = 0
        self.bytes_consumed = 0

    def _check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise MediaValidationError("media_validation_deadline_exceeded")

    def _fill(self, minimum: int) -> None:
        self._check_deadline()
        while len(self._buffer) < minimum and not self._eof:
            try:
                chunk = next(self._chunks)
            except StopIteration:
                self._eof = True
                break
            if not isinstance(chunk, bytes) or not chunk:
                if chunk == b"":
                    continue
                raise MediaValidationError("media_stream_chunk_invalid")
            self.bytes_received += len(chunk)
            if self.bytes_received > self.max_bytes:
                raise MediaValidationError("media_validation_byte_budget_exceeded")
            self._buffer.extend(chunk)
            self._check_deadline()

    def peek_up_to(self, count: int) -> bytes:
        if count < 0 or count > self.MATERIALIZE_LIMIT:
            raise MediaValidationError("media_validation_materialize_budget_exceeded")
        self._fill(count)
        return bytes(self._buffer[:count])

    def read_exact(self, count: int) -> bytes:
        if count < 0 or count > self.MATERIALIZE_LIMIT:
            raise MediaValidationError("media_validation_materialize_budget_exceeded")
        self._fill(count)
        if len(self._buffer) < count:
            raise _Truncated
        value = bytes(self._buffer[:count])
        del self._buffer[:count]
        self.bytes_consumed += count
        return value

    def read_byte(self) -> int:
        return self.read_exact(1)[0]

    def iter_exact(self, count: int) -> Iterator[bytes]:
        if count < 0:
            raise MediaValidationError("media_structure_length_invalid")
        remaining = count
        while remaining:
            block = self.read_exact(min(remaining, self.MATERIALIZE_LIMIT))
            remaining -= len(block)
            yield block

    def skip(self, count: int) -> None:
        for _block in self.iter_exact(count):
            pass

    def at_eof(self) -> bool:
        self._fill(1)
        return not self._buffer and self._eof

    def drain(self) -> None:
        while not self.at_eof():
            self.skip(min(len(self._buffer), self.MATERIALIZE_LIMIT))


def _invalid(format_name: str, code: str, depth: int = 0) -> _Outcome:
    return _Outcome(format_name, False, "corrupt_media", code, depth)


def _unsupported(format_name: str, code: str, depth: int = 0) -> _Outcome:
    return _Outcome(format_name, False, "unsupported", code, depth)


def _valid(format_name: str, depth: int) -> _Outcome:
    return _Outcome(format_name, True, "structure_valid", "media_structure_valid", depth)


def _depth(depth: int, maximum: int, format_name: str) -> _Outcome | None:
    if depth > maximum:
        return _invalid(format_name, "media_structure_depth_exceeded", depth)
    return None


def _jpeg(stream: _BoundedStream, max_depth: int) -> _Outcome:
    try:
        if stream.read_exact(2) != b"\xff\xd8":
            return _invalid("jpeg", "jpeg_soi_missing")
        depth = 0
        saw_sof = False
        while True:
            if stream.read_byte() != 0xFF:
                return _invalid("jpeg", "jpeg_marker_invalid", depth)
            marker = stream.read_byte()
            while marker == 0xFF:
                marker = stream.read_byte()
            if marker == 0xD9:
                return _invalid("jpeg", "jpeg_image_data_missing", depth)
            if marker in {0xD8, 0x00}:
                return _invalid("jpeg", "jpeg_marker_invalid", depth)
            if marker in {*range(0xD0, 0xD8), 0x01}:
                continue
            length = int.from_bytes(stream.read_exact(2), "big")
            if length < 2:
                return _invalid("jpeg", "jpeg_segment_bounds_invalid", depth)
            depth += 1
            overflow = _depth(depth, max_depth, "jpeg")
            if overflow:
                return overflow
            payload_length = length - 2
            if marker in {0xC0, 0xC1, 0xC2}:
                if saw_sof:
                    stream.skip(payload_length)
                    return _invalid("jpeg", "jpeg_frame_header_duplicate", depth)
                header = stream.read_exact(payload_length)
                if len(header) < 6 or int.from_bytes(header[1:3], "big") == 0 or int.from_bytes(header[3:5], "big") == 0 or header[5] == 0:
                    return _invalid("jpeg", "jpeg_frame_header_invalid", depth)
                saw_sof = True
                continue
            if marker in {0xC3, *range(0xC5, 0xCC), *range(0xCD, 0xD0)}:
                stream.skip(payload_length)
                return _unsupported("jpeg", "jpeg_advanced_frame_unsupported", depth)
            if marker == 0xDA:
                stream.skip(payload_length)
                if not saw_sof:
                    return _invalid("jpeg", "jpeg_frame_header_missing", depth)
                entropy_bytes = 0
                while True:
                    value = stream.read_byte()
                    if value != 0xFF:
                        entropy_bytes += 1
                        continue
                    following = stream.read_byte()
                    while following == 0xFF:
                        following = stream.read_byte()
                    if following == 0x00:
                        entropy_bytes += 1
                        continue
                    if 0xD0 <= following <= 0xD7:
                        continue
                    if following == 0xD9:
                        if entropy_bytes == 0:
                            return _invalid("jpeg", "jpeg_image_data_missing", depth)
                        if not stream.at_eof():
                            return _invalid("jpeg", "jpeg_trailing_bytes", depth)
                        return _valid("jpeg", depth)
                    return _unsupported("jpeg", "jpeg_multiscan_unsupported", depth)
            stream.skip(payload_length)
    except _Truncated:
        return _invalid("jpeg", "jpeg_truncated", locals().get("depth", 0))


def _png(stream: _BoundedStream, max_depth: int) -> _Outcome:
    try:
        if stream.read_exact(8) != b"\x89PNG\r\n\x1a\n":
            return _invalid("png", "png_signature_missing")
        depth = 0
        saw_ihdr = False
        saw_idat = False
        idat_closed = False
        saw_plte = False
        while True:
            length = int.from_bytes(stream.read_exact(4), "big")
            kind = stream.read_exact(4)
            if length > stream.max_bytes:
                return _invalid("png", "png_chunk_bounds_invalid", depth)
            depth += 1
            overflow = _depth(depth, max_depth, "png")
            if overflow:
                return overflow
            crc = binascii.crc32(kind)
            first = bytearray()
            for block in stream.iter_exact(length):
                if len(first) < 32:
                    first.extend(block[: 32 - len(first)])
                crc = binascii.crc32(block, crc)
            expected_crc = int.from_bytes(stream.read_exact(4), "big")
            if crc & 0xFFFFFFFF != expected_crc:
                return _invalid("png", "png_crc_invalid", depth)
            if depth == 1:
                if kind != b"IHDR" or length != 13:
                    return _invalid("png", "png_ihdr_invalid", depth)
                if int.from_bytes(first[0:4], "big") == 0 or int.from_bytes(first[4:8], "big") == 0:
                    return _invalid("png", "png_dimensions_invalid", depth)
                saw_ihdr = True
                continue
            if kind == b"IHDR":
                return _invalid("png", "png_ihdr_duplicate", depth)
            if kind == b"PLTE":
                if saw_plte or saw_idat or length == 0 or length % 3:
                    return _invalid("png", "png_plte_invalid", depth)
                saw_plte = True
            elif kind == b"IDAT":
                if idat_closed or length == 0:
                    return _invalid("png", "png_idat_invalid", depth)
                saw_idat = True
            elif saw_idat:
                idat_closed = True
            if kind == b"IEND":
                if length != 0 or not saw_ihdr or not saw_idat:
                    return _invalid("png", "png_iend_invalid", depth)
                if not stream.at_eof():
                    return _invalid("png", "png_trailing_bytes", depth)
                return _valid("png", depth)
            if kind[:1].isalpha() and kind[:1].isupper() and kind not in {b"PLTE", b"IDAT"}:
                return _unsupported("png", "png_critical_chunk_unsupported", depth)
    except _Truncated:
        return _invalid("png", "png_truncated", locals().get("depth", 0))


def _gif_subblocks(stream: _BoundedStream) -> tuple[bool, int]:
    payload_bytes = 0
    while True:
        size = stream.read_byte()
        if size == 0:
            return payload_bytes > 0, payload_bytes
        stream.skip(size)
        payload_bytes += size


def _gif(stream: _BoundedStream, max_depth: int) -> _Outcome:
    try:
        header = stream.read_exact(13)
        if header[:6] not in {b"GIF87a", b"GIF89a"}:
            return _invalid("gif", "gif_header_invalid")
        if int.from_bytes(header[6:8], "little") == 0 or int.from_bytes(header[8:10], "little") == 0:
            return _invalid("gif", "gif_dimensions_invalid")
        packed = header[10]
        if packed & 0x80:
            stream.skip(3 * (2 ** ((packed & 0x07) + 1)))
        depth = 0
        saw_image = False
        while True:
            marker = stream.read_byte()
            if marker == 0x3B:
                if not saw_image:
                    return _invalid("gif", "gif_image_data_missing", depth)
                if not stream.at_eof():
                    return _invalid("gif", "gif_trailing_bytes", depth)
                return _valid("gif", depth)
            depth += 1
            overflow = _depth(depth, max_depth, "gif")
            if overflow:
                return overflow
            if marker == 0x21:
                stream.read_byte()
                _gif_subblocks(stream)
                continue
            if marker != 0x2C:
                return _invalid("gif", "gif_block_marker_invalid", depth)
            if saw_image:
                return _unsupported("gif", "gif_animation_unsupported", depth)
            descriptor = stream.read_exact(9)
            if int.from_bytes(descriptor[4:6], "little") == 0 or int.from_bytes(descriptor[6:8], "little") == 0:
                return _invalid("gif", "gif_image_descriptor_invalid", depth)
            if descriptor[8] & 0x80:
                stream.skip(3 * (2 ** ((descriptor[8] & 0x07) + 1)))
            lzw_minimum = stream.read_byte()
            if lzw_minimum < 2 or lzw_minimum > 12:
                return _invalid("gif", "gif_lzw_code_size_invalid", depth)
            has_data, _length = _gif_subblocks(stream)
            if not has_data:
                return _invalid("gif", "gif_image_data_missing", depth)
            saw_image = True
    except _Truncated:
        return _invalid("gif", "gif_truncated", locals().get("depth", 0))


def _webp(stream: _BoundedStream, max_depth: int) -> _Outcome:
    try:
        header = stream.read_exact(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            return _invalid("webp", "webp_header_invalid")
        declared_total = int.from_bytes(header[4:8], "little") + 8
        if declared_total < 20 or declared_total > stream.max_bytes:
            return _invalid("webp", "webp_riff_size_invalid")
        depth = 0
        image_chunks = 0
        advanced = False
        while stream.bytes_consumed < declared_total:
            if declared_total - stream.bytes_consumed < 8:
                return _invalid("webp", "webp_chunk_truncated", depth)
            kind = stream.read_exact(4)
            length = int.from_bytes(stream.read_exact(4), "little")
            padded = length + (length & 1)
            if padded > declared_total - stream.bytes_consumed:
                return _invalid("webp", "webp_chunk_bounds_invalid", depth)
            depth += 1
            overflow = _depth(depth, max_depth, "webp")
            if overflow:
                return overflow
            if kind in {b"VP8 ", b"VP8L"}:
                if length == 0:
                    return _invalid("webp", "webp_image_chunk_empty", depth)
                image_chunks += 1
            elif kind == b"VP8X":
                if length != 10:
                    return _invalid("webp", "webp_vp8x_invalid", depth)
            elif kind in {b"ANIM", b"ANMF"}:
                advanced = True
            stream.skip(padded)
        if stream.bytes_consumed != declared_total or not stream.at_eof():
            return _invalid("webp", "webp_riff_size_invalid", depth)
        if advanced:
            return _unsupported("webp", "webp_animation_unsupported", depth)
        if image_chunks != 1:
            return _invalid("webp", "webp_image_chunk_missing", depth)
        return _valid("webp", depth)
    except _Truncated:
        return _invalid("webp", "webp_truncated", locals().get("depth", 0))


def _box_header(stream: _BoundedStream) -> tuple[int, bytes, int]:
    size = int.from_bytes(stream.read_exact(4), "big")
    kind = stream.read_exact(4)
    header = 8
    if size == 1:
        size = int.from_bytes(stream.read_exact(8), "big")
        header = 16
    if size == 0:
        raise MediaValidationError("avif_open_ended_box_unsupported")
    if size < header:
        raise MediaValidationError("avif_box_bounds_invalid")
    return size, kind, header


def _avif_meta(stream: _BoundedStream, payload_length: int, max_depth: int, depth: int) -> tuple[set[bytes], int, _Outcome | None]:
    if payload_length < 4:
        return set(), depth, _invalid("avif", "avif_meta_truncated", depth)
    stream.skip(4)
    remaining = payload_length - 4
    kinds: set[bytes] = set()
    while remaining:
        if remaining < 8:
            return kinds, depth, _invalid("avif", "avif_meta_child_truncated", depth)
        size, kind, header = _box_header(stream)
        if size > remaining:
            return kinds, depth, _invalid("avif", "avif_meta_child_bounds_invalid", depth)
        depth += 1
        overflow = _depth(depth, max_depth, "avif")
        if overflow:
            return kinds, depth, overflow
        if kind in kinds and kind in {b"pitm", b"iloc", b"iinf"}:
            return kinds, depth, _invalid("avif", "avif_mandatory_box_duplicate", depth)
        kinds.add(kind)
        stream.skip(size - header)
        remaining -= size
    return kinds, depth, None


def _avif(stream: _BoundedStream, max_depth: int) -> _Outcome:
    try:
        depth = 0
        saw_ftyp = False
        saw_meta = False
        saw_mdat = False
        meta_kinds: set[bytes] = set()
        while not stream.at_eof():
            size, kind, header = _box_header(stream)
            payload_length = size - header
            if payload_length > stream.max_bytes - stream.bytes_consumed:
                return _invalid("avif", "avif_box_bounds_invalid", depth)
            depth += 1
            overflow = _depth(depth, max_depth, "avif")
            if overflow:
                return overflow
            if kind == b"ftyp":
                if saw_ftyp or depth != 1 or payload_length < 8:
                    return _invalid("avif", "avif_ftyp_invalid", depth)
                brands = stream.read_exact(payload_length)
                compatible = [brands[index : index + 4] for index in range(8, len(brands), 4)]
                if brands[:4] not in {b"avif", b"avis"} and not {b"avif", b"avis"}.intersection(compatible):
                    return _invalid("avif", "avif_brand_missing", depth)
                saw_ftyp = True
            elif kind == b"meta":
                if saw_meta:
                    return _invalid("avif", "avif_meta_duplicate", depth)
                meta_kinds, depth, error = _avif_meta(stream, payload_length, max_depth, depth)
                if error:
                    return error
                saw_meta = True
            elif kind == b"mdat":
                if saw_mdat or payload_length == 0:
                    return _invalid("avif", "avif_mdat_invalid", depth)
                stream.skip(payload_length)
                saw_mdat = True
            elif kind in {b"moov", b"trak", b"iref"}:
                stream.skip(payload_length)
                return _unsupported("avif", "avif_advanced_structure_unsupported", depth)
            else:
                stream.skip(payload_length)
        if not saw_ftyp or not saw_meta or not saw_mdat:
            return _invalid("avif", "avif_required_box_missing", depth)
        if not {b"pitm", b"iloc", b"iinf"}.issubset(meta_kinds):
            return _invalid("avif", "avif_item_mapping_missing", depth)
        return _valid("avif", depth)
    except _Truncated:
        return _invalid("avif", "avif_truncated", locals().get("depth", 0))
    except MediaValidationError as exc:
        code = str(exc)
        if code.endswith("unsupported"):
            return _unsupported("avif", code, locals().get("depth", 0))
        return _invalid("avif", code, locals().get("depth", 0))


def validate_media_stream(chunks: Iterable[bytes], *, max_bytes: int, max_depth: int, deadline_monotonic: float) -> MediaValidationResult:
    if max_bytes <= 0 or max_depth <= 0:
        raise MediaValidationError("media_validation_budget_invalid")
    stream = _BoundedStream(chunks, max_bytes=max_bytes, deadline=deadline_monotonic)
    prefix = stream.peek_up_to(12)
    if prefix.startswith(b"\xff\xd8"):
        outcome = _jpeg(stream, max_depth)
    elif prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        outcome = _png(stream, max_depth)
    elif prefix.startswith((b"GIF87a", b"GIF89a")):
        outcome = _gif(stream, max_depth)
    elif prefix.startswith(b"RIFF"):
        outcome = _webp(stream, max_depth)
    elif len(prefix) >= 8 and prefix[4:8] == b"ftyp":
        outcome = _avif(stream, max_depth)
    else:
        outcome = _unsupported("unknown", "media_format_unsupported")
    stream.drain()
    return MediaValidationResult(outcome.format, outcome.valid, outcome.disposition, outcome.safe_code, stream.bytes_received, outcome.depth)
