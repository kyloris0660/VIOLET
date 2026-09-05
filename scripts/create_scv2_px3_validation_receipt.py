#!/usr/bin/env python3
"""Issue the fixed same-HEAD local validation receipt for SCV2-PX3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_contracts.scv2_px3_contract import (  # noqa: E402
    Scv2Px3EvidencePaths,
    load_px3_evidence_artifacts,
)
from scripts.scv2_px3_validation_receipt import (  # noqa: E402
    EVIDENCE_ARTIFACT_NAMES,
    create_same_head_validation_receipt,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run canonical focused PX3 tests and issue a same-HEAD receipt."
    )
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args(argv)
    try:
        evidence = load_px3_evidence_artifacts(
            Scv2Px3EvidencePaths(Path(args.evidence_dir)), require_receipt=False
        )
        receipt = create_same_head_validation_receipt(
            repo_root=ROOT,
            evidence_root=evidence["_root"],
            evidence_payloads={name: evidence[name] for name in EVIDENCE_ARTIFACT_NAMES},
        )
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "passed": True,
                "schema_version": receipt["schema_version"],
                "git_head": receipt["git_head"],
                "git_tree": receipt["git_tree"],
                "command_fingerprint": receipt["command_fingerprint"],
                "stdout_fingerprint": receipt["stdout_fingerprint"],
                "clean_before_after": receipt["clean_before_after"],
                "same_head_tree": receipt["same_head_tree"],
                "trust_level": receipt["trust_level"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
