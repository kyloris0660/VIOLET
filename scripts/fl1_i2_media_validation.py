"""Bounded incremental media-structure validation without pixel decoding."""

from __future__ import annotations

import binascii
import time
import zlib
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
        frame_components: set[int] = set()
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
                if len(header) < 9 or int.from_bytes(header[1:3], "big") == 0 or int.from_bytes(header[3:5], "big") == 0 or header[5] == 0:
                    return _invalid("jpeg", "jpeg_frame_header_invalid", depth)
                component_count = header[5]
                if len(header) != 6 + 3 * component_count:
                    return _invalid("jpeg", "jpeg_frame_header_invalid", depth)
                frame_components = {header[6 + 3 * index] for index in range(component_count)}
                if len(frame_components) != component_count:
                    return _invalid("jpeg", "jpeg_frame_header_invalid", depth)
                saw_sof = True
                continue
            if marker in {0xC3, *range(0xC5, 0xCC), *range(0xCD, 0xD0)}:
                stream.skip(payload_length)
                return _unsupported("jpeg", "jpeg_advanced_frame_unsupported", depth)
            if marker == 0xDA:
                scan = stream.read_exact(payload_length)
                if not saw_sof:
                    return _invalid("jpeg", "jpeg_frame_header_missing", depth)
                if not scan:
                    return _invalid("jpeg", "jpeg_scan_header_invalid", depth)
                scan_components = scan[0]
                if scan_components == 0 or len(scan) != 1 + 2 * scan_components + 3:
                    return _invalid("jpeg", "jpeg_scan_header_invalid", depth)
                if {scan[1 + 2 * index] for index in range(scan_components)} - frame_components:
                    return _invalid("jpeg", "jpeg_scan_header_invalid", depth)
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
        decompressor: zlib.decompressobj | None = None
        expected_decoded = 0
        decoded_bytes = 0
        row_size = 0

        def consume_decoded(data: bytes) -> bool:
            nonlocal decoded_bytes
            for value in data:
                if row_size and decoded_bytes % row_size == 0 and value > 4:
                    return False
                decoded_bytes += 1
                if decoded_bytes > expected_decoded:
                    return False
            return True

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
                if kind == b"IDAT":
                    if decompressor is None:
                        return _invalid("png", "png_idat_before_ihdr", depth)
                    try:
                        decoded = decompressor.decompress(block, max(0, expected_decoded - decoded_bytes + 1))
                    except zlib.error:
                        return _invalid("png", "png_zlib_invalid", depth)
                    if not consume_decoded(decoded) or decompressor.unconsumed_tail:
                        return _invalid("png", "png_decoded_size_invalid", depth)
            expected_crc = int.from_bytes(stream.read_exact(4), "big")
            if crc & 0xFFFFFFFF != expected_crc:
                return _invalid("png", "png_crc_invalid", depth)
            if depth == 1:
                if kind != b"IHDR" or length != 13:
                    return _invalid("png", "png_ihdr_invalid", depth)
                if int.from_bytes(first[0:4], "big") == 0 or int.from_bytes(first[4:8], "big") == 0:
                    return _invalid("png", "png_dimensions_invalid", depth)
                width = int.from_bytes(first[0:4], "big")
                height = int.from_bytes(first[4:8], "big")
                bit_depth = first[8]
                color_type = first[9]
                allowed = {
                    0: ({1, 2, 4, 8, 16}, 1),
                    2: ({8, 16}, 3),
                    3: ({1, 2, 4, 8}, 1),
                    4: ({8, 16}, 2),
                    6: ({8, 16}, 4),
                }
                if color_type not in allowed or bit_depth not in allowed[color_type][0] or first[10:12] != b"\x00\x00" or first[12] != 0:
                    if first[12] != 0:
                        return _unsupported("png", "png_interlace_unsupported", depth)
                    return _invalid("png", "png_ihdr_invalid", depth)
                row_payload = (width * allowed[color_type][1] * bit_depth + 7) // 8
                row_size = 1 + row_payload
                expected_decoded = row_size * height
                if expected_decoded <= 0 or expected_decoded > stream.max_bytes:
                    return _invalid("png", "png_decoded_size_invalid", depth)
                decompressor = zlib.decompressobj()
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
                if not saw_ihdr or (color_type == 3 and not saw_plte):
                    return _invalid("png", "png_idat_order_invalid", depth)
                saw_idat = True
            elif saw_idat:
                idat_closed = True
            if kind == b"IEND":
                if length != 0 or not saw_ihdr or not saw_idat:
                    return _invalid("png", "png_iend_invalid", depth)
                assert decompressor is not None
                try:
                    tail = decompressor.flush()
                except zlib.error:
                    return _invalid("png", "png_zlib_invalid", depth)
                if not consume_decoded(tail) or not decompressor.eof or decompressor.unused_data or decoded_bytes != expected_decoded:
                    return _invalid("png", "png_decoded_size_invalid", depth)
                if not stream.at_eof():
                    return _invalid("png", "png_trailing_bytes", depth)
                return _valid("png", depth)
            if kind[:1].isalpha() and kind[:1].isupper() and kind not in {b"PLTE", b"IDAT"}:
                return _unsupported("png", "png_critical_chunk_unsupported", depth)
    except _Truncated:
        return _invalid("png", "png_truncated", locals().get("depth", 0))


