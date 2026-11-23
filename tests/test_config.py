from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from plotter.config import Config, config_from_dict, load_config
from plotter.errors import ConfigError

from .conftest import REPO_ROOT


def test_shipped_config_loads(cfg: Config) -> None:
    assert cfg.machine.bed_size_mm == (230.0, 320.0)
    assert cfg.machine.page_size_mm == (210.0, 297.0)
    assert cfg.machine.margins_mm.left == 20.0
    assert cfg.motion.draw_feed_mm_min == 3000.0
    assert cfg.pen.up_cmd == "M3 S30"
    assert cfg.vision.hatch.angles_deg == (45.0, -45.0, 0.0, 90.0)
    assert cfg.text.jitter.seed == 42
    assert cfg.ai.model_env == "AI_MODEL"


def test_defaults_match_shipped_file(cfg: Config) -> None:
    # The dataclass defaults mirror machine.yaml so a partial file behaves predictably.
    assert config_from_dict({}) == cfg


def test_printable_area_and_home(cfg: Config) -> None:
    assert cfg.printable_area == (20.0, 15.0, 200.0, 282.0)
    assert cfg.home_in_page_mm == (-10.0, 307.0)


def test_partial_override_keeps_other_defaults() -> None:
    cfg = config_from_dict({"motion": {"draw_feed_mm_min": 1500}})
    assert cfg.motion.draw_feed_mm_min == 1500.0
    assert cfg.motion.travel_feed_mm_min == 5000.0


def test_camera_source_accepts_index_or_url() -> None:
    assert config_from_dict({"camera": {"source": 2}}).camera.source == 2
    url = "http://192.168.1.5:4747/video"
    assert config_from_dict({"camera": {"source": url}}).camera.source == url
    with pytest.raises(ConfigError, match="camera"):
        config_from_dict({"camera": {"source": [1]}})


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"machine": {"bogus": 1}}, "unknown key"),
        ({"machine": {"baud": "fast"}}, "integer"),
        ({"motion": {"draw_feed_mm_min": "x"}}, "number"),
        ({"motion": {"draw_feed_mm_min": 0}}, "draw_feed"),
        ({"machine": {"page_origin_mm": [30, 10]}}, "does not fit"),
        ({"machine": {"page_origin_mm": [1, 2, 3]}}, "exactly 2"),
        ({"machine": {"margins_mm": {"left": 150, "right": 100}}}, "printable width"),
        ({"vision": {"mode": "magic"}}, "vision.mode"),
        ({"vision": {"adaptive_block": 30}}, "odd"),
        ({"paths": {"optimize": "fastest"}}, "paths.optimize"),
        ({"pen": {"up_cmd": "M3 S90"}}, "differ"),
        ({"pen": {"down_cmd": "$1=255"}}, r"\$ setting"),
        ({"text": {"first_line_y_mm": 16}}, "top margin"),
        ({"ai": {"enabled": "yes"}}, "true or false"),
        ({"motion": {"coord_decimals": 9}}, "0..4"),
        ({"vision": {"hatch": {"angles_deg": ["a"]}}}, "only numbers"),
        ({"machine": []}, "mapping"),
    ],
)
def test_invalid_values_raise(data: dict[str, object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        config_from_dict(data)  # type: ignore[arg-type]


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_load_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("machine: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(p)


def test_load_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(p)


def test_error_mentions_file(tmp_path: Path) -> None:
    data = yaml.safe_load((REPO_ROOT / "config" / "machine.yaml").read_text(encoding="utf-8"))
    data["motion"]["accel_mm_s2"] = -1
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"m\.yaml.*accel"):
        load_config(p)


def test_config_is_immutable(cfg: Config) -> None:
    with pytest.raises(AttributeError):
        cfg.motion.draw_feed_mm_min = 1  # type: ignore[misc]
