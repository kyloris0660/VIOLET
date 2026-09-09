"""Deadline-enforced source metadata RPCs with terminated and reaped workers.

Reuse one worker per synchronous plan/execution. Directory iterators have a
separate worker so a bad member's stat cannot discard the directory cursor.
"""

from contextlib import contextmanager
from functools import wraps
import multiprocessing
import os
from pathlib import Path
import stat
import threading
import time

from .source_read_diagnostics import exception_detail, worker_detail

DEFAULT_TIMEOUT = 10.0
_local = threading.local()


class SourceMetadataError(OSError):
    def __init__(self, diagnostic):
        super().__init__(diagnostic.get("errno"), diagnostic.get("message") or "Source metadata unavailable")
        self.diagnostic = diagnostic


def _worker_main(conn):
    entries = None
    try:
        while True:
            op, args = conn.recv()
            try:
                if op == "scan_open":
                    if entries is not None:
                        entries.close()
                    entries = os.scandir(args[0])
                    result = None
                elif op == "scan_next":
                    entry = next(entries, None)
                    if entry is None:
                        result = None
                    else:
                        try:
                            result = (entry.name, entry.is_dir(follow_symlinks=False), None)
                        except OSError as exc:
                            result = (entry.name, False, exception_detail(exc, stage="member_type"))
                elif op == "stat":
                    value = Path(args[0]).stat(follow_symlinks=args[1])
                    result = dict(file_size=value.st_size, mtime=value.st_mtime,
                        mtime_ns=value.st_mtime_ns, is_file=stat.S_ISREG(value.st_mode),
                        is_dir=stat.S_ISDIR(value.st_mode), is_symlink=stat.S_ISLNK(value.st_mode))
                elif op in {"resolve", "is_symlink", "exists", "is_file", "is_dir"}:
                    result = getattr(Path(args[0]), op)()
                    if op == "resolve":
                        result = str(result)
                elif op == "scannable":
                    from .local_library_scanner import _is_scannable_file_unbounded
                    result = _is_scannable_file_unbounded(Path(args[0]), hydrated_only=args[1], max_bytes=args[2])
                elif op == "cloud_only":
                    from .local_library_scanner import _is_cloud_only_unbounded
                    result = _is_cloud_only_unbounded(Path(args[0]))
                else:
                    raise ValueError("unsupported source metadata operation")
                conn.send(("ok", result))
            except Exception as exc:
                conn.send(("error", exception_detail(exc, stage=op)))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        # Provider iterator close can block too; termination is parent-owned.
        conn.close()


class SourceIOWorker:
    def __init__(self, *, worker_target=None):
        self.worker_target = worker_target or _worker_main
        self.process = None
        self.conn = None

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        process, self.process = self.process, None
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if process.is_alive():
                raise RuntimeError("source_metadata_worker_could_not_be_terminated")
            process.close()

    def call(self, op, *args, timeout=DEFAULT_TIMEOUT):
        started = time.monotonic()
        timeout = max(0.001, float(timeout))
        try:
            if self.process is None:
                parent, child = multiprocessing.Pipe()
                process = multiprocessing.Process(target=self.worker_target, args=(child,), daemon=True)
                self.conn, self.process = parent, process
                try:
                    process.start()
                except Exception as exc:
                    self.process = None
                    parent.close()
                    self.conn = None
                    raise SourceMetadataError({**exception_detail(exc, stage=op),
                        "shared_dependency": "source_worker_start"}) from exc
                finally:
                    child.close()
            self.conn.send((op, args))
            if not self.conn.poll(max(0, timeout - (time.monotonic() - started))):
                pid = self.process.pid
                self.close()
                raise SourceMetadataError({**worker_detail(None, stage=op, status="timeout",
                    started=started, timeout=timeout, exitcode=None), "worker_pid": pid,
                    "worker_terminated": True, "reason": "read_timeout"})
            status, value = self.conn.recv()
            if status != "ok":
                raise SourceMetadataError(worker_detail(value, stage=op, status="error",
                    started=started, timeout=timeout, exitcode=None))
            return value
        except (EOFError, BrokenPipeError) as exc:
            self.close()
            raise SourceMetadataError(exception_detail(exc, stage=op)) from exc


@contextmanager
def source_io_scope():
    existing = getattr(_local, "worker", None)
    if existing is not None:
        yield existing
        return
    worker = _local.worker = SourceIOWorker()
    try:
        yield worker
    finally:
        _local.worker = None
        worker.close()


def with_source_io(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with source_io_scope():
            return function(*args, **kwargs)
    return wrapped


def source_io(op, *args, timeout=DEFAULT_TIMEOUT):
    with source_io_scope() as worker:
        return worker.call(op, *args, timeout=timeout)


def source_resolve(path):
    return Path(source_io("resolve", str(path)))
