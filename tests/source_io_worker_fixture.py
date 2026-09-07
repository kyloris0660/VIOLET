"""Spawn-safe real blocking filesystem fixtures (no application state)."""

import errno
import os
from pathlib import Path
import time


def filesystem_worker(conn):
    from app.utils.bounded_source_io import _worker_main
    original_scandir, original_stat = os.scandir, Path.stat

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

    os.scandir, Path.stat = scandir, stat
    _worker_main(conn)
