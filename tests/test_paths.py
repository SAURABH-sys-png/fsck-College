from __future__ import annotations

import numpy as np
import pytest

from plotter import paths as P


def _seg(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y1]], dtype=np.float64)


def test_path_length_and_stats() -> None:
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], dtype=np.float64)
    assert P.path_length(sq) == pytest.approx(40.0)
    st = P.stats([sq, _seg(20, 0, 30, 0)], start=(0, 0))
    assert st["paths"] == 2
    assert st["pen_down_mm"] == pytest.approx(50.0)
    assert st["pen_up_mm"] == pytest.approx(20.0)  # (0,0)->(0,0) + (0,0)->(20,0)
    assert P.stats([])["pen_up_mm"] == 0.0


def test_fit_to_area_keeps_aspect_and_centres() -> None:
    d = [_seg(0, 0, 100, 50)]
    out = P.fit_to_area(d, (10, 10, 60, 110))
    b = P.bounds(out)
    assert b is not None
    xmin, ymin, xmax, ymax = b
    assert xmax - xmin == pytest.approx(50.0)
    assert ymax - ymin == pytest.approx(25.0)
    assert (xmin + xmax) / 2 == pytest.approx(35.0)
    assert (ymin + ymax) / 2 == pytest.approx(60.0)
    assert d[0][1, 0] == 100  # input untouched


def test_fit_to_area_stretch_and_degenerate() -> None:
    out = P.fit_to_area([_seg(0, 0, 10, 20)], (0, 0, 100, 100), keep_aspect=False)
    assert P.bounds(out) == pytest.approx((0, 0, 100, 100))
    # vertical line: zero width must not divide by zero
    out = P.fit_to_area([_seg(5, 0, 5, 10)], (0, 0, 100, 100))
    assert P.bounds(out) == pytest.approx((50, 0, 50, 100))
    assert P.fit_to_area([], (0, 0, 1, 1)) == []
    with pytest.raises(ValueError, match="empty"):
        P.fit_to_area([_seg(0, 0, 1, 1)], (0, 0, 0, 10))


def test_simplify_rdp() -> None:
    x = np.linspace(0, 10, 101)
    line = np.column_stack([x, np.zeros_like(x)])
    assert len(P.simplify([line], 0.01)[0]) == 2
    corner = np.array([[0, 0], [5, 0.01], [10, 0], [10, 10]], dtype=np.float64)
    out = P.simplify([corner], 0.05)[0]
    assert out.tolist() == [[0, 0], [10, 0], [10, 10]]
    assert len(P.simplify([line], 0)[0]) == 101
    # closed loop keeps its shape
    t = np.linspace(0, 2 * np.pi, 200)
    circle = np.column_stack([np.cos(t), np.sin(t)]) * 10
    simp = P.simplify([circle], 0.1)[0]
    assert 8 < len(simp) < 60
    assert np.allclose(simp[0], simp[-1])


def test_dedupe_and_filter_short() -> None:
    p = np.array([[0, 0], [0.05, 0], [0.1, 0], [1, 0], [1.05, 0]], dtype=np.float64)
    out = P.dedupe_points([p], 0.2)[0]
    assert out[0].tolist() == [0, 0]
    assert out[-1].tolist() == [1.05, 0]
    assert len(out) < len(p)
    d = [_seg(0, 0, 0.1, 0), _seg(0, 0, 5, 0), np.array([[1.0, 1.0]])]
    kept = P.filter_short(d, 0.2)
    assert len(kept) == 1
    assert P.path_length(kept[0]) == 5


def test_join_paths_with_reversal() -> None:
    a = _seg(0, 0, 10, 0)
    b = _seg(20, 0, 10.1, 0)  # reversed, small gap
    c = _seg(20, 0, 20, 10)
    far = _seg(50, 50, 60, 60)
    out = P.join_paths([a, b, c, far], tol=0.2)
    assert len(out) == 2
    longest = max(out, key=P.path_length)
    assert P.path_length(longest) == pytest.approx(30.0, abs=0.01)  # 10 + 0.1 gap + 9.9 + 10
    # exact join drops the duplicate point
    out = P.join_paths([_seg(0, 0, 1, 0), _seg(1, 0, 2, 0)], tol=0.0)
    assert len(out) == 1
    assert len(out[0]) == 3


def test_join_grows_head_too() -> None:
    a = _seg(10, 0, 20, 0)
    b = _seg(0, 0, 10, 0)  # attaches to a's head
    out = P.join_paths([a, b], 0.1)
    assert len(out) == 1
    assert P.path_length(out[0]) == pytest.approx(20)


def _shuffled_grid(seed: int = 1) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    segs = [_seg(x, y, x + 3, y) for x in range(0, 100, 5) for y in range(0, 100, 5)]
    order = rng.permutation(len(segs))
    return [segs[i] if rng.random() < 0.5 else segs[i][::-1] for i in order]


@pytest.mark.parametrize("method", ["greedy", "greedy+2opt"])
def test_order_paths_reduces_travel(method: str) -> None:
    d = _shuffled_grid()
    before = P.stats(d)["pen_up_mm"]
    out = P.order_paths(d, method)
    after = P.stats(out)["pen_up_mm"]
    assert after < before * 0.25
    assert len(out) == len(d)
    # same set of strokes (possibly reversed)
    key = sorted(tuple(sorted(map(tuple, p.round(6).tolist()))) for p in out)
    ref = sorted(tuple(sorted(map(tuple, p.round(6).tolist()))) for p in d)
    assert key == ref


def test_two_opt_improves_on_greedy() -> None:
    rng = np.random.default_rng(3)
    d = [rng.uniform(0, 200, (2, 2)) for _ in range(300)]
    g = P.stats(P.order_paths(d, "greedy"))["pen_up_mm"]
    o = P.stats(P.order_paths(d, "greedy+2opt"))["pen_up_mm"]
    assert o < g * 0.95


def test_order_paths_none_and_errors() -> None:
    d = _shuffled_grid()
    out = P.order_paths(d, "none")
    assert all(np.array_equal(a, b) for a, b in zip(out, d, strict=True))
    with pytest.raises(ValueError, match="unknown"):
        P.order_paths(d, "magic")


def test_order_paths_large_skips_two_opt() -> None:
    d = _shuffled_grid()
    out = P.order_paths(d, "greedy+2opt", two_opt_max_paths=10)
    assert P.stats(out)["pen_up_mm"] < P.stats(d)["pen_up_mm"]


def test_order_never_worse_than_input() -> None:
    # Already optimal input: ordering must not make it worse.
    d = [_seg(i, 0, i + 0.5, 0) for i in range(10)]
    before = P.stats(d)["pen_up_mm"]
    assert P.stats(P.order_paths(d, "greedy"))["pen_up_mm"] <= before + 1e-9
