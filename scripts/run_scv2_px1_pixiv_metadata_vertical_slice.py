#!/usr/bin/env python3
"""Run the SCV2-PX1 synthetic/offline Pixiv metadata vertical slice."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

def _run(workspace: Path, *, retain_evidence: bool) -> dict[str, object]:
    from app.services.pixiv_metadata_vertical_slice_service import (
        repository_synthetic_pixiv_fixture,
        run_synthetic_pixiv_vertical_slice,
        write_synthetic_vertical_slice_evidence,
    )

    fixture = repository_synthetic_pixiv_fixture()
    summary = run_synthetic_pixiv_vertical_slice(
        workspace=workspace,
        fixture=fixture,
    )
    if retain_evidence:
        write_synthetic_vertical_slice_evidence(
            workspace,
            fixture=fixture,
            summary=summary,
        )
    return summary


@contextmanager
def _task_runtime_environment() -> Iterator[None]:
    keys = (
        "VIOLET_SKIP_DOTENV",
        "VIOLET_ENV",
        "POSTGRES_DB",
        "TEST_DATABASE_URL",
        "VIOLET_STORAGE_ROOT",
        "VIOLET_TEST_STORAGE_ROOT",
    )
    previous = {key: os.environ.get(key) for key in keys}
    with tempfile.TemporaryDirectory(
        prefix="violet-scv2-px1-runtime-storage-"
    ) as runtime_storage:
        os.environ.update(
            {
                "VIOLET_SKIP_DOTENV": "1",
                "VIOLET_ENV": "test",
                "POSTGRES_DB": "scv2_px1_task_temp",
                "TEST_DATABASE_URL": "",
                "VIOLET_STORAGE_ROOT": runtime_storage,
                "VIOLET_TEST_STORAGE_ROOT": runtime_storage,
            }
        )
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Retain fixed-name evidence in this task-owned OS-temporary directory.",
    )
    args = parser.parse_args(argv)
    with _task_runtime_environment():
        if args.evidence_dir is not None:
            summary = _run(args.evidence_dir, retain_evidence=True)
        else:
            with tempfile.TemporaryDirectory(prefix="violet-scv2-px1-") as temporary:
                summary = _run(Path(temporary), retain_evidence=False)
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
