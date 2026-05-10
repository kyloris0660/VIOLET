#!/usr/bin/env python3
"""
Generate pre-computed CLIP text embeddings for zero-shot classification.

Downloads the CLIP text encoder ONNX model and tokenizer from HuggingFace,
tokenizes prompts from clip_prompts.json, runs them through the text encoder,
computes per-category centroids, and saves to clip_text_embeddings.npz.

Requirements (dev-time only, not needed at runtime):
  - tokenizers (pip install tokenizers)
  - onnxruntime, numpy, huggingface_hub (already in requirements.txt)

Usage:
  python scripts/generate_clip_text_embeddings.py
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as rt
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

REPO_ID = "Xenova/clip-vit-base-patch32"
REVISION = "d15189d"
TEXT_MODEL_FILE = "onnx/text_model.onnx"
TOKENIZER_FILE = "tokenizer.json"

ASSETS_DIR = Path(__file__).parent.parent / "backend" / "app" / "assets" / "content_classification"
PROMPTS_FILE = ASSETS_DIR / "clip_prompts.json"
OUTPUT_FILE = ASSETS_DIR / "clip_text_embeddings.npz"

CONTEXT_LENGTH = 77


def download_files():
    print(f"Downloading text model from {REPO_ID} (revision {REVISION})...")
    text_model_path = hf_hub_download(
        repo_id=REPO_ID, filename=TEXT_MODEL_FILE, revision=REVISION
    )
    print(f"  text_model: {text_model_path}")

    tokenizer_path = hf_hub_download(
        repo_id=REPO_ID, filename=TOKENIZER_FILE, revision=REVISION
    )
    print(f"  tokenizer: {tokenizer_path}")
    return text_model_path, tokenizer_path


def tokenize_prompts(tokenizer: Tokenizer, prompts: list[str]) -> np.ndarray:
    all_input_ids = []
    all_attention_masks = []

    for prompt in prompts:
        encoded = tokenizer.encode(prompt)
        ids = encoded.ids

        if len(ids) > CONTEXT_LENGTH:
            ids = ids[:CONTEXT_LENGTH]
            ids[-1] = 49407  # EOS token

        padded_ids = ids + [0] * (CONTEXT_LENGTH - len(ids))
        attention_mask = [1] * len(ids) + [0] * (CONTEXT_LENGTH - len(ids))

        all_input_ids.append(padded_ids)
        all_attention_masks.append(attention_mask)

    return (
        np.array(all_input_ids, dtype=np.int64),
        np.array(all_attention_masks, dtype=np.int64),
    )


def get_text_embeddings(
    session: rt.InferenceSession,
    input_ids: np.ndarray,
    attention_mask: np.ndarray,
) -> np.ndarray:
    input_names = [inp.name for inp in session.get_inputs()]
    feed = {"input_ids": input_ids}
    if "attention_mask" in input_names:
        feed["attention_mask"] = attention_mask

    outputs = session.run(None, feed)

    output_names = [out.name for out in session.get_outputs()]
    print(f"  Text model outputs: {output_names}")
    for i, name in enumerate(output_names):
        print(f"    {name}: shape={outputs[i].shape}")

    embeddings = outputs[0]  # (batch, 512) — already projected text_embeds

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    return embeddings


def main():
    if not PROMPTS_FILE.exists():
        print(f"ERROR: Prompts file not found: {PROMPTS_FILE}")
        sys.exit(1)

    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    text_model_path, tokenizer_path = download_files()

    print("Loading tokenizer...")
    tokenizer = Tokenizer.from_file(tokenizer_path)

    print("Loading text encoder ONNX session...")
    opts = rt.SessionOptions()
    opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = rt.InferenceSession(
        text_model_path, sess_options=opts, providers=["CPUExecutionProvider"]
    )

    print(f"\nModel inputs: {[inp.name for inp in session.get_inputs()]}")
    print(f"Model outputs: {[out.name for out in session.get_outputs()]}")

    embeddings_dict = {}

    for cat_name, cat_info in config["categories"].items():
        prompts = cat_info["prompts"]
        print(f"\nCategory '{cat_name}': {len(prompts)} prompts")
        for p in prompts:
            print(f"  - {p}")

        input_ids, attention_mask = tokenize_prompts(tokenizer, prompts)
        embeddings = get_text_embeddings(session, input_ids, attention_mask)

        print(f"  Embeddings shape: {embeddings.shape}")
        print(f"  Per-prompt norms: {np.linalg.norm(embeddings, axis=1)}")

        centroid = np.mean(embeddings, axis=0).astype(np.float32)
        centroid = centroid / np.linalg.norm(centroid)

        embeddings_dict[f"centroid_{cat_name}"] = centroid
        embeddings_dict[f"prompts_{cat_name}"] = embeddings

        print(f"  Centroid norm: {np.linalg.norm(centroid):.6f}")

    print(f"\nSaving to {OUTPUT_FILE}...")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(str(OUTPUT_FILE), **embeddings_dict)

    file_size = OUTPUT_FILE.stat().st_size
    file_hash = hashlib.sha256(OUTPUT_FILE.read_bytes()).hexdigest()

    print(f"\nOutput: {OUTPUT_FILE}")
    print(f"  Size: {file_size:,} bytes")
    print(f"  SHA256: {file_hash}")

    print("\nSaved arrays:")
    data = np.load(str(OUTPUT_FILE))
    for key in sorted(data.files):
        arr = data[key]
        print(f"  {key}: shape={arr.shape}, dtype={arr.dtype}")

    print("\nPairwise centroid cosine similarities:")
    cat_names = list(config["categories"].keys())
    for i, c1 in enumerate(cat_names):
        for c2 in cat_names[i + 1:]:
            sim = float(np.dot(
                embeddings_dict[f"centroid_{c1}"],
                embeddings_dict[f"centroid_{c2}"],
            ))
            print(f"  {c1} <-> {c2}: {sim:.4f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
