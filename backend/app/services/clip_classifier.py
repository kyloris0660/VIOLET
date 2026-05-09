"""
CLIP zero-shot anime/non-anime classifier prototype.

Uses CLIP ViT-B/32 ONNX visual encoder with pre-computed text embeddings
to classify images as anime, non_anime, or unknown.

Runtime dependencies: onnxruntime, Pillow, numpy, huggingface_hub
(all already in requirements.txt)

Model source: Xenova/clip-vit-base-patch32 (MIT license, from openai/clip-vit-base-patch32)
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import onnxruntime as rt
from PIL import Image

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent.parent / "assets" / "content_classification"
PROMPTS_FILE = ASSETS_DIR / "clip_prompts.json"
EMBEDDINGS_FILE = ASSETS_DIR / "clip_text_embeddings.npz"

CLIP_REPO_ID = "Xenova/clip-vit-base-patch32"
CLIP_REVISION = "d15189d"
CLIP_VISION_FILE = "onnx/vision_model.onnx"

CLIP_IMAGE_SIZE = 224
CLIP_IMAGE_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_IMAGE_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


class CLIPClassifier:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._session: Optional[rt.InferenceSession] = None
        self._text_embeddings: Optional[Dict[str, np.ndarray]] = None
        self._category_map: Optional[Dict[str, str]] = None
        self._prompts_config: Optional[dict] = None
        self._inference_lock = threading.Lock()
        self._initialized = True

    def _download_model(self) -> str:
        from huggingface_hub import hf_hub_download
        logger.info("Downloading CLIP vision model from %s (revision %s)...",
                     CLIP_REPO_ID, CLIP_REVISION)
        path = hf_hub_download(
            repo_id=CLIP_REPO_ID,
            filename=CLIP_VISION_FILE,
            revision=CLIP_REVISION,
        )
        logger.info("CLIP vision model downloaded: %s", path)
        return path

    def ensure_loaded(self) -> bool:
        if self._session is not None and self._text_embeddings is not None:
            return True
        with self._lock:
            if self._session is not None and self._text_embeddings is not None:
                return True
            try:
                model_path = self._download_model()
                self._load_session(model_path)
                self._load_text_embeddings()
                return True
            except Exception:
                logger.exception("Failed to load CLIP classifier")
                return False

    def _load_session(self, model_path: str):
        opts = rt.SessionOptions()
        opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = max(1, os.cpu_count() or 4)
        opts.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
        self._session = rt.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        logger.info("CLIP vision ONNX session loaded")

    def _load_text_embeddings(self):
        if not EMBEDDINGS_FILE.exists():
            raise FileNotFoundError(
                f"Text embeddings not found: {EMBEDDINGS_FILE}. "
                "Run scripts/generate_clip_text_embeddings.py first."
            )
        data = np.load(str(EMBEDDINGS_FILE))
        self._text_embeddings = {}
        self._category_map = {}

        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            self._prompts_config = json.load(f)

        for cat_name, cat_info in self._prompts_config["categories"].items():
            key = f"centroid_{cat_name}"
            if key not in data:
                raise ValueError(f"Missing centroid for category '{cat_name}' in embeddings file")
            centroid = data[key].astype(np.float32)
            centroid = centroid / np.linalg.norm(centroid)
            self._text_embeddings[cat_name] = centroid
            self._category_map[cat_name] = cat_info["maps_to"]

        logger.info("Loaded text embeddings for categories: %s",
                     list(self._text_embeddings.keys()))

    def preprocess_image(self, image: Image.Image) -> np.ndarray:
        if image.mode != "RGB":
            image = image.convert("RGB")
        w, h = image.size
        short = min(w, h)
        scale = CLIP_IMAGE_SIZE / short
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.BICUBIC)
        left = (new_w - CLIP_IMAGE_SIZE) // 2
        top = (new_h - CLIP_IMAGE_SIZE) // 2
        image = image.crop((left, top, left + CLIP_IMAGE_SIZE, top + CLIP_IMAGE_SIZE))
        pixels = np.array(image, dtype=np.float32) / 255.0
        pixels = (pixels - CLIP_IMAGE_MEAN) / CLIP_IMAGE_STD
        pixels = pixels.transpose(2, 0, 1)  # HWC -> CHW
        return pixels

    def get_image_embedding(self, image: Image.Image) -> np.ndarray:
        pixel_values = self.preprocess_image(image)
        pixel_values = np.expand_dims(pixel_values, 0)  # add batch dim
        with self._inference_lock:
            outputs = self._session.run(None, {"pixel_values": pixel_values})
        embedding = outputs[0][0]  # (512,)
        embedding = embedding / np.linalg.norm(embedding)
        return embedding

    def classify_image(
        self, image: Image.Image, unknown_margin: float = 0.005
    ) -> Dict:
        if not self.ensure_loaded():
            return {
                "content_class": "error",
                "confidence": 0.0,
                "scores": {},
                "reason": "CLIP model not loaded",
            }

        embedding = self.get_image_embedding(image)

        scores = {}
        for cat_name, centroid in self._text_embeddings.items():
            similarity = float(np.dot(embedding, centroid))
            scores[cat_name] = similarity

        sorted_cats = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_cat, best_score = sorted_cats[0]
        second_score = sorted_cats[1][1] if len(sorted_cats) > 1 else 0.0
        margin = best_score - second_score

        if margin < unknown_margin:
            content_class = "unknown"
            confidence = margin / unknown_margin
            reason = (f"Low margin ({margin:.4f} < {unknown_margin}): "
                      f"{best_cat}={best_score:.4f} vs {sorted_cats[1][0]}={second_score:.4f}")
        else:
            content_class = self._category_map.get(best_cat, "unknown")
            confidence = min(1.0, margin / 0.15)
            reason = f"Best: {best_cat}={best_score:.4f}, margin={margin:.4f}"

        return {
            "content_class": content_class,
            "confidence": round(confidence, 4),
            "best_category": best_cat,
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "margin": round(margin, 4),
            "reason": reason,
        }

    def classify_file(
        self, file_path: str, unknown_margin: float = 0.005
    ) -> Dict:
        try:
            image = Image.open(file_path)
            image.load()
        except Exception as e:
            return {
                "content_class": "error",
                "confidence": 0.0,
                "scores": {},
                "reason": f"Failed to load image: {e}",
                "file": str(file_path),
            }
        result = self.classify_image(image, unknown_margin=unknown_margin)
        result["file"] = str(file_path)
        return result

    def model_info(self) -> Dict:
        return {
            "provider": "clip_zero_shot",
            "model": "clip-vit-base-patch32",
            "onnx_source": CLIP_REPO_ID,
            "revision": CLIP_REVISION,
            "license": "MIT",
            "embedding_dim": 512,
            "image_size": CLIP_IMAGE_SIZE,
            "loaded": self._session is not None,
            "categories": list(self._text_embeddings.keys()) if self._text_embeddings else [],
        }
