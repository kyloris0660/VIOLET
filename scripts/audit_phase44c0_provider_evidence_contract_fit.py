"""Print the Phase 4.4-C0 provider-evidence schema-fit audit.

Lifecycle: reusable validation/safety tool. This script is non-mutating: it
does not open a DB connection, call providers, upload images, or write files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.provider_evidence_schema_fit import audit_provider_evidence_contract_fit  # noqa: E402


def main() -> int:
    print(json.dumps(audit_provider_evidence_contract_fit(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
