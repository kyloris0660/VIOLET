#!/usr/bin/env python3
"""Run the SCV2-PX1 synthetic/offline Pixiv metadata vertical slice."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.pixiv_metadata_projection_service import canonical_json_bytes  # noqa: E402
from app.services.pixiv_metadata_vertical_slice_service import (  # noqa: E402
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
    write_synthetic_vertical_slice_evidence,
)


def _run(workspace: Path, *, retain_evidence: bool) -> dict[str, object]:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Retain fixed-name evidence in this task-owned OS-temporary directory.",
    )
    args = parser.parse_args(argv)
    if args.evidence_dir is not None:
        summary = _run(args.evidence_dir, retain_evidence=True)
    else:
        with tempfile.TemporaryDirectory(prefix="violet-scv2-px1-") as temporary:
            summary = _run(Path(temporary), retain_evidence=False)
    sys.stdout.buffer.write(canonical_json_bytes(summary) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
