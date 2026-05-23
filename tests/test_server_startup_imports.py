import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = REPO_ROOT / "backend"


def _without_backend_pythonpath() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    if not pythonpath:
        env.pop("PYTHONPATH", None)
        return env

    backend_resolved = BACKEND_PATH.resolve()
    kept_parts: list[str] = []
    for part in pythonpath.split(os.pathsep):
        if not part:
            continue
        try:
            if Path(part).resolve() == backend_resolved:
                continue
        except OSError:
            pass
        kept_parts.append(part)

    if kept_parts:
        env["PYTHONPATH"] = os.pathsep.join(kept_parts)
    else:
        env.pop("PYTHONPATH", None)
    return env


def test_startup_imports_do_not_require_backend_pythonpath():
    script = "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "repo = Path.cwd().resolve()",
            "backend = (repo / 'backend').resolve()",
            "assert str(backend) not in [str(Path(p).resolve()) for p in sys.path if p]",
            "import backend.app.services.source_ingestion_gate",
            "import backend.app.main",
        ]
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=_without_backend_pythonpath(),
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "No module named 'app'" not in result.stderr
