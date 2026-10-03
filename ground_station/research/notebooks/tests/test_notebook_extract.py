import json

from ground_station.research.notebooks.extract import MAX_OUT_LINES, extract, extract_file

NB = {
    "nbformat": 4,
    "cells": [
        {"cell_type": "markdown", "source": ["# Title\n", "theta_dot = -Gamma x e"]},
        {"cell_type": "code", "source": ["x = 1\n", "print(x)"],
         "outputs": [{"output_type": "stream", "text": [f"line{i}\n" for i in range(30)]}]},
        {"cell_type": "code", "source": "plt.plot(x)",
         "outputs": [{"output_type": "display_data",
                      "data": {"image/png": "iVBORw0KGgo" * 1000, "text/plain": ["<Figure>"]}}]},
        {"cell_type": "code", "source": "1/0",
         "outputs": [{"output_type": "error", "ename": "ZeroDivisionError",
                      "evalue": "division by zero", "traceback": ["\x1b[0;31mjunk"]}]},
    ],
}


def test_notebook_extract_layout():
    txt = extract(NB, "demo.ipynb")
    assert "# %% [cell 0] markdown" in txt and "# theta_dot = -Gamma x e" in txt
    assert "# %% [cell 1] code\nx = 1\nprint(x)\n" in txt
    assert "# >> line0" in txt and f"# >> line{MAX_OUT_LINES - 1}" in txt
    assert f"# >> line{MAX_OUT_LINES}" not in txt
    assert "[... 10 more output lines truncated]" in txt
    assert "iVBOR" not in txt and "# >> [image dropped]" in txt and "# >> <Figure>" in txt
    assert "# >> ZeroDivisionError: division by zero" in txt and "junk" not in txt


def test_notebook_extract_file_compiles(tmp_path):
    nb = tmp_path / "demo.ipynb"
    nb.write_text(json.dumps(NB), encoding="utf-8")
    out = tmp_path / "out" / "demo.py"
    extract_file(nb, out)
    compile(out.read_text(encoding="utf-8"), str(out), "exec")
