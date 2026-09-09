"""Spawn-safe real blocking filesystem fixtures (no application state)."""

import errno
import os
from pathlib import Path
import time


def filesystem_worker(conn):
    from app.utils.bounded_source_io import _worker_main
    original_scandir, original_stat, original_resolve = os.scandir, Path.stat, Path.resolve

    class BlockedIterator:
        def __next__(self):
            time.sleep(60)
            raise StopIteration

        def close(self):
            pass

    def scandir(path):
        name = Path(path).name
        if name == "blocked-open":
            time.sleep(60)
        if name == "blocked-next":
            return BlockedIterator()
        if name == "denied":
            raise PermissionError(errno.EACCES, "fixture denied", str(path))
        return original_scandir(path)

    def stat(path, *args, **kwargs):
        if path.name == "blocked-stat":
            time.sleep(60)
        return original_stat(path, *args, **kwargs)

    def resolve(path, *args, **kwargs):
        if path.name == "blocked-resolve.png":
            time.sleep(60)
        return original_resolve(path, *args, **kwargs)

    os.scandir, Path.stat, Path.resolve = scandir, stat, resolve
    _worker_main(conn)


def hash_worker(path, conn):
    from app.utils.local_library_scanner import _hash_file_in_subprocess
    if Path(path).name == 'timeout.png':
        time.sleep(60)
    elif Path(path).name == 'error.png':
        from app.utils.source_read_diagnostics import exception_detail
        conn.send(('error', exception_detail(OSError(5, 'fixture read error'), stage='source_hash')))
        conn.close()
    else:
        _hash_file_in_subprocess(path, conn)
