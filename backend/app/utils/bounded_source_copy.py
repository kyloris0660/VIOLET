"""Copy one source into an exclusively owned target under a hard deadline."""

import hashlib
import multiprocessing
import time
from pathlib import Path

from .source_read_diagnostics import exception_detail, worker_detail


class SourceCopyError(OSError):
    def __init__(self, detail):
        super().__init__(detail.get("errno"), "Source copy failed", detail.get("filename"))
        self.diagnostic = detail


def _copy_worker(source, destination, conn):
    stage = "source_copy"
    try:
        digest = hashlib.md5()
        with open(source, "rb") as src, open(destination, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
                digest.update(chunk)
        stage = "copied_image_decode"
        from PIL import Image
        with Image.open(destination) as image:
            image.verify()
        with Image.open(destination) as image:
            image.load()
        conn.send(("ok", digest.hexdigest()))
    except Exception as exc:
        conn.send(("error", {**exception_detail(exc, stage=stage), "filename": getattr(exc, "filename", None),
            "copied_content_hash": digest.hexdigest() if stage == "copied_image_decode" else None}))
    finally:
        conn.close()


def copy_source(source: Path, destination: Path, *, timeout_seconds: int, expected_hash=None):
    # The parent owns this newly created path before launching a cancellable
    # child; cleanup never targets a preexisting user's file.
    with destination.open("xb"):
        pass
    started = time.monotonic()
    parent, child = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(target=_copy_worker, args=(str(source), str(destination), child), daemon=True)
    try:
        before = source.stat()
        try:
            process.start()
        except Exception as exc:
            raise SourceCopyError(worker_detail({**exception_detail(exc, stage="copy_worker_start"),
                "shared_dependency": "source_worker_start"}, stage="copy_worker_start", status="start_failed",
                started=started, timeout=timeout_seconds, exitcode=None)) from exc
        child.close()
        process.join(timeout=timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            raise SourceCopyError(worker_detail(None, stage="source_copy", status="timeout",
                started=started, timeout=timeout_seconds, exitcode=process.exitcode))
        try:
            status, result = parent.recv() if parent.poll(1) else ("error", None)
        except EOFError:
            status, result = "error", None
        after = source.stat()
        observed_hash = result if status == "ok" else (result or {}).get("copied_content_hash")
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or (expected_hash and observed_hash and expected_hash != observed_hash)):
            raise SourceCopyError({"stage": "source_copy", "reason": "content_changed_after_plan",
                                   "exception_type": None, "errno": None, "winerror": None})
        if status != "ok":
            if result and result.get("stage") == "copied_image_decode":
                result["copied_version_verified"] = bool(expected_hash and observed_hash == expected_hash)
            raise SourceCopyError(worker_detail(result, stage=(result or {}).get("stage", "source_copy"),
                status="error" if result else "no_result", started=started,
                timeout=timeout_seconds, exitcode=process.exitcode))
        return destination.stat().st_size
    except BaseException:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        if not process.is_alive():
            destination.unlink(missing_ok=True)
        raise
    finally:
        parent.close()
        child.close()
