from __future__ import annotations

import pytest

from .conftest import REPO_ROOT

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

APP = str(REPO_ROOT / "app" / "streamlit_app.py")


def _button(at: AppTest, label: str):  # type: ignore[no-untyped-def]
    return next(b for b in at.button if b.label == label)


def test_gui_write_preview_and_dry_run_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    at.sidebar.radio[0].set_value("WRITE").run()
    at.text_area[0].set_value("Hello from the GUI").run()
    _button(at, "Lay out text").click().run()
    assert not at.exception
    assert [m.label for m in at.metric][:2] == ["Paths", "Pen-down"]
    _button(at, "Send").click().run()
    assert not at.exception
    assert any("dry run" in s.value for s in at.success)


def test_gui_real_send_needs_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value("WRITE").run()
    at.text_area[0].set_value("Hi").run()
    _button(at, "Lay out text").click().run()
    dry = next(c for c in at.checkbox if c.label.startswith("Dry run"))
    dry.uncheck().run()
    assert _button(at, "Send").disabled  # not until "I am next to the machine" is ticked