class _GifLzwValidator:
    def __init__(self, minimum_code_size: int) -> None:
        self.minimum = minimum_code_size
        self.clear = 1 << minimum_code_size
        self.end = self.clear + 1
        self.code_size = minimum_code_size + 1
        self.next_code = self.end + 1
        self.bit_buffer = 0
        self.bit_count = 0
        self.saw_clear = False
        self.saw_data = False
        self.ended = False
        self.previous = False
        self.invalid = False

    def feed(self, block: bytes) -> None:
        if self.ended and any(block):
            self.invalid = True
            return
        for value in block:
            self.bit_buffer |= value << self.bit_count
            self.bit_count += 8
            while self.bit_count >= self.code_size and not self.ended:
                code = self.bit_buffer & ((1 << self.code_size) - 1)
                self.bit_buffer >>= self.code_size
                self.bit_count -= self.code_size
                if code == self.clear:
                    self.code_size = self.minimum + 1
                    self.next_code = self.end + 1
                    self.saw_clear = True
                    self.previous = False
                    continue
                if code == self.end:
                    if not self.saw_clear or not self.saw_data:
                        self.invalid = True
                    self.ended = True
                    continue
                if not self.saw_clear or (not self.previous and code >= self.clear) or code > self.next_code:
                    self.invalid = True
                    return
                self.saw_data = True
                if self.previous and self.next_code < 4096:
                    self.next_code += 1
                    if self.next_code == (1 << self.code_size) and self.code_size < 12:
                        self.code_size += 1
                self.previous = True

    @property
    def valid(self) -> bool:
        return self.ended and self.saw_clear and self.saw_data and not self.invalid


