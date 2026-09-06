"""Streaming metadata enumeration with finite initial admission limits."""

import os
import time
from pathlib import Path

from .source_read_diagnostics import exception_detail


def source_files(root_path, *, errors=None, dispositions=None, max_entries=100000, max_seconds=300, max_depth=64):
    deadline = time.monotonic() + max_seconds
    seen = 0
    stack = [(Path(root_path), 0)]

    def unknown(directory, reason, detail=None):
        if errors is not None:
            errors.append({**(detail or {}), "stage": "directory_enumeration", "path": str(directory),
                           "reason": reason, "coverage": "unknown"})

    while stack:
        directory, depth = stack.pop()
        if depth > max_depth:
            unknown(directory, "enumeration_depth_limit")
            continue
        if seen >= max_entries or time.monotonic() >= deadline:
            unknown(directory, "enumeration_resource_limit")
            return
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if seen >= max_entries or time.monotonic() >= deadline:
                        unknown(directory, "enumeration_resource_limit")
                        return
                    seen += 1
                    path = Path(entry.path)
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError as exc:
                        unknown(path, "member_type_error", exception_detail(exc, stage="member_type"))
                        yield path
                        continue
                    if is_directory:
                        excluded = entry.name in {".git", "__pycache__", "venv"}
                        if dispositions is not None:
                            dispositions.append(dict(path=str(path), stage="directory_enumeration",
                                disposition="excluded_directory" if excluded else "directory_scheduled"))
                        if not excluded:
                            stack.append((path, depth + 1))
                    else:
                        yield path
        except OSError as exc:
            unknown(directory, "source_walk_error", exception_detail(exc, stage="directory_enumeration"))
