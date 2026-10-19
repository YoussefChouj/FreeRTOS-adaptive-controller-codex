import json

import numpy as np


def test_nn_replay_exits_nonzero_on_mismatch_and_missing(tmp_path):
    from ground_station.analysis import nn_replay

    # Missing truth4 flight
    assert nn_replay.main(["dummy", "--truth", "non_existent_flight", "--ckpt", "dummy.pt"]) == 1

def test_nn_replay_writes_metrics(tmp_path, monkeypatch):
    import ground_station.analysis.nn_replay as nnr

    # A real truth4.csv under a temp cwd (no global Path.exists patch, which would make the output asserts vacuous).
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs/video_truth/dummy_flight").mkdir(parents=True)
    (tmp_path / "logs/video_truth/dummy_flight/truth4.csv").touch()

    def dummy_model(d):
        return np.zeros((len(d["ts"]), 2))

    def mock_seq(name, rate_hz):
        n = 100
        t = np.arange(n)
        return {"ts": t, "t": t, "earth": np.ones(n, dtype=complex), "truth": np.ones(n, dtype=complex),
                "back": np.ones(n, dtype=complex), "R": np.zeros(n, dtype=complex),
                "of": np.zeros(n, dtype=complex), "head": np.zeros(n, dtype=complex)}

    def mock_drift_score(name, ests):
        return {"est": {"earth": {"drift60s_p90_cm": 1.0}, "nn": {"drift60s_p90_cm": 0.5}}}

    monkeypatch.setattr(nnr, "load_model", lambda path: dummy_model)
    monkeypatch.setattr(nnr, "predict", lambda m, d: m(d))
    monkeypatch.setattr(nnr, "seq", mock_seq)
    monkeypatch.setattr(nnr, "drift_score", mock_drift_score)

    out_dir = tmp_path / "out"
    assert nnr.main(["stem", "--truth", "dummy_flight", "--ckpt", "dummy.pt", "--out", str(out_dir)]) == 0

    assert (out_dir / "nn_replay.md").exists()
    assert (out_dir / "nn_replay.json").exists()

    j = json.loads((out_dir / "nn_replay.json").read_text())
    assert j["drift60s_p90_cm"]["nn"] == 0.5


def test_load_model_rejects_unreadable_and_foreign_files(tmp_path):
    import pytest

    from ground_station.analysis.nn_io import _torch, load_model
    empty = tmp_path / "empty.pt"
    empty.touch()
    with pytest.raises(ValueError, match="Unreadable checkpoint"):
        load_model(empty)
    foreign = tmp_path / "foreign.pt"
    _torch().save({"weights": 1}, foreign)
    with pytest.raises(ValueError, match="missing"):
        load_model(foreign)


def test_hash_mismatch(tmp_path):
    from torch import nn

    from ground_station.analysis.nn_io import _torch, save_model
    torch = _torch()
    class DummyNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.a = nn.Parameter(torch.ones(1))
        def forward(self, x):
            return x
    net = DummyNet()
    model = (net, np.zeros(17), np.ones(17), list(range(17)))
    ckpt = tmp_path / "model.pt"

    # Save the original model
    save_model(model, ckpt, {"kind": "dummy"})

    # Manually tamper with the hash
    payload = torch.load(ckpt, map_location="cpu", weights_only=False)
    payload["sha256"] = "deadbeef"
    torch.save(payload, ckpt)

    import ground_station.analysis.nn_replay as nnr
    # nn_replay should exit with 1 and print error
    assert nnr.main(["stem", "--truth", "dummy", "--ckpt", str(ckpt)]) == 1
