from __future__ import annotations

import base64
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import anthropic
import cv2
import httpx2
import numpy as np
import pytest

from plotter import ocr
from plotter.config import Config
from plotter.errors import OcrError


class FakeMessages:
    def __init__(
        self, text: str = "Hello\nWorld", stop: str = "end_turn", exc: Exception | None = None
    ):
        self.text, self.stop, self.exc = text, stop, exc
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(
            stop_reason=self.stop,
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=self.text),
            ],
        )


class FakeClient:
    def __init__(self, **kw: Any) -> None:
        self.messages = FakeMessages(**kw)


@pytest.fixture
def img() -> np.ndarray:
    page = np.full((300, 200, 3), 255, np.uint8)
    cv2.putText(page, "Hi", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    return page


@pytest.fixture
def with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("AI_MODEL", "test-model")


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=req), body=None)


def test_ai_request_shape(cfg: Config, img: np.ndarray, with_env: None) -> None:
    client = FakeClient()
    text = ocr.read_text(img, cfg, "ai", client=client)
    assert text == "Hello\nWorld"
    call = client.messages.calls[0]
    assert call["model"] == "test-model"  # from the env var, never hardcoded
    blocks = call["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    base64.b64decode(blocks[0]["source"]["data"], validate=True)
    assert blocks[1]["text"] == ocr.PROMPT


def test_ai_requires_model(cfg: Config, img: np.ndarray, no_env: None) -> None:
    with pytest.raises(OcrError, match="AI_MODEL"):
        ocr.read_text(img, cfg, "ai", client=FakeClient())


def test_ai_requires_key_without_client(
    cfg: Config, img: np.ndarray, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AI_MODEL", "m")
    with pytest.raises(OcrError, match="ANTHROPIC_API_KEY"):
        ocr.read_text_ai(img, cfg)


def test_ai_builds_client_from_env(
    cfg: Config, img: np.ndarray, with_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def factory(**kw: Any) -> FakeClient:
        seen.update(kw)
        return FakeClient(text="ok")

    monkeypatch.setattr(ocr.anthropic, "Anthropic", factory)
    assert ocr.read_text(img, cfg, "auto") == "ok"
    assert seen["max_retries"] == cfg.ai.max_retries
    assert seen["timeout"] == cfg.ai.timeout_s


def test_falls_back_to_tesseract_without_key(
    cfg: Config, img: np.ndarray, no_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_string", lambda im: "from tesseract\n")
    assert ocr.read_text(img, cfg) == "from tesseract"


def test_falls_back_when_ai_disabled(
    cfg: Config, img: np.ndarray, with_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_string", lambda im: "tess")
    c = replace(cfg, ai=replace(cfg.ai, enabled=False))
    assert ocr.read_text(img, c) == "tess"


@pytest.mark.parametrize(
    ("exc", "match"),
    [
        (_status_error(anthropic.AuthenticationError, 401), "rejected"),
        (_status_error(anthropic.NotFoundError, 404), "not found"),
        (_status_error(anthropic.RateLimitError, 429), "rate limited"),
        (_status_error(anthropic.InternalServerError, 500), "HTTP 500"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "reach"),
    ],
)
def test_ai_errors_become_ocr_errors(
    cfg: Config, img: np.ndarray, with_env: None, exc: Exception, match: str
) -> None:
    with pytest.raises(OcrError, match=match):
        ocr.read_text(img, cfg, "ai", client=FakeClient(exc=exc))


def test_auto_falls_back_on_api_error(
    cfg: Config, img: np.ndarray, with_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_string", lambda im: "fallback")
    client = FakeClient(exc=_status_error(anthropic.InternalServerError, 503))
    assert ocr.read_text(img, cfg, "auto", client=client) == "fallback"


def test_refusal_and_truncation(
    cfg: Config, img: np.ndarray, with_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(OcrError, match="declined"):
        ocr.read_text(img, cfg, "ai", client=FakeClient(stop="refusal"))
    assert ocr.read_text(img, cfg, "ai", client=FakeClient(stop="max_tokens")) == "Hello\nWorld"
    assert "truncated" in caplog.text


def test_api_key_never_logged(
    cfg: Config, img: np.ndarray, with_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    ocr.read_text(img, cfg, "ai", client=FakeClient())
    assert "sk-test-not-real" not in caplog.text
    assert len(caplog.text) < 2000  # no image payload in logs


def test_tesseract_missing(cfg: Config, img: np.ndarray, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytesseract

    def boom(im: Any) -> str:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_string", boom)
    with pytest.raises(OcrError, match="apt install tesseract-ocr"):
        ocr.read_text(img, cfg, "tesseract")

    def fail(im: Any) -> str:
        raise pytesseract.TesseractError(1, "bad")

    monkeypatch.setattr(pytesseract, "image_to_string", fail)
    with pytest.raises(OcrError, match="failed"):
        ocr.read_text(img, cfg, "tesseract")


def test_encode_image_downscales() -> None:
    big = np.zeros((4000, 3000, 3), np.uint8)
    media, data = ocr.encode_image(big, 1000)
    assert media == "image/jpeg"
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == 1000


def test_clean_for_tesseract_binary(img: np.ndarray) -> None:
    bw = ocr.clean_for_tesseract(img)
    assert set(np.unique(bw)) <= {0, 255}


def test_unknown_engine(cfg: Config, img: np.ndarray) -> None:
    with pytest.raises(ValueError, match="unknown OCR engine"):
        ocr.read_text(img, cfg, "eyes")