def _gif_subblocks(stream: _BoundedStream, validator: _GifLzwValidator | None = None) -> tuple[bool, int]:
    payload_bytes = 0
    while True:
        size = stream.read_byte()
        if size == 0:
            return payload_bytes > 0, payload_bytes
        if validator is None:
            stream.skip(size)
        else:
            remaining = size
            while remaining:
                block = stream.read_exact(min(remaining, stream.MATERIALIZE_LIMIT))
                validator.feed(block)
                remaining -= len(block)
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
            validator = _GifLzwValidator(lzw_minimum)
            has_data, _length = _gif_subblocks(stream, validator)
            if not has_data or not validator.valid:
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
        unsupported_codec = False
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
                if image_chunks:
                    return _invalid("webp", "webp_image_chunk_duplicate", depth)
                minimum = 10 if kind == b"VP8 " else 5
                if length < minimum:
                    return _invalid("webp", "webp_frame_header_invalid", depth)
                header_bytes = stream.read_exact(minimum)
                if kind == b"VP8 ":
                    frame_tag = int.from_bytes(header_bytes[:3], "little")
                    partition_length = (frame_tag >> 5) & 0x7FFFF
                    width = int.from_bytes(header_bytes[6:8], "little") & 0x3FFF
                    height = int.from_bytes(header_bytes[8:10], "little") & 0x3FFF
                    if frame_tag & 1 or header_bytes[3:6] != b"\x9d\x01\x2a" or not width or not height:
                        return _invalid("webp", "webp_vp8_frame_header_invalid", depth)
                    if partition_length == 0 or partition_length > length - minimum:
                        return _invalid("webp", "webp_vp8_partition_bounds_invalid", depth)
                else:
                    if header_bytes[0] != 0x2F:
                        return _invalid("webp", "webp_vp8l_signature_invalid", depth)
                    packed_header = int.from_bytes(header_bytes[1:5], "little")
                    width = (packed_header & 0x3FFF) + 1
                    height = ((packed_header >> 14) & 0x3FFF) + 1
                    version = (packed_header >> 29) & 0x7
                    if version != 0 or not width or not height:
                        return _invalid("webp", "webp_vp8l_frame_header_invalid", depth)
                    if length == minimum:
                        return _invalid("webp", "webp_vp8l_image_data_missing", depth)
                    unsupported_codec = True
                stream.skip(length - minimum + (length & 1))
                image_chunks += 1
            elif kind == b"VP8X":
                if length != 10:
                    return _invalid("webp", "webp_vp8x_invalid", depth)
                stream.skip(padded)
            elif kind in {b"ANIM", b"ANMF"}:
                advanced = True
                stream.skip(padded)
            else:
                stream.skip(padded)
        if stream.bytes_consumed != declared_total or not stream.at_eof():
            return _invalid("webp", "webp_riff_size_invalid", depth)
        if advanced:
            return _unsupported("webp", "webp_animation_unsupported", depth)
        if image_chunks != 1:
            return _invalid("webp", "webp_image_chunk_missing", depth)
        if unsupported_codec:
            return _unsupported("webp", "webp_vp8l_bitstream_unsupported", depth)
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


