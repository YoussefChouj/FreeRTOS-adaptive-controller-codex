import numpy as np
import pytest


def test_get_has_find(make_log):
    t = np.arange(0, 1, 0.1)
    log = make_log({"a.Theta[0]": (t, t), "a.Theta[1]": (t, t), "b": (t, t)},
                   {"a.Theta[0]": 10, "a.Theta[1]": 10, "b": 10})
    assert log.has("b") and not log.has("c")
    with pytest.raises(KeyError):
        log.get("c")
    assert log.find("a.Theta[[]*]") == ["a.Theta[0]", "a.Theta[1]"]
    assert log.find("a.Theta[0]") == []            # "[0]" is a character class (documented)


def test_window_half_open(make_log):
    t = np.arange(10) / 10.0
    log = make_log({"x": (t, t)}, {"x": 10})
    w = log.window("x", 0.2, 0.5)
    assert np.allclose(w.t, [0.2, 0.3, 0.4])
    assert w.rate_hz == 10 and w.slot == 0


def test_aligned_grid_and_nan_outside_span(make_log):
    ta = np.arange(200) / 100.0
    tb = 1.0 + np.arange(20) / 10.0
    log = make_log({"a": (ta, 2 * ta), "b": (tb, tb)}, {"a": 100, "b": 10})
    grid, out = log.aligned(["a", "b"])
    assert np.allclose(np.diff(grid), 0.1)                     # min nominal rate
    assert grid[0] == 0.0 and grid[-1] == pytest.approx(2.9)   # last sample included
    assert np.isnan(out["a"][grid > ta[-1]]).all()
    assert np.isnan(out["b"][grid < 1.0]).all()
    inside = grid <= ta[-1]
    assert np.allclose(out["a"][inside], 2 * grid[inside])


def test_aligned_explicit_rate_and_window(make_log):
    t = np.arange(1000) / 100.0
    log = make_log({"a": (t, t)}, {"a": 100})
    grid, out = log.aligned(["a"], rate_hz=50, t0=2.0, t1=4.0)
    assert grid.size == 100 and grid[0] == 2.0 and grid[-1] < 4.0
    assert np.allclose(out["a"], grid)


def test_aligned_errors_and_empty(make_log):
    t = np.arange(10) / 10.0
    log = make_log({"a": (t, t), "e": (np.array([]), np.array([]))}, {"a": 10, "e": 10})
    with pytest.raises(ValueError):
        log.aligned([])
    with pytest.raises(ValueError):
        log.aligned(["a"], rate_hz=0)
    grid, out = log.aligned(["a", "e"])
    assert out["e"].size == grid.size and np.isnan(out["e"]).all()


def test_hover_fixture_shape(hover_log):
    assert hover_log.has("mrac_state.roll.Theta[1]")
    assert hover_log.get("real_voltage").rate_hz == 10
    assert [s.rate_hz for s in hover_log.slots] == [100, 25, 10]
