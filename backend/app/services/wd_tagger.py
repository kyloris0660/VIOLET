import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import numpy as np
import onnxruntime as rt
import pandas as pd
from PIL import Image

from ..config import settings
from .job_control import (
    LoadControlConfig,
    ProviderProvenance,
    build_ai_tagging_load_control_config,
    select_onnx_provider,
)

class WDTagger:
    """
    WD Tagger using ONNX models from SmilingWolf's collection.
    Provider-aware ONNX runtime with bounded CPU fallback controls.
    """
    _instance = None
    _initialized = False
    _lock = threading.Lock()
    
    AVAILABLE_MODELS = {
        "wd-eva02-large-tagger-v3": "SmilingWolf/wd-eva02-large-tagger-v3",
        "wd-vit-tagger-v3": "SmilingWolf/wd-vit-tagger-v3",
        "wd-swinv2-tagger-v3": "SmilingWolf/wd-swinv2-tagger-v3",
        "wd-convnext-tagger-v3": "SmilingWolf/wd-convnext-tagger-v3",
        "wd-vit-large-tagger-v3": "SmilingWolf/wd-vit-large-tagger-v3",
    }
    
    # Speed ranking (relative, lower is faster)
    MODEL_SPEED_RANKING = {
        "wd-vit-tagger-v3": 1,        # Fastest
        "wd-convnext-tagger-v3": 2,
        "wd-swinv2-tagger-v3": 3,
        "wd-eva02-large-tagger-v3": 4,
        "wd-vit-large-tagger-v3": 5,  # Slowest
    }
    
    MODEL_FILENAME = "model.onnx"
    LABEL_FILENAME = "selected_tags.csv"
    
    # Optimal batch sizes per model (tuned for ~16GB RAM systems)
    OPTIMAL_BATCH_SIZES = {
        "wd-eva02-large-tagger-v3": 4,
        "wd-vit-tagger-v3": 16,
        "wd-swinv2-tagger-v3": 8,
        "wd-convnext-tagger-v3": 12,
        "wd-vit-large-tagger-v3": 2,
    }
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._model = None
            self._tag_data = None
            self._target_size = None
            self._current_model_name = None
            self._current_model_repo = None
            self._input_name = None
            self._inference_lock = threading.Lock()
            self._load_control_config = build_ai_tagging_load_control_config(settings)
            self._load_control_signature = None
            self._session_options_summary = {}
            self._provider_provenance = ProviderProvenance(
                requested_provider_preference=self._load_control_config.provider_preference,
                available_providers=tuple(rt.get_available_providers()),
                actual_provider=None,
            )
            self._num_preprocess_workers = 0
            self._preprocess_executor = None
            self._configure_preprocess_executor(self._load_control_config)
            WDTagger._initialized = True

    def _refresh_load_control_config(self) -> LoadControlConfig:
        self._load_control_config = build_ai_tagging_load_control_config(settings)
        self._configure_preprocess_executor(self._load_control_config)
        return self._load_control_config

    def _configure_preprocess_executor(self, config: LoadControlConfig) -> None:
        if self._preprocess_executor and self._num_preprocess_workers == config.preprocess_workers:
            return
        if self._preprocess_executor:
            self._preprocess_executor.shutdown(wait=False)
        self._num_preprocess_workers = config.preprocess_workers
        self._preprocess_executor = ThreadPoolExecutor(
            max_workers=self._num_preprocess_workers,
            thread_name_prefix="wd_preprocess",
        )
    
    def _get_session_options(self, config: Optional[LoadControlConfig] = None) -> rt.SessionOptions:
        """Create bounded ONNX Runtime session options."""
        config = config or self._load_control_config
        sess_options = rt.SessionOptions()
        
        # Enable all graph optimizations
        sess_options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL

        sess_options.intra_op_num_threads = config.cpu_intra_op_threads
        sess_options.inter_op_num_threads = config.cpu_inter_op_threads
        sess_options.execution_mode = getattr(
            rt.ExecutionMode,
            config.execution_mode,
            rt.ExecutionMode.ORT_SEQUENTIAL,
        )
        
        # Enable memory pattern optimization
        sess_options.enable_mem_pattern = True
        
        # Disable memory arena shrinking for better performance with repeated inference
        sess_options.enable_cpu_mem_arena = True
        
        return sess_options
    
    def _load_model(
        self,
        model_name: str = "wd-eva02-large-tagger-v3",
        *,
        local_files_only: bool = False,
    ):
        """Load the specified model with bounded provider/load controls."""
        if model_name not in self.AVAILABLE_MODELS:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(self.AVAILABLE_MODELS.keys())}")

        config = self._refresh_load_control_config()
        signature = config.runtime_signature()
        if (
            self._current_model_name == model_name
            and self._model is not None
            and self._load_control_signature == signature
        ):
            return
        
        try:
            import huggingface_hub
        except ImportError:
            raise ImportError("huggingface_hub is required. Install with: pip install huggingface_hub")
        
        model_repo = self.AVAILABLE_MODELS[model_name]
        
        # Resolve model files from the Hugging Face cache, downloading only
        # when local_files_only is False.
        csv_path = huggingface_hub.hf_hub_download(
            model_repo,
            self.LABEL_FILENAME,
            local_files_only=local_files_only,
        )
        model_path = huggingface_hub.hf_hub_download(
            model_repo,
            self.MODEL_FILENAME,
            local_files_only=local_files_only,
        )
        
        # Load tags
        df = pd.read_csv(csv_path)
        
        # Pre-compute indices as numpy arrays for faster lookup
        self._tag_data = {
            'names': df["name"].tolist(),
            'rating': np.where(df["category"] == 9)[0],
            'general': np.where(df["category"] == 0)[0],
            'character': np.where(df["category"] == 4)[0],
        }
        
        sess_options = self._get_session_options(config)
        selection = select_onnx_provider(
            config.provider_preference,
            rt.get_available_providers(),
            allow_fallback=config.allow_provider_fallback,
        )
        provider_errors: List[str] = []
        session = None
        loaded_providers: tuple[str, ...] = ()

        for provider in selection.candidate_provider_order:
            try:
                session = rt.InferenceSession(
                    model_path,
                    sess_options=sess_options,
                    providers=[provider],
                )
                loaded_providers = tuple(session.get_providers())
                break
            except Exception as exc:
                provider_errors.append(f"{provider}:{exc.__class__.__name__}")

        if session is None:
            self._provider_provenance = ProviderProvenance(
                requested_provider_preference=selection.requested_provider_preference,
                available_providers=selection.available_providers,
                actual_provider=None,
                fallback_occurred=True,
                fallback_reason=selection.fallback_reason or "no_provider_loaded",
                provider_load_errors=tuple(provider_errors),
            )
            raise RuntimeError(
                "Unable to load WDTagger ONNX session with requested providers: "
                + ", ".join(selection.requested_provider_preference)
            )

        actual_provider = loaded_providers[0] if loaded_providers else selection.selected_provider
        fallback_reason = selection.fallback_reason
        fallback_occurred = selection.fallback_occurred
        if provider_errors:
            fallback_occurred = True
            error_reason = "provider_load_errors=" + ",".join(provider_errors)
            fallback_reason = f"{fallback_reason}; {error_reason}" if fallback_reason else error_reason

        self._model = session
        self._session_options_summary = {
            "cpu_intra_op_threads": config.cpu_intra_op_threads,
            "cpu_inter_op_threads": config.cpu_inter_op_threads,
            "execution_mode": config.execution_mode,
        }
        self._provider_provenance = ProviderProvenance(
            requested_provider_preference=selection.requested_provider_preference,
            available_providers=selection.available_providers,
            actual_provider=actual_provider,
            loaded_providers=loaded_providers,
            fallback_occurred=fallback_occurred,
            fallback_reason=fallback_reason,
            provider_load_errors=tuple(provider_errors),
        )

        input_info = self._model.get_inputs()[0]
        self._target_size = input_info.shape[2]
        self._input_name = input_info.name
        self._current_model_name = model_name
        self._current_model_repo = model_repo
        self._load_control_signature = signature

    def ensure_loaded(
        self,
        model_name: str = "wd-eva02-large-tagger-v3",
        *,
        local_files_only: bool = False,
    ):
        """Ensure the model is loaded."""
        config = self._refresh_load_control_config()
        needs_load = (
            self._model is None
            or self._current_model_name != model_name
            or self._load_control_signature != config.runtime_signature()
        )
        if needs_load:
            with self._lock:
                config = self._refresh_load_control_config()
                if (
                    self._model is None
                    or self._current_model_name != model_name
                    or self._load_control_signature != config.runtime_signature()
                ):
                    self._load_model(model_name, local_files_only=local_files_only)
    
    def _prepare_image(self, image: Image.Image) -> np.ndarray:
        """
        Preprocess a single image for the model.
        Optimized version with minimal allocations.
        """
        width, height = image.size
        
        # Handle transparency
        if image.mode == 'RGBA':
            # Create white background and composite
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            image = background
        elif image.mode != 'RGB':
            image = image.convert("RGB")
        
        # Pad to square
        max_dim = max(width, height)
        if width != height:
            pad_left = (max_dim - width) // 2
            pad_top = (max_dim - height) // 2
            padded_image = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
            padded_image.paste(image, (pad_left, pad_top))
            image = padded_image
        
        # Resize to target size
        if image.size[0] != self._target_size:
            image = image.resize(
                (self._target_size, self._target_size), 
                Image.BICUBIC
            )
        
        # Convert to numpy array in BGR format (model expects BGR)
        # Using np.asarray is faster than np.array as it doesn't copy if not needed
        image_array = np.asarray(image, dtype=np.float32)
        
        # RGB to BGR conversion using fancy indexing
        image_array = image_array[:, :, ::-1].copy()
        
        return image_array
    
    def _prepare_image_from_path(self, file_path: str) -> Tuple[str, Optional[np.ndarray]]:
        """Load and prepare an image from file path."""
        try:
            ext = Path(file_path).suffix.lower()
            
            if ext == '.gif':
                image = self._extract_gif_frame(file_path)
            elif ext in ['.mp4', '.webm', '.mov', '.avi', '.mkv']:
                image = self._extract_video_frame(file_path)
            else:
                image = Image.open(file_path)
            
            prepared = self._prepare_image(image)
            
            # Close image to free memory
            if hasattr(image, 'close'):
                image.close()
            
            return (file_path, prepared)
        except Exception as e:
            return (file_path, None)
    
    def _prepare_images_parallel(
        self, 
        file_paths: List[str],
        max_workers: Optional[int] = None
    ) -> List[Tuple[str, Optional[np.ndarray]]]:
        """Prepare multiple images in parallel."""
        if max_workers is None:
            max_workers = self._num_preprocess_workers
        
        results = []
        
        # Submit all tasks
        futures = {
            self._preprocess_executor.submit(self._prepare_image_from_path, fp): fp 
            for fp in file_paths
        }
        
        # Collect results in submission order
        path_to_result = {}
        for future in as_completed(futures):
            file_path, prepared = future.result()
            path_to_result[file_path] = prepared
        
        # Return in original order
        return [(fp, path_to_result.get(fp)) for fp in file_paths]
    
    def _extract_tags_from_scores(
        self,
        scores: np.ndarray,
        general_threshold: float,
        character_threshold: float,
        hide_rating_tags: bool,
        character_tags_first: bool
    ) -> List[Dict[str, Any]]:
        """Extract tags from model output scores using vectorized operations."""
        results = []
        names = self._tag_data['names']
        
        # Character tags - vectorized threshold check
        char_indices = self._tag_data['character']
        char_scores = scores[char_indices]
        char_mask = char_scores >= character_threshold
        
        for idx, score in zip(char_indices[char_mask], char_scores[char_mask]):
            results.append({
                'name': names[idx],
                'category': 'character',
                'confidence': float(score)
            })
        
        # General tags
        gen_indices = self._tag_data['general']
        gen_scores = scores[gen_indices]
        gen_mask = gen_scores >= general_threshold
        
        for idx, score in zip(gen_indices[gen_mask], gen_scores[gen_mask]):
            results.append({
                'name': names[idx],
                'category': 'general',
                'confidence': float(score)
            })
        
        # Rating tags
        if not hide_rating_tags:
            rating_indices = self._tag_data['rating']
            rating_scores = scores[rating_indices]
            rating_mask = rating_scores > 0.5
            
            for idx, score in zip(rating_indices[rating_mask], rating_scores[rating_mask]):
                results.append({
                    'name': names[idx],
                    'category': 'rating',
                    'confidence': float(score)
                })
        
        # Sort results
        if character_tags_first:
            # Group by category then sort by confidence
            char_tags = sorted(
                [r for r in results if r['category'] == 'character'],
                key=lambda x: x['confidence'], 
                reverse=True
            )
            general_tags = sorted(
                [r for r in results if r['category'] == 'general'],
                key=lambda x: x['confidence'], 
                reverse=True
            )
            rating_tags = sorted(
                [r for r in results if r['category'] == 'rating'],
                key=lambda x: x['confidence'], 
                reverse=True
            )
            results = char_tags + general_tags + rating_tags
        else:
            results.sort(key=lambda x: x['confidence'], reverse=True)
        
        return results
    
    def predict(
        self,
        image: Image.Image,
        general_threshold: float = 0.35,
        character_threshold: float = 0.85,
        hide_rating_tags: bool = True,
        character_tags_first: bool = True,
        model_name: str = "wd-eva02-large-tagger-v3"
    ) -> List[Dict[str, Any]]:
        """Predict tags for a single image."""
        self.ensure_loaded(model_name)
        
        processed_image = self._prepare_image(image)
        processed_batch = np.expand_dims(processed_image, axis=0)
        
        with self._inference_lock:
            preds = self._model.run(None, {self._input_name: processed_batch})[0]
        
        scores = preds[0]  # First (and only) batch item
        
        return self._extract_tags_from_scores(
            scores, general_threshold, character_threshold,
            hide_rating_tags, character_tags_first
        )
    
    def predict_batch(
        self,
        images: List[np.ndarray],
        general_threshold: float = 0.35,
        character_threshold: float = 0.85,
        hide_rating_tags: bool = True,
        character_tags_first: bool = True,
        model_name: str = "wd-eva02-large-tagger-v3"
    ) -> List[List[Dict[str, Any]]]:
        """
        Predict tags for a batch of preprocessed images.
        
        Args:
            images: List of preprocessed image arrays (from _prepare_image)
        """
        if not images:
            return []
        
        self.ensure_loaded(model_name)
        
        # Stack into batch
        batch = np.stack(images, axis=0)
        
        # Run inference
        with self._inference_lock:
            preds = self._model.run(None, {self._input_name: batch})[0]
        
        # Extract tags for each image
        results = []
        for scores in preds:
            tags = self._extract_tags_from_scores(
                scores, general_threshold, character_threshold,
                hide_rating_tags, character_tags_first
            )
            results.append(tags)
        
        return results
    
    def predict_from_file(
        self,
        file_path: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Predict tags from a single file path."""
        model_name = kwargs.get('model_name', 'wd-eva02-large-tagger-v3')
        local_files_only = bool(kwargs.get('local_files_only', False))
        self.ensure_loaded(model_name, local_files_only=local_files_only)

        _, prepared = self._prepare_image_from_path(file_path)
        
        if prepared is None:
            return []
        
        batch = np.expand_dims(prepared, axis=0)
        
        with self._inference_lock:
            preds = self._model.run(None, {self._input_name: batch})[0]
        
        return self._extract_tags_from_scores(
            preds[0],
            kwargs.get('general_threshold', 0.35),
            kwargs.get('character_threshold', 0.85),
            kwargs.get('hide_rating_tags', True),
            kwargs.get('character_tags_first', True)
        )
    
    def predict_from_files_batch(
        self,
        file_paths: List[str],
        general_threshold: float = 0.35,
        character_threshold: float = 0.85,
        hide_rating_tags: bool = True,
        character_tags_first: bool = True,
        model_name: str = "wd-eva02-large-tagger-v3",
        batch_size: Optional[int] = None,
        progress_callback: Optional[callable] = None
    ) -> List[Tuple[str, List[Dict[str, Any]]]]:
        """
        Predict tags for multiple files efficiently using batch processing.
        
        Args:
            file_paths: List of file paths to process
            progress_callback: Optional callback(processed, total) for progress updates
            
        Returns:
            List of (file_path, tags) tuples in the same order as input
        """
        if not file_paths:
            return []
        
        self.ensure_loaded(model_name)
        
        if batch_size is None:
            batch_size = self.get_optimal_batch_size(model_name)
        
        total = len(file_paths)
        results = {}
        processed_count = 0
        
        # Process in batches
        for i in range(0, total, batch_size):
            batch_paths = file_paths[i:i + batch_size]
            
            # Parallel preprocessing
            prepared = self._prepare_images_parallel(batch_paths)
            
            # Filter out failed preparations
            valid_items = [(fp, img) for fp, img in prepared if img is not None]
            failed_paths = [fp for fp, img in prepared if img is None]
            
            # Mark failed as empty
            for fp in failed_paths:
                results[fp] = []
            
            if valid_items:
                # Stack valid images
                batch_images = np.stack([img for _, img in valid_items], axis=0)
                
                # Run inference
                with self._inference_lock:
                    preds = self._model.run(None, {self._input_name: batch_images})[0]
                
                # Extract tags
                for (fp, _), scores in zip(valid_items, preds):
                    tags = self._extract_tags_from_scores(
                        scores, general_threshold, character_threshold,
                        hide_rating_tags, character_tags_first
                    )
                    results[fp] = tags
            
            processed_count += len(batch_paths)
            
            if progress_callback:
                progress_callback(processed_count, total)
        
        # Return in original order
        return [(fp, results.get(fp, [])) for fp in file_paths]
    
    def predict_from_files_streaming(
        self,
        file_paths: List[str],
        general_threshold: float = 0.35,
        character_threshold: float = 0.85,
        hide_rating_tags: bool = True,
        character_tags_first: bool = True,
        model_name: str = "wd-eva02-large-tagger-v3",
        batch_size: Optional[int] = None
    ) -> Generator[Tuple[str, List[Dict[str, Any]]], None, None]:
        """
        Stream prediction results as they complete.
        
        Yields (file_path, tags) tuples as each batch completes.
        """
        if not file_paths:
            return
        
        self.ensure_loaded(model_name)
        
        if batch_size is None:
            batch_size = self.get_optimal_batch_size(model_name)
        
        for i in range(0, len(file_paths), batch_size):
            batch_paths = file_paths[i:i + batch_size]
            
            # Parallel preprocessing
            prepared = self._prepare_images_parallel(batch_paths)
            
            # Separate valid and failed
            valid_items = [(fp, img) for fp, img in prepared if img is not None]
            failed_paths = [fp for fp, img in prepared if img is None]
            
            # Yield failed immediately
            for fp in failed_paths:
                yield (fp, [])
            
            if valid_items:
                batch_images = np.stack([img for _, img in valid_items], axis=0)
                
                with self._inference_lock:
                    preds = self._model.run(None, {self._input_name: batch_images})[0]
                
                for (fp, _), scores in zip(valid_items, preds):
                    tags = self._extract_tags_from_scores(
                        scores, general_threshold, character_threshold,
                        hide_rating_tags, character_tags_first
                    )
                    yield (fp, tags)
    
    def _extract_gif_frame(self, file_path: str, frame_index: int = 0) -> Image.Image:
        """Extract a frame from a GIF."""
        with Image.open(file_path) as gif:
            gif.seek(frame_index)
            return gif.convert('RGB')
    
    def _extract_video_frame(self, file_path: str, frame_index: int = 0) -> Image.Image:
        """Extract a frame from a video file using ffmpeg."""
        import subprocess
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            cmd = [
                'ffmpeg', '-i', file_path,
                '-vf', f'select=eq(n\\,{frame_index})',
                '-vframes', '1',
                '-y', '-loglevel', 'error',
                tmp_path
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
            
            image = Image.open(tmp_path).convert('RGB')
            return image
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    
    @property
    def is_loaded(self) -> bool:
        return self._model is not None
    
    @property
    def current_model(self) -> Optional[str]:
        return self._current_model_name

    @property
    def current_model_repo(self) -> Optional[str]:
        return self._current_model_repo
    
    def get_optimal_batch_size(self, model_name: Optional[str] = None) -> int:
        """Get the configured bounded batch size for the model."""
        name = model_name or self._current_model_name or "wd-eva02-large-tagger-v3"
        model_cap = self.OPTIMAL_BATCH_SIZES.get(name, 4)
        configured = self._load_control_config.batch_size
        return max(1, min(configured, model_cap))

    def get_batch_size_provenance(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        name = model_name or self._current_model_name or settings.AI_MODEL_NAME
        model_cap = self.OPTIMAL_BATCH_SIZES.get(name, 4)
        effective = self.get_optimal_batch_size(name)
        cap_source = self._load_control_config.batch_cap_source
        if effective == model_cap and model_cap < self._load_control_config.effective_batch_size:
            cap_source = "model_optimal_batch_size"
        return {
            "configured_batch_size": self._load_control_config.configured_batch_size,
            "load_control_effective_batch_size": self._load_control_config.effective_batch_size,
            "effective_batch_size": effective,
            "batch_cap_source": cap_source,
            "batch_max_items": self._load_control_config.batch_max_items,
            "phase_max_batch_size": 16,
            "model_optimal_batch_size": model_cap,
        }

    def get_provider_provenance(self) -> Dict[str, Any]:
        """Return public-safe ONNX provider selection provenance."""
        return self._provider_provenance.to_public_dict()

    def get_load_control_config(self) -> Dict[str, Any]:
        """Return public-safe load-control settings currently applied."""
        payload = self._load_control_config.to_public_dict()
        payload["applied_session_options"] = dict(self._session_options_summary)
        return payload

    def get_runtime_provenance(
        self,
        *,
        model_name: Optional[str] = None,
        thresholds: Optional[Dict[str, float]] = None,
        batch_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return model/provider/backend provenance for AI tagging summaries."""
        name = model_name or self._current_model_name or settings.AI_MODEL_NAME
        batch_provenance = self.get_batch_size_provenance(name)
        return {
            "model_name": name,
            "model_repo_id": self.AVAILABLE_MODELS.get(name),
            "thresholds": thresholds or {},
            "provider": self.get_provider_provenance(),
            "load_control": self.get_load_control_config(),
            "configured_batch_size": batch_provenance["configured_batch_size"],
            "effective_batch_size": batch_provenance["effective_batch_size"],
            "batch_size": batch_provenance["effective_batch_size"],
            "batch": batch_provenance,
            "tagger_version_source": "SmilingWolf WD Tagger v3 ONNX via onnxruntime",
            "backend": "onnxruntime",
        }
    
    def shutdown(self):
        """Clean up resources."""
        if hasattr(self, '_preprocess_executor') and self._preprocess_executor:
            self._preprocess_executor.shutdown(wait=False)

# Global singleton instance
_tagger_instance: Optional[WDTagger] = None
_tagger_lock = threading.Lock()

def get_wd_tagger() -> WDTagger:
    """Get the singleton WD Tagger instance."""
    global _tagger_instance
    if _tagger_instance is None:
        with _tagger_lock:
            if _tagger_instance is None:
                _tagger_instance = WDTagger()
    return _tagger_instance