def _memory_boxes(payload: bytes) -> Iterator[tuple[bytes, bytes]]:
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 8:
            raise ValueError("avif_meta_child_truncated")
        size = int.from_bytes(payload[offset : offset + 4], "big")
        kind = payload[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if len(payload) - offset < 16:
                raise ValueError("avif_meta_child_truncated")
            size = int.from_bytes(payload[offset + 8 : offset + 16], "big")
            header = 16
        if size < header or offset + size > len(payload):
            raise ValueError("avif_meta_child_bounds_invalid")
        yield kind, payload[offset + header : offset + size]
        offset += size


def _parse_avif_item_mapping(children: dict[bytes, bytes]) -> tuple[int, set[int], dict[int, tuple[int, int]]]:
    pitm = children[b"pitm"]
    if len(pitm) != 6 or pitm[:4] != b"\x00\x00\x00\x00":
        raise ValueError("avif_pitm_invalid")
    primary_item = int.from_bytes(pitm[4:6], "big")
    if primary_item == 0:
        raise ValueError("avif_pitm_invalid")

    iinf = children[b"iinf"]
    if len(iinf) < 6 or iinf[:4] != b"\x00\x00\x00\x00":
        raise ValueError("avif_iinf_invalid")
    entry_count = int.from_bytes(iinf[4:6], "big")
    entries = list(_memory_boxes(iinf[6:]))
    if entry_count != len(entries) or entry_count != 1 or entries[0][0] != b"infe":
        raise ValueError("avif_iinf_invalid")
    infe = entries[0][1]
    if len(infe) < 13 or infe[:4] != b"\x02\x00\x00\x00":
        raise ValueError("avif_infe_invalid")
    item_id = int.from_bytes(infe[4:6], "big")
    protection = int.from_bytes(infe[6:8], "big")
    if item_id == 0 or protection != 0 or infe[8:12] != b"av01" or infe[-1:] != b"\x00":
        raise ValueError("avif_infe_invalid")

    iloc = children[b"iloc"]
    if len(iloc) != 22 or iloc[:4] != b"\x00\x00\x00\x00" or iloc[4:6] != b"\x44\x00":
        raise ValueError("avif_iloc_invalid")
    if int.from_bytes(iloc[6:8], "big") != 1:
        raise ValueError("avif_iloc_invalid")
    located_item = int.from_bytes(iloc[8:10], "big")
    data_reference = int.from_bytes(iloc[10:12], "big")
    extent_count = int.from_bytes(iloc[12:14], "big")
    extent_offset = int.from_bytes(iloc[14:18], "big")
    extent_length = int.from_bytes(iloc[18:22], "big") if len(iloc) >= 22 else -1
    # The exact supported v0 layout is 22 bytes; reject the 20-byte shell.
    if len(iloc) != 22 or located_item == 0 or data_reference != 0 or extent_count != 1 or extent_length <= 0:
        raise ValueError("avif_iloc_invalid")
    return primary_item, {item_id}, {located_item: (extent_offset, extent_length)}


def _avif_meta(
    stream: _BoundedStream,
    payload_length: int,
    max_depth: int,
    depth: int,
) -> tuple[tuple[int, set[int], dict[int, tuple[int, int]]] | None, int, _Outcome | None]:
    if payload_length < 4:
        return None, depth, _invalid("avif", "avif_meta_truncated", depth)
    if payload_length > stream.MATERIALIZE_LIMIT:
        return None, depth, _unsupported("avif", "avif_meta_too_large_unsupported", depth)
    payload = stream.read_exact(payload_length)
    if payload[:4] != b"\x00\x00\x00\x00":
        return None, depth, _unsupported("avif", "avif_meta_version_unsupported", depth)
    children: dict[bytes, bytes] = {}
    try:
        for kind, child_payload in _memory_boxes(payload[4:]):
            depth += 1
            overflow = _depth(depth, max_depth, "avif")
            if overflow:
                return None, depth, overflow
            if kind in children and kind in {b"pitm", b"iloc", b"iinf"}:
                return None, depth, _invalid("avif", "avif_mandatory_box_duplicate", depth)
            children[kind] = child_payload
        if not {b"pitm", b"iloc", b"iinf"}.issubset(children):
            return None, depth, _invalid("avif", "avif_item_mapping_missing", depth)
        mapping = _parse_avif_item_mapping(children)
    except ValueError as exc:
        return None, depth, _invalid("avif", str(exc), depth)
    return mapping, depth, None


def _avif(stream: _BoundedStream, max_depth: int) -> _Outcome:
    try:
        depth = 0
        saw_ftyp = False
        saw_meta = False
        saw_mdat = False
        item_mapping: tuple[int, set[int], dict[int, tuple[int, int]]] | None = None
        mdat_extent: tuple[int, int] | None = None
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
                item_mapping, depth, error = _avif_meta(stream, payload_length, max_depth, depth)
                if error:
                    return error
                saw_meta = True
            elif kind == b"mdat":
                if saw_mdat or payload_length == 0:
                    return _invalid("avif", "avif_mdat_invalid", depth)
                mdat_extent = (stream.bytes_consumed, payload_length)
                stream.skip(payload_length)
                saw_mdat = True
            elif kind in {b"moov", b"trak", b"iref"}:
                stream.skip(payload_length)
                return _unsupported("avif", "avif_advanced_structure_unsupported", depth)
            else:
                stream.skip(payload_length)
        if not saw_ftyp or not saw_meta or not saw_mdat:
            return _invalid("avif", "avif_required_box_missing", depth)
        if item_mapping is None or mdat_extent is None:
            return _invalid("avif", "avif_item_mapping_missing", depth)
        primary, item_ids, locations = item_mapping
        if primary not in item_ids or primary not in locations or locations[primary] != mdat_extent:
            return _invalid("avif", "avif_item_mapping_inconsistent", depth)
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
