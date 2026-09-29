import pytest

from ground_station.analysis.flightlab import registry as R


def rec(**kw):
    base = dict(id="X-1", severity="warn", category="pid", target=None, action="investigate", factor=None,
                evidence={"a": 1}, rationale="r", confidence="low")
    base.update(kw)
    return R.Recommendation(**base)


@pytest.mark.parametrize("field,bad", [("severity", "high"), ("category", "x"),
                                       ("action", "tune"), ("confidence", "sure")])
def test_recommendation_rejects_bad_enums(field, bad):
    with pytest.raises(ValueError):
        rec(**{field: bad})


def test_recommendation_evidence_must_be_dict():
    with pytest.raises(ValueError):
        rec(evidence=[1])
    assert rec().to_dict()["evidence"] == {"a": 1}


def test_sort_recommendations():
    rs = [rec(id="B", severity="info"), rec(id="Z", severity="critical"), rec(id="A", severity="info")]
    assert [r.id for r in R.sort_recommendations(rs)] == ["Z", "A", "B"]


def test_get_path():
    d = {"a": {"b": {"c": 0}}, "n": None}
    assert R.get_path(d, "a.b.c") == 0
    assert R.get_path(d, "a.x") is None and R.get_path(d, "n.x") is None


def test_register_plugin_validates_and_rejects_duplicates(monkeypatch):
    monkeypatch.setattr(R, "PLUGINS", {})

    @R.register_plugin
    class P:
        name = "tmp_p"
        def requires(self, log, cfg): return []
        def run(self, log, segs, cfg): return {}
        def figures(self, log, segs, cfg, out_dir): return []

    assert isinstance(R.PLUGINS["tmp_p"], P)
    assert R.plugin_order(R.PLUGINS["tmp_p"]) == (100, "tmp_p")

    with pytest.raises(ValueError):
        @R.register_plugin
        class Q(P):
            pass

    with pytest.raises(ValueError):
        @R.register_plugin
        class NoRun:
            name = "norun"
            def requires(self, log, cfg): return []
            def figures(self, log, segs, cfg, out_dir): return []


def test_register_rule(monkeypatch):
    monkeypatch.setattr(R, "RULES", {})

    @R.register_rule(requires=["loops"])
    def TMP_RULE(metrics, cfg, ctx):
        return []

    assert R.RULES["TMP_RULE"].requires == ["loops"]
    assert R.RULES["TMP_RULE"].fn is TMP_RULE
