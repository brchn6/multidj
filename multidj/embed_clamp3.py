"""CLaMP 3 embedding backend for MultiDJ.

Two-stage pipeline:
  1. MERT (m-a-p/MERT-v1-95M) — audio → per-chunk 768-dim feature vectors.
  2. CLaMP 3 audio encoder — sequence of MERT vectors → single 768-dim embedding.

The CLaMP 3 model code lives in the git submodule at ``vendor/clamp3/code/``.
Run ``git submodule update --init`` if that directory is empty.

Requires: ``uv sync --extra clamp3``
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
_CLAMP3_CODE = _REPO_ROOT / "vendor" / "clamp3" / "code"

# Model constants
CLAMP3_MODEL_NAME = "clamp3_saas"
_CLAMP3_HF_REPO = "sander-wood/clamp3"
_CLAMP3_CHECKPOINT = (
    "weights_clamp3_saas"
    "_h_size_768"
    "_t_model_FacebookAI_xlm-roberta-base"
    "_t_length_128"
    "_a_size_768"
    "_a_layers_12"
    "_a_length_128"
    "_s_size_768"
    "_s_layers_12"
    "_p_size_64"
    "_p_length_512.pth"
)

_MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
_MERT_SR = 24_000
_MERT_WINDOW_SECS = 5          # one MERT feature per 5-second chunk
_CLAMP3_MAX_AUDIO_LEN = 128    # CLaMP3 max sequence length (chunks)
_CLAMP3_HIDDEN = 768


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def _ensure_submodule() -> None:
    """Raise a helpful error if the submodule hasn't been checked out."""
    if not (_CLAMP3_CODE / "utils.py").exists():
        raise RuntimeError(
            "CLaMP 3 submodule not found. Run:\n\n"
            "    git submodule update --init vendor/clamp3\n"
        )


def _add_clamp3_to_path() -> None:
    code_str = str(_CLAMP3_CODE)
    if code_str not in sys.path:
        sys.path.insert(0, code_str)


