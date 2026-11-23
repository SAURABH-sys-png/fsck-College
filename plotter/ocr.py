"""Text recognition: Claude vision (primary) with Tesseract as the offline fallback.

This is the only module that talks to the network (CLAUDE.md §4). The model name and the API
key are read from the environment variables named in ``config.ai`` — never hardcoded, never
logged. Rate-limit, 5xx and connection errors are retried by the Anthropic SDK with
exponential backoff (``ai.max_retries``); each request times out after ``ai.timeout_s``.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Protocol

import anthropic
import cv2
import numpy as np
import numpy.typing as npt

from plotter.config import Config
from plotter.errors import OcrError

logger = logging.getLogger(__name__)

PROMPT = (
    "Transcribe all text exactly as written. Preserve line breaks and paragraph breaks. "
    "Output plain text only, no commentary."
)
ENGINES = ("auto", "ai", "tesseract")
_MAX_TOKENS = 16000
_JPEG_QUALITY = 90

TESSERACT_HINT = (
    "Install Tesseract (Ubuntu: sudo apt install tesseract-ocr · macOS: brew install tesseract "
    "· Windows: https://github.com/UB-Mannheim/tesseract/wiki) or set ANTHROPIC_API_KEY to use "
    "AI OCR."
)


class _Messages(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class MessagesClient(Protocol):
    """The subset of ``anthropic.Anthropic`` used here (lets tests inject a fake)."""

    @property
    def messages(self) -> _Messages:
        """The Messages API resource."""
        ...


def ai_available(cfg: Config) -> bool:
    """Return True if AI OCR is enabled and the API key variable is set."""
    return cfg.ai.enabled and bool(os.environ.get(cfg.ai.key_env))


def encode_image(image: npt.NDArray[np.uint8], max_px: int) -> tuple[str, str]:
    """Downscale (long side ≤ ``max_px``) and JPEG-encode an image for the API.

    Returns:
        ``(media_type, base64_data)``.

    Raises:
        OcrError: If encoding fails.
    """
    h, w = image.shape[:2]
    scale = min(1.0, max_px / max(h, w))
    if scale < 1.0:
        image = np.asarray(
            cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA),
            dtype=np.uint8,
        )
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise OcrError("could not encode the image for OCR")
    return "image/jpeg", base64.standard_b64encode(buf.tobytes()).decode("ascii")


def read_text_ai(
    image: npt.NDArray[np.uint8], cfg: Config, client: MessagesClient | None = None
) -> str:
    """Transcribe ``image`` with Claude vision.

    Args:
        image: BGR or grayscale page image.
        cfg: Configuration (``ai`` section).
        client: Optional pre-built client (tests inject a fake). By default an
            ``anthropic.Anthropic`` client is created from the key in ``ai.key_env``.

    Returns:
        The transcription (plain text).

    Raises:
        OcrError: Missing key/model, API failure, or a refusal.
    """
    model = os.environ.get(cfg.ai.model_env, "").strip()
    if not model:
        raise OcrError(
            f"set {cfg.ai.model_env} in your environment or .env to the Claude model to use "
            "(e.g. claude-opus-5-5)"
        )
    api: Any = client
    if api is None:
        key = os.environ.get(cfg.ai.key_env, "")
        if not key:
            raise OcrError(f"{cfg.ai.key_env} is not set; add it to .env or use Tesseract")
        api = anthropic.Anthropic(
            api_key=key, max_retries=cfg.ai.max_retries, timeout=cfg.ai.timeout_s
        )
    media_type, data = encode_image(image, cfg.ai.max_image_px)
    logger.info("sending page to %s for OCR (%d KiB)", model, len(data) * 3 // 4 // 1024)
    try:
        resp = api.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": data},
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        )
    except anthropic.AuthenticationError as exc:
        raise OcrError(f"the API key in {cfg.ai.key_env} was rejected; check it") from exc
    except anthropic.NotFoundError as exc:
        raise OcrError(f"model {model!r} not found; check {cfg.ai.model_env}") from exc
    except anthropic.RateLimitError as exc:
        raise OcrError("rate limited by the API after retries; try again later") from exc
    except anthropic.APIStatusError as exc:
        raise OcrError(f"the API returned HTTP {exc.status_code}; try again later") from exc
    except anthropic.APIConnectionError as exc:
        raise OcrError("cannot reach the API (network or timeout)") from exc

    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        raise OcrError("the model declined to transcribe this image")
    if stop == "max_tokens":
        logger.warning("OCR output hit max_tokens; the transcription may be truncated")
    parts = [b.text for b in getattr(resp, "content", []) if getattr(b, "type", "") == "text"]
    return "".join(parts).strip("\n")


def clean_for_tesseract(image: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Grayscale + Otsu binarisation (dark text on white)."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return np.asarray(bw, dtype=np.uint8)


def read_text_tesseract(image: npt.NDArray[np.uint8]) -> str:
    """Transcribe ``image`` with Tesseract (offline).

    Raises:
        OcrError: If Tesseract is not installed or fails.
    """
    import pytesseract

    try:
        text = pytesseract.image_to_string(clean_for_tesseract(image))
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(f"Tesseract is not installed. {TESSERACT_HINT}") from exc
    except pytesseract.TesseractError as exc:
        raise OcrError(f"Tesseract failed: {exc}") from exc
    return str(text).strip("\n")


def read_text(
    image: npt.NDArray[np.uint8],
    cfg: Config,
    engine: str = "auto",
    client: MessagesClient | None = None,
) -> str:
    """Read the text in a page image.

    Args:
        image: BGR or grayscale image (ideally flattened with :mod:`plotter.perspective`).
        cfg: Configuration.
        engine: ``auto`` (Claude if ``ai.enabled`` and the key is set, otherwise Tesseract;
            falls back to Tesseract if the AI call fails), ``ai`` or ``tesseract``.
        client: Optional injected API client (tests).

    Returns:
        The recognised text with line breaks preserved.

    Raises:
        OcrError: If the chosen engine fails (or both fail in ``auto``).
        ValueError: For an unknown engine name.
    """
    if engine not in ENGINES:
        raise ValueError(f"unknown OCR engine {engine!r}; use auto, ai or tesseract")
    if engine == "ai":
        return read_text_ai(image, cfg, client)
    if engine == "tesseract":
        return read_text_tesseract(image)
    if ai_available(cfg) or client is not None:
        try:
            return read_text_ai(image, cfg, client)
        except OcrError as exc:
            logger.warning("AI OCR failed (%s); falling back to Tesseract", exc)
    else:
        logger.info("%s not set or AI disabled; using Tesseract", cfg.ai.key_env)
    return read_text_tesseract(image)
