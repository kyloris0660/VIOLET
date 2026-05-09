#!/usr/bin/env python3
"""
Standalone CLIP content classifier evaluation script.

Evaluates the CLIP zero-shot anime/non-anime classifier directly on local
image folders — no database, no scan, no WD tagging required.

Usage:
  python scripts/evaluate_clip_content_classifier.py --anime-dir <path> --non-anime-dir <path> [--mixed-dir <path>]

Each directory is classified image-by-image. Results are reported per-directory
and aggregated with confusion matrix and key metrics.

Gate criteria (Phase 3.1a):
  - Non-anime false positive rate <= 10-15%
  - Anime recall >= 80%
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.clip_classifier import CLIPClassifier

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def collect_images(directory: str) -> list[Path]:
    d = Path(directory)
    if not d.exists():
        logger.error("Directory does not exist: %s", directory)
        return []
    images = sorted(
        p for p in d.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()
    )
    return images


def evaluate_directory(
    classifier: CLIPClassifier,
    directory: str,
    expected_class: str | None,
    unknown_margin: float,
) -> dict:
    images = collect_images(directory)
    if not images:
        return {"directory": directory, "error": "No images found", "count": 0}

    results = []
    class_counts = Counter()
    errors = 0
    timings = []

    for i, img_path in enumerate(images):
        t0 = time.perf_counter()
        result = classifier.classify_file(str(img_path), unknown_margin=unknown_margin)
        elapsed = time.perf_counter() - t0
        timings.append(elapsed)

        cls = result["content_class"]
        if cls == "error":
            errors += 1
        else:
            class_counts[cls] += 1

        result["elapsed_ms"] = round(elapsed * 1000, 1)
        results.append(result)

        if (i + 1) % 25 == 0 or (i + 1) == len(images):
            logger.info(
                "  [%s] %d/%d images classified (%.1f ms/img avg)",
                Path(directory).name,
                i + 1,
                len(images),
                sum(timings) / len(timings) * 1000,
            )

    total_classified = sum(class_counts.values())
    avg_ms = sum(timings) / len(timings) * 1000 if timings else 0

    summary = {
        "directory": str(directory),
        "expected_class": expected_class,
        "total_images": len(images),
        "total_classified": total_classified,
        "errors": errors,
        "class_distribution": dict(class_counts),
        "avg_ms_per_image": round(avg_ms, 1),
        "total_time_s": round(sum(timings), 1),
    }

    if expected_class and total_classified > 0:
        correct = class_counts.get(expected_class, 0)
        summary["accuracy"] = round(correct / total_classified, 4)
        summary["correct"] = correct
        summary["incorrect"] = total_classified - correct

        misclassified = [
            r for r in results
            if r["content_class"] != expected_class and r["content_class"] != "error"
        ]
        summary["misclassified_files"] = [
            {
                "file": r["file"],
                "predicted": r["content_class"],
                "best_category": r.get("best_category", ""),
                "scores": r.get("scores", {}),
                "margin": r.get("margin", 0),
                "confidence": r.get("confidence", 0),
            }
            for r in misclassified
        ]

    summary["per_image_results"] = results
    return summary


def compute_gate_metrics(eval_results: list[dict]) -> dict:
    anime_result = next((r for r in eval_results if r.get("expected_class") == "anime"), None)
    non_anime_result = next((r for r in eval_results if r.get("expected_class") == "non_anime"), None)

    metrics = {}

    if anime_result and anime_result.get("total_classified", 0) > 0:
        tc = anime_result["total_classified"]
        dist = anime_result["class_distribution"]
        anime_correct = dist.get("anime", 0)
        anime_recall = anime_correct / tc
        metrics["anime_recall"] = round(anime_recall, 4)
        metrics["anime_total"] = tc
        metrics["anime_correct"] = anime_correct
        metrics["anime_as_non_anime"] = dist.get("non_anime", 0)
        metrics["anime_as_unknown"] = dist.get("unknown", 0)

    if non_anime_result and non_anime_result.get("total_classified", 0) > 0:
        tc = non_anime_result["total_classified"]
        dist = non_anime_result["class_distribution"]
        non_anime_correct = dist.get("non_anime", 0)
        false_positives = dist.get("anime", 0)
        fp_rate = false_positives / tc
        metrics["non_anime_fp_rate"] = round(fp_rate, 4)
        metrics["non_anime_total"] = tc
        metrics["non_anime_correct"] = non_anime_correct
        metrics["non_anime_as_anime"] = false_positives
        metrics["non_anime_as_unknown"] = dist.get("unknown", 0)

    anime_recall = metrics.get("anime_recall", 0)
    fp_rate = metrics.get("non_anime_fp_rate", 1)

    metrics["gate_anime_recall_pass"] = anime_recall >= 0.80
    metrics["gate_fp_rate_pass_strict"] = fp_rate <= 0.10
    metrics["gate_fp_rate_pass_relaxed"] = fp_rate <= 0.15
    metrics["gate_overall_pass"] = (
        metrics["gate_anime_recall_pass"] and metrics["gate_fp_rate_pass_relaxed"]
    )

    return metrics


def print_report(eval_results: list[dict], gate_metrics: dict, unknown_margin: float):
    print("\n" + "=" * 80)
    print("CLIP Zero-Shot Content Classifier — Evaluation Report")
    print(f"Threshold (unknown_margin): {unknown_margin}")
    print("=" * 80)

    for r in eval_results:
        print(f"\n--- {r['directory']} ---")
        print(f"  Expected class: {r.get('expected_class', 'mixed/unknown')}")
        print(f"  Total images: {r['total_images']}")
        print(f"  Errors: {r.get('errors', 0)}")
        print(f"  Classification distribution: {r.get('class_distribution', {})}")
        if "accuracy" in r:
            print(f"  Accuracy: {r['accuracy']:.2%} ({r['correct']}/{r['total_classified']})")
        print(f"  Avg time: {r.get('avg_ms_per_image', 0):.1f} ms/image")
        print(f"  Total time: {r.get('total_time_s', 0):.1f}s")

        mis = r.get("misclassified_files", [])
        if mis:
            print(f"\n  Misclassified ({len(mis)} files):")
            for m in mis[:20]:
                print(f"    {Path(m['file']).name}: predicted={m['predicted']}, "
                      f"best_cat={m['best_category']}, margin={m['margin']:.4f}, "
                      f"scores={m['scores']}")
            if len(mis) > 20:
                print(f"    ... and {len(mis) - 20} more")

    print("\n" + "=" * 80)
    print("GATE METRICS")
    print("=" * 80)

    if "anime_recall" in gate_metrics:
        recall = gate_metrics["anime_recall"]
        status = "PASS" if gate_metrics["gate_anime_recall_pass"] else "FAIL"
        print(f"  Anime recall: {recall:.2%} "
              f"({gate_metrics['anime_correct']}/{gate_metrics['anime_total']}) "
              f"[target >= 80%] → {status}")
        print(f"    anime->non_anime: {gate_metrics.get('anime_as_non_anime', 0)}, "
              f"anime->unknown: {gate_metrics.get('anime_as_unknown', 0)}")

    if "non_anime_fp_rate" in gate_metrics:
        fp = gate_metrics["non_anime_fp_rate"]
        status_s = "PASS" if gate_metrics["gate_fp_rate_pass_strict"] else "FAIL"
        status_r = "PASS" if gate_metrics["gate_fp_rate_pass_relaxed"] else "FAIL"
        print(f"  Non-anime FP rate: {fp:.2%} "
              f"({gate_metrics['non_anime_as_anime']}/{gate_metrics['non_anime_total']}) "
              f"[target <= 10%] → {status_s}  [relaxed <= 15%] → {status_r}")
        print(f"    non_anime->unknown: {gate_metrics.get('non_anime_as_unknown', 0)}")

    overall = gate_metrics.get("gate_overall_pass", False)
    print(f"\n  OVERALL GATE: {'PASS' if overall else 'FAIL'}")
    if overall:
        print("  -> Proceed to Phase 3.1b integration")
    else:
        print("  -> STOP. Do not integrate. Report failure analysis.")

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate CLIP zero-shot anime/non-anime classifier on local folders"
    )
    parser.add_argument("--anime-dir", required=True, help="Directory of anime images")
    parser.add_argument("--non-anime-dir", required=True, help="Directory of non-anime images")
    parser.add_argument("--mixed-dir", help="Directory of mixed images (optional)")
    parser.add_argument(
        "--unknown-margin", type=float, default=0.04,
        help="Margin threshold for unknown classification (default: 0.04)"
    )
    parser.add_argument(
        "--output-json", help="Path to save detailed JSON results"
    )
    args = parser.parse_args()

    classifier = CLIPClassifier()
    logger.info("Loading CLIP classifier...")
    if not classifier.ensure_loaded():
        logger.error("Failed to load CLIP classifier")
        sys.exit(1)

    logger.info("Model info: %s", json.dumps(classifier.model_info(), indent=2))

    eval_results = []

    logger.info("\n=== Evaluating ANIME directory: %s ===", args.anime_dir)
    anime_eval = evaluate_directory(classifier, args.anime_dir, "anime", args.unknown_margin)
    eval_results.append(anime_eval)

    logger.info("\n=== Evaluating NON-ANIME directory: %s ===", args.non_anime_dir)
    non_anime_eval = evaluate_directory(classifier, args.non_anime_dir, "non_anime", args.unknown_margin)
    eval_results.append(non_anime_eval)

    if args.mixed_dir:
        logger.info("\n=== Evaluating MIXED directory: %s ===", args.mixed_dir)
        mixed_eval = evaluate_directory(classifier, args.mixed_dir, None, args.unknown_margin)
        eval_results.append(mixed_eval)

    gate_metrics = compute_gate_metrics(eval_results)
    print_report(eval_results, gate_metrics, args.unknown_margin)

    if args.output_json:
        output = {
            "gate_metrics": gate_metrics,
            "unknown_margin": args.unknown_margin,
            "model_info": classifier.model_info(),
            "evaluations": [
                {k: v for k, v in r.items() if k != "per_image_results"}
                for r in eval_results
            ],
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\nDetailed results saved to: {args.output_json}")

    sys.exit(0 if gate_metrics.get("gate_overall_pass", False) else 1)


if __name__ == "__main__":
    main()
