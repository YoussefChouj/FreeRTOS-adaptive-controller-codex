"""frf.py on synthetic flights with a known plant (synth.simulate)."""

import numpy as np
import pandas as pd
import pytest

from ground_station.autotune import excitation as ex
from ground_station.autotune import frf as fr
from ground_station.autotune import synth
from ground_station.autotune.design import read_pid_rows

ROLL = read_pid_rows()["gyroxPID"]


@pytest.fixture(scope="module")
def roll_log(tmp_path_factory):
    return synth.write_session(tmp_path_factory.mktemp("roll"), synth.simulate("roll", seed=0))


def _ident(path, excite=ex.ID_EXCITE, axis="roll"):
    run = fr.extract(fr.load_series(path), axis, excite)
    f = fr.plant_frf([run], ROLL, (excite["f0"], excite["f1"]))
    return run, f


def test_rebuilt_dither_is_aligned_to_the_start(roll_log):
    t0, corr, *_ = fr.find_start(fr.load_series(roll_log), "roll", ex.ID_EXCITE)
    assert t0 == pytest.approx(9.0, abs=0.02) and corr > 0.9


def test_fit_recovers_the_known_plant(roll_log):
    run, f = _ident(roll_log)
    assert run.source.startswith("core streams") and run.fs == 100.0
    fit = fr.fit_plant(f)
    p = synth.DEMO_PLANT
    assert fit.plant.k == pytest.approx(p.k, rel=0.2)
    assert fit.plant.tau_s == pytest.approx(p.tau_s, rel=0.35)
    assert abs(fit.plant.delay_s - p.delay_s) < 0.006  # + half a 200 Hz sample of zero-order hold
    assert fr.quality_problem(f, fit) == ""


def test_id_frame_source_gives_the_same_plant(tmp_path):
    path = synth.write_session(tmp_path, synth.simulate("roll", seed=0, id_frame=True))
    run, f = _ident(path)
    assert run.source == "0x03 ID frame" and run.fs == 200.0
    assert fr.fit_plant(f).plant.k == pytest.approx(synth.DEMO_PLANT.k, rel=0.2)


def test_low_coherence_is_refused_with_a_reason(tmp_path):
    weak = {**ex.ID_EXCITE, "amp": 5.0}  # aligns (corr > 0.3) but drowns in gyro noise
    path = synth.write_session(tmp_path, synth.simulate("roll", excite=weak, gyro_noise_dps=8.0, seed=3))
    _, f = _ident(path, weak)
    assert f.coverage < fr.BAND_COVER_MIN
    fit = None
    try:
        fit = fr.fit_plant(f)
    except fr.IdError as exc:
        assert "coherent bins" in str(exc)
        return
    assert "coherence" in fr.quality_problem(f, fit)


def test_missing_excitation_is_an_error(tmp_path):
    path = synth.write_session(tmp_path, synth.simulate("roll", excite={**ex.ID_EXCITE, "amp": 0.0}, seed=4))
    with pytest.raises(fr.IdError, match="no multisine found"):
        fr.extract(fr.load_series(path), "roll", ex.ID_EXCITE)


def test_hover_rms_uses_the_pre_excite_hold(roll_log):
    s = fr.load_series(roll_log)
    pre = fr.hover_rate_rms(s, "roll", 9.0)
    whole = fr.hover_rate_rms(s, "roll", None)
    assert pre is not None and 1.0 < pre < whole  # the excitation adds tracking error


def test_wide_csv_is_accepted(tmp_path):
    t = np.arange(0, 1, 0.01)
    (tmp_path / "w.csv").write_text("t,a\n" + "\n".join(f"{x},{2 * x}" for x in t), encoding="utf-8")
    s = fr.load_series(tmp_path / "w.csv")
    assert np.allclose(s["a"][1], 2 * t)

def test_duplicate_timestamps_are_tolerated(tmp_path):
    df = synth.simulate("roll", seed=0)
    df2 = df.copy()
    df2["slot"] = df2["slot"] + 10
    df_dup = pd.concat([df, df2]).sort_values("received_ns", kind="stable")

    path = synth.write_session(tmp_path, df_dup)
    series = fr.load_series(path)

    t0, corr, grid, r, x, fs = fr.find_start(series, "roll", ex.ID_EXCITE)

    # Check if find_start returns same t0/fs as single-slot version
    path_single = synth.write_session(tmp_path / "single", df)
    series_single = fr.load_series(path_single)
    t0_s, corr_s, grid_s, r_s, x_s, fs_s = fr.find_start(series_single, "roll", ex.ID_EXCITE)

    assert abs(t0 - t0_s) <= 1.0/fs
    assert fs == fs_s


def test_design_band_judges_the_fit_where_the_margins_are_read():
    # 10-27 roll rate shape: model right from 1 to 12 Hz, +8 dB above 20 Hz -> full band refused, design band passes
    p = synth.DEMO_PLANT
    f = np.geomspace(0.5, 40.0, 60)
    g = p.frf(2 * np.pi * f) * np.where(f > 20.0, 10 ** (8 / 20), 1.0)
    frf = fr.Frf(f, g, np.full(len(f), 0.9), np.ones(len(f), bool), (0.5, 40.0), 1.0, 8)
    fit = fr.PlantFit(p, 0.45, len(f))
    assert "fit residual 0.45" in fr.quality_problem(frf, fit)
    assert fr.band_residual(frf, fit, (1.0, 12.0)) == (pytest.approx(0.0, abs=1e-9), 33)
    assert fr.quality_problem(frf, fit, design_band_hz=(1.0, 12.0)) == ""
    assert "design band" in fr.quality_problem(frf, fit, design_band_hz=(12.0, 40.0))
    assert "only 0 coherent bins" in fr.quality_problem(frf, fit, design_band_hz=(50.0, 60.0))
