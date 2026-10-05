"""demo_loads --stress-tags: the stress.make controllers run on the demo rows and the tables take the first tag as base."""
import demo_loads as dl


def test_stress_tag_runs_on_demo_rows(capsys):
    rowlist, ref, sp = dl.build({'arm250_m0': (0.25, 0)}, [2000], '1003')
    res = {t: dl.run_stress(t, rowlist, ref, sp) for t in ('pid_nom', 'h0g_nom')}
    assert all(len(m) == len(rowlist) for m in res.values())
    dl.tables(res, {'arm250_m0': (0.25, 0)}, 1, 'pid_nom')
    assert 'vs pid_nom' in capsys.readouterr().out
