import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, Tuple

import cv2
from PIL import Image

from ..schemas import FileTypeEnum
from .logger import logger

# ---------------------------------------------------------------------------
# python-magic availability cache
# ---------------------------------------------------------------------------
# python-magic (+ system libmagic) may not be installed. We probe once on
# first use and cache the result so that large scans don't pay O(N) exception
# overhead or produce O(N) warning lines when the library is missing.
_MAGIC_DETECTOR = None
_MAGIC_PROBE_DONE = False
_MAGIC_UNAVAILABLE_REASON: str | None = None


def _get_magic_detector():
    """Return a cached ``magic.Magic(mime=True)`` instance, or *None*.

    The probe runs at most once per process.  If ``import magic`` or
    ``magic.Magic(mime=True)`` fails, subsequent calls return *None*
    immediately without retrying.
    """
    global _MAGIC_DETECTOR, _MAGIC_PROBE_DONE, _MAGIC_UNAVAILABLE_REASON

    if _MAGIC_PROBE_DONE:
        return _MAGIC_DETECTOR

    _MAGIC_PROBE_DONE = True
    try:
        import magic

        _MAGIC_DETECTOR = magic.Magic(mime=True)
    except Exception as exc:
        _MAGIC_DETECTOR = None
        _MAGIC_UNAVAILABLE_REASON = str(exc)
        logger.warning(
            "python-magic unavailable; MIME detection will use "
            "PIL/mimetypes fallbacks: %s",
            exc,
        )
    return _MAGIC_DETECTOR


def _reset_magic_cache():
    """Reset the module-level magic cache (for testing only)."""
    global _MAGIC_DETECTOR, _MAGIC_PROBE_DONE, _MAGIC_UNAVAILABLE_REASON
    _MAGIC_DETECTOR = None
    _MAGIC_PROBE_DONE = False
    _MAGIC_UNAVAILABLE_REASON = None


def is_valid_mime_type(value: str) -> bool:
    """Check whether *value* looks like a valid MIME type (type/subtype).

    Rules:
    - Must contain exactly one ``/``
    - Total length must be <= 100 (VARCHAR(100) column constraint)
    - Must not be empty or whitespace-only
    """
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) > 100:
        return False
    parts = value.split("/")
    if len(parts) != 2:
        return False
    return bool(parts[0]) and bool(parts[1])


def calculate_file_hash(file_path: Path) -> str:
    """Calculate MD5 hash of a file"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_mime_type(file_path: Path) -> str:
    """Detect MIME type with a robust fallback chain.

    Order:
    1. python-magic  — only accepted if result is a valid MIME string
    2. PIL content sniff — opens the file header, maps Image.format → MIME
    3. mimetypes.guess_type — stdlib, extension-based (least reliable)
    4. ``application/octet-stream`` — safe ultimate fallback
    """
    # --- 1. python-magic (cached probe) -------------------------------------
    detector = _get_magic_detector()
    if detector is not None:
        try:
            result = detector.from_file(str(file_path))
            if result and is_valid_mime_type(result):
                return result
            logger.warning(
                "python-magic returned invalid MIME for %s: %s — trying fallbacks",
                file_path.name,
                repr(result)[:200],
            )
        except Exception as exc:
            # Per-file failure is fine — log and fall through to PIL/mimetypes.
            logger.warning(
                "python-magic failed for %s: %s — trying fallbacks",
                file_path.name,
                exc,
            )

    # --- 2. PIL content sniff ----------------------------------------------
    try:
        with Image.open(file_path) as img:
            fmt = img.format  # e.g. "PNG", "JPEG", "GIF", "WEBP"
            if fmt:
                pil_mime = Image.MIME.get(fmt)
                if pil_mime and is_valid_mime_type(pil_mime):
                    logger.info("PIL detected MIME for %s: %s", file_path.name, pil_mime)
                    return pil_mime
    except Exception:
        pass  # not an image PIL can open — that's fine

    # --- 3. mimetypes.guess_type (extension-based) -------------------------
    guessed, _ = mimetypes.guess_type(str(file_path))
    if guessed and is_valid_mime_type(guessed):
        logger.info("mimetypes guessed MIME for %s: %s", file_path.name, guessed)
        return guessed

    # --- 4. ultimate fallback ----------------------------------------------
    logger.warning("All MIME detection failed for %s — using application/octet-stream", file_path.name)
    return "application/octet-stream"

def determine_file_type(mime_type: str, filename: str, file_path: Path = None) -> FileTypeEnum:
    """Determine if file is image, video, or gif"""
    if mime_type.startswith('video/'):
        return FileTypeEnum.video
    elif mime_type == 'image/gif':
        return FileTypeEnum.gif
    elif mime_type == 'image/webp':
        if file_path and is_animated_webp(file_path):
            return FileTypeEnum.gif
        else:
            return FileTypeEnum.image
    elif mime_type.startswith('image/'):
        return FileTypeEnum.image
    else:
        ext = filename.lower().split('.')[-1]
        if ext in ['mp4', 'webm', 'mov', 'avi', 'mkv']:
            return FileTypeEnum.video
        elif ext == 'gif':
            return FileTypeEnum.gif
        elif ext == 'webp' and file_path and is_animated_webp(file_path):
            return FileTypeEnum.gif
        else:
            return FileTypeEnum.image

def is_animated_webp(file_path: Path) -> bool:
    """Check if a WebP file is animated by looking for the ANIM chunk"""
    try:
        with open(file_path, 'rb') as f:
            # Read first 12 bytes to check RIFF header
            header = f.read(12)
            if len(header) < 12:
                return False
            
            # Check for RIFF and WEBP signature
            if header[0:4] != b'RIFF' or header[8:12] != b'WEBP':
                return False
            
            # Look for ANIM chunk in the next 1KB
            chunk_data = f.read(1024)
            
            return b'ANIM' in chunk_data
    except Exception as e:
        logger.error(f"Error checking if WebP is animated: {e}")
        return False

def get_image_dimensions(file_path: Path) -> Optional[Tuple[int, int]]:
    """Get dimensions of an image"""
    try:
        with Image.open(file_path) as img:
            return img.size
    except Exception:
        return None

def get_video_info(file_path: Path) -> Optional[dict]:
    """Get video dimensions and duration"""
    try:
        cap = cv2.VideoCapture(str(file_path))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        cap.release()
        
        return {
            'width': width,
            'height': height,
            'duration': duration
        }
    except Exception:
        return None

def process_media_file(file_path: Path) -> dict:
    """Process media file and extract metadata"""
    file_size = file_path.stat().st_size
    file_hash = calculate_file_hash(file_path)
    mime_type = get_mime_type(file_path)
    file_type = determine_file_type(mime_type, file_path.name, file_path)
    
    result = {
        'hash': file_hash,
        'mime_type': mime_type,
        'file_type': file_type,
        'file_size': file_size,
        'width': None,
        'height': None,
        'duration': None
    }
    
    if file_type in [FileTypeEnum.image, FileTypeEnum.gif]:
        dimensions = get_image_dimensions(file_path)
        if dimensions:
            result['width'], result['height'] = dimensions
    elif file_type == FileTypeEnum.video:
        video_info = get_video_info(file_path)
        if video_info:
            result.update(video_info)
    return result