def _check_clamp3_deps() -> None:
    missing = []
    for pkg in ("torch", "transformers", "librosa", "soundfile", "requests"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise RuntimeError(
            f"Missing packages for CLaMP 3: {missing}. Install with:\n\n"
            "    uv sync --extra clamp3\n"
        )


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_clamp3_model() -> tuple[Any, Any, Any, str]:
    """Load MERT + CLaMP 3 audio encoder.

    Returns (mert_model, mert_processor, clamp3_model, device).
    """
    _ensure_submodule()
    _check_clamp3_deps()
    _add_clamp3_to_path()

    import torch
    from transformers import AutoModel, Wav2Vec2FeatureExtractor, BertConfig

    # Import CLaMP3 model classes from the vendored submodule
    from utils import CLaMP3Model  # type: ignore  # noqa: PLC0415

    device = (
        "cuda" if torch.cuda.is_available()
        else "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
        else "cpu"
    )

    # --- Stage 1: MERT ---
    _progress(f"Loading MERT ({_MERT_MODEL_NAME}) on {device}…")
    mert_model = AutoModel.from_pretrained(_MERT_MODEL_NAME, trust_remote_code=True)
    mert_processor = Wav2Vec2FeatureExtractor.from_pretrained(
        _MERT_MODEL_NAME, trust_remote_code=True
    )
    mert_model = mert_model.to(device)
    mert_model.eval()

    # --- Stage 2: CLaMP3 audio encoder ---
    _progress(f"Loading CLaMP 3 ({CLAMP3_MODEL_NAME})…")
    audio_config = BertConfig(
        vocab_size=1,
        hidden_size=768,
        num_hidden_layers=12,
        num_attention_heads=12,          # 768 // 64
        intermediate_size=768 * 4,
        max_position_embeddings=128,
    )
    symbolic_config = BertConfig(
        vocab_size=1,
        hidden_size=768,
        num_hidden_layers=12,
        num_attention_heads=12,
        intermediate_size=768 * 4,
        max_position_embeddings=512,
    )
    clamp3_model = CLaMP3Model(
        audio_config=audio_config,
        symbolic_config=symbolic_config,
        text_model_name="FacebookAI/xlm-roberta-base",
        hidden_size=768,
        load_m3=False,  # we don't need M3 weights for audio inference
    )

    # Download checkpoint via HuggingFace hub (cached after first run)
    ckpt_path = _download_clamp3_checkpoint()
    _progress(f"Loading checkpoint from {ckpt_path}…")
    checkpoint = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    clamp3_model.load_state_dict(checkpoint["model"])
    _progress(
        f"CLaMP 3 checkpoint: epoch {checkpoint['epoch']}, "
        f"loss {checkpoint['min_eval_loss']:.4f}"
    )
    clamp3_model = clamp3_model.to(device)
    clamp3_model.eval()

    return mert_model, mert_processor, clamp3_model, device


def _download_clamp3_checkpoint() -> str:
    """Download CLaMP 3 SAAS weights via huggingface_hub (caches in ~/.cache/huggingface)."""
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        return hf_hub_download(repo_id=_CLAMP3_HF_REPO, filename=_CLAMP3_CHECKPOINT)
    except ImportError:
        pass

    # Fallback: manual download with requests
    import os
    import requests  # type: ignore

    cache_dir = Path.home() / ".cache" / "clamp3"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / _CLAMP3_CHECKPOINT
    if dest.exists():
        return str(dest)

    url = (
        "https://huggingface.co/sander-wood/clamp3/resolve/main/"
        + _CLAMP3_CHECKPOINT
    )
    _progress(f"Downloading CLaMP 3 weights from {url}…")
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
    return str(dest)


# ---------------------------------------------------------------------------
# MERT feature extraction
# ---------------------------------------------------------------------------

def _extract_mert_features(
    filepath: str,
    mert_model: Any,
    mert_processor: Any,
    device: str,
) -> np.ndarray:
    """Extract per-chunk MERT features from an audio file.

    Returns array of shape (n_chunks, 768).  Each chunk is 5 seconds.
    """
    import torch
    import librosa  # type: ignore

    y, _ = librosa.load(filepath, sr=_MERT_SR, mono=True)
    window = _MERT_SR * _MERT_WINDOW_SECS

    # Split into non-overlapping 5-second chunks
    chunks: list[np.ndarray] = []
    start = 0
    while start < len(y):
        chunk = y[start : start + window]
        if len(chunk) < window:
            chunk = np.pad(chunk, (0, window - len(chunk)))
        chunks.append(chunk)
        start += window

    features: list[np.ndarray] = []
    for chunk in chunks:
        inputs = mert_processor(
            chunk.tolist(), sampling_rate=_MERT_SR, return_tensors="pt"
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = mert_model(**inputs, output_hidden_states=True)
        # Use the last hidden state, mean-pool over time → (768,)
        feat = outputs.last_hidden_state.mean(dim=1).squeeze(0).cpu().numpy()
        features.append(feat)

    return np.stack(features)  # (n_chunks, 768)


# ---------------------------------------------------------------------------
# CLaMP3 audio encoding
# ---------------------------------------------------------------------------

def _encode_mert_features_with_clamp3(
    mert_features: np.ndarray,
    clamp3_model: Any,
    device: str,
) -> np.ndarray:
    """Run CLaMP 3 audio encoder on pre-extracted MERT features.

    Implements the same segmentation + weighted-average logic as
    ``vendor/clamp3/code/extract_clamp3.py``.

    Args:
        mert_features: shape (n_chunks, 768)
        clamp3_model: loaded CLaMP3Model
        device: torch device string

    Returns:
        768-dim numpy float32 embedding
    """
    import torch

    # Add BOS/EOS zero sentinel vectors (as done by extract_clamp3.py)
    n, d = mert_features.shape
    zero = np.zeros((1, d), dtype=np.float32)
    input_data = np.concatenate([zero, mert_features, zero], axis=0)  # (n+2, 768)
    input_tensor = torch.tensor(input_data, dtype=torch.float32)      # (n+2, 768)

    max_len = _CLAMP3_MAX_AUDIO_LEN
    total_len = len(input_tensor)

    # Build segments of length max_len (last segment is last max_len tokens)
    segment_list: list[torch.Tensor] = []
    for i in range(0, total_len, max_len):
        segment_list.append(input_tensor[i : i + max_len])
    if len(segment_list) > 1:
        # Replace last segment with the true tail (may overlap with prev)
        segment_list[-1] = input_tensor[-max_len:]

    weighted_features: list[tuple[torch.Tensor, int]] = []
    for seg in segment_list:
        real_len = len(seg)
        # Pad to max_len
        if real_len < max_len:
            pad = torch.zeros(max_len - real_len, d, dtype=torch.float32)
            seg_padded = torch.cat([seg, pad], dim=0)
        else:
            seg_padded = seg
        mask = torch.zeros(max_len, dtype=torch.float32)
        mask[:real_len] = 1.0

        seg_in = seg_padded.unsqueeze(0).to(device)   # (1, max_len, 768)
        mask_in = mask.unsqueeze(0).to(device)         # (1, max_len)

        with torch.no_grad():
            feat = clamp3_model.get_audio_features(
                audio_inputs=seg_in,
                audio_masks=mask_in,
                get_global=True,
            )  # (1, 768)

        weighted_features.append((feat.squeeze(0), real_len))

    # Weighted average by segment real_len
    total_weight = sum(w for _, w in weighted_features)
    embedding = sum(f * w for f, w in weighted_features) / total_weight  # (768,)
    return embedding.cpu().numpy().astype(np.float32)


def encode_audio_clamp3(
    filepath: str,
    mert_model: Any,
    mert_processor: Any,
    clamp3_model: Any,
    device: str,
) -> np.ndarray:
    """Full CLAMP3 pipeline: audio file → 768-dim embedding."""
    mert_feats = _extract_mert_features(filepath, mert_model, mert_processor, device)
    return _encode_mert_features_with_clamp3(mert_feats, clamp3_model, device)
