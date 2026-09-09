"""Finite source enumeration with interruptible open, next and member-type I/O."""

import time
from pathlib import Path

from .bounded_source_io import SourceIOWorker, SourceMetadataError, DEFAULT_TIMEOUT


def source_files(root_path, *, errors=None, dispositions=None, max_entries=100000,
                 max_seconds=300, max_depth=64, max_path_bytes=32 * 1024 * 1024,
                 io_timeout=DEFAULT_TIMEOUT, _worker_target=None):
    deadline = time.monotonic() + max_seconds
    seen = path_bytes = 0
    stack = [(Path(root_path), 0)]
    worker = SourceIOWorker(worker_target=_worker_target)

    def unknown(directory, reason, detail=None):
        if errors is not None:
            errors.append({**(detail or {}), "stage": "directory_enumeration", "path": str(directory),
                "reason": reason, "coverage": "unknown", "continuation": "new_plan_retry_unknown_subtree"})

    def call(op, *args):
        return worker.call(op, *args, timeout=min(io_timeout, max(0.001, deadline - time.monotonic())))

    try:
        while stack:
            directory, depth = stack.pop()
            if depth > max_depth:
                unknown(directory, "enumeration_depth_limit")
                continue
            if seen >= max_entries or time.monotonic() >= deadline or path_bytes >= max_path_bytes:
                unknown(directory, "enumeration_resource_limit")
                for pending, _depth in stack:
                    unknown(pending, "enumeration_resource_limit")
                return
            try:
                call("scan_open", str(directory))
                while True:
                    if seen >= max_entries or time.monotonic() >= deadline or path_bytes >= max_path_bytes:
                        unknown(directory, "enumeration_resource_limit")
                        break
                    entry = call("scan_next")
                    if entry is None:
                        break
                    name, is_directory, detail = entry
                    path = directory / name
                    seen += 1
                    path_bytes += len(str(path).encode("utf-8"))
                    if path_bytes > max_path_bytes:
                        unknown(directory, "enumeration_resource_limit")
                        break
                    if detail:
                        unknown(path, "member_type_error", detail)
                    if is_directory:
                        excluded = name in {".git", "__pycache__", "venv"}
                        if dispositions is not None:
                            dispositions.append(dict(path=str(path), stage="directory_enumeration",
                                disposition="excluded_directory" if excluded else "directory_scheduled"))
                        if not excluded:
                            stack.append((path, depth + 1))
                    else:
                        yield path
            except SourceMetadataError as exc:
                unknown(directory, "source_walk_error", exc.diagnostic)
                if exc.diagnostic.get("shared_dependency"):
                    return
                worker.close()
    finally:
        worker.close()
