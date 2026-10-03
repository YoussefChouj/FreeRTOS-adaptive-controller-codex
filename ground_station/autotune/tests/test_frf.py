"""frf.py on synthetic flights with a known plant (synth.simulate)."""

import numpy as np
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
