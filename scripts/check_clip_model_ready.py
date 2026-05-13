"""Preflight check: verify CLIP model is cached locally and loadable.

Usage:
    python scripts/check_clip_model_ready.py
    python scripts/check_clip_model_ready.py --json

Exits 0 if the CLIP classifier can be loaded successfully from local cache.
Exits 1 if the model is missing, corrupted, or cannot be loaded.

This script is cache-only by default: it forces ``HF_HUB_OFFLINE=1``
before loading the classifier so that ``huggingface_hub`` never attempts
a network download.  If the model is not already cached locally the
check fails with exit 1.
"""
import json as _json
import os
import sys
import time
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def check_clip_ready(*, verbose: bool = True) -> dict:
    """Check that CLIPClassifier can load from local cache.

    Forces ``HF_HUB_OFFLINE=1`` to guarantee no network access.
    The previous value is saved and restored after the check.

    Returns a dict with:
        ready (bool): True if model loaded successfully
        model_info (dict): Model metadata if loaded
        error (str|None): Error message if not ready
        elapsed_ms (int): Time taken in milliseconds
        hf_hub_offline (str): Value of HF_HUB_OFFLINE *during* the check
        cache_only (bool): Always True — script never downloads
    """
    start = time.time()

    # ── Force cache-only mode ─────────────────────────────────────
    _prev_offline = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"

    result = {
        "ready": False,
        "model_info": None,
        "error": None,
        "elapsed_ms": 0,
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE", ""),
        "cache_only": True,
    }

    try:
        from app.services.clip_classifier import CLIPClassifier

        classifier = CLIPClassifier()

        # Reset any previous failure state so we get a fresh load attempt
        # (the singleton may carry stale cooldown from a prior failed run)
        classifier._load_failed = False
        classifier._load_error = None
        classifier._load_failed_at = None

        loaded = classifier.ensure_loaded()

        if loaded:
            result["ready"] = True
            result["model_info"] = classifier.model_info()
            if verbose:
                info = result["model_info"]
                print(f"OK: CLIP model ready (cache-only)")
                print(f"  Provider: {info.get('provider')}")
                print(f"  Model: {info.get('model')}")
                print(f"  Categories: {info.get('categories')}")
                print(f"  Loaded: {info.get('loaded')}")
        else:
            result["error"] = classifier._load_error or "ensure_loaded() returned False"
            if verbose:
                print(f"FAIL: CLIP model not ready: {result['error']}")

    except ImportError as e:
        result["error"] = f"Import error: {e}"
        if verbose:
            print(f"FAIL: {result['error']}")
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        if verbose:
            print(f"FAIL: {result['error']}")
    finally:
        # ── Restore previous HF_HUB_OFFLINE value ────────────────
        if _prev_offline is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = _prev_offline

    result["elapsed_ms"] = int((time.time() - start) * 1000)
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Preflight check: verify CLIP model is cached locally and loadable (cache-only, no download)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output result as JSON (for automation)"
    )
    args = parser.parse_args()

    verbose = not args.json
    result = check_clip_ready(verbose=verbose)

    if args.json:
        print(_json.dumps(result, indent=2, ensure_ascii=False))

    sys.exit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
