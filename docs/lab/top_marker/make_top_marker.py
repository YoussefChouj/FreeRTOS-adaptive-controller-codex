"""Printable top-of-drone marker: ArUco DICT_4X4_50 id 1, 80 mm black square in a 90 mm white label, A4 true scale.

Vector cells (no image scaling), so it prints crisp at any dpi. Run: python make_top_marker.py -> top_marker_id1.pdf/.png
"""
import cv2, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

LABEL, BLACK, ID = 90.0, 80.0, 1
bits = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), ID, 6) < 128


def marker(ax, x0, y0):
    """Label with its lower-left corner at (x0, y0) mm; marker row 0 (ArUco corners 0-1) is the top edge = drone nose."""
    ax.add_patch(Rectangle((x0, y0), LABEL, LABEL, fill=False, ls=(0, (3, 2)), lw=0.4, ec="0.5"))   # cut line
    m, c = (LABEL - BLACK) / 2, BLACK / 6
    for r, k in zip(*np.nonzero(bits)):
        ax.add_patch(Rectangle((x0 + m + k * c, y0 + m + (5 - r) * c), c, c, fc="k", ec="k", lw=0.05))
    ax.annotate("", (x0 + LABEL / 2, y0 + LABEL + 9), (x0 + LABEL / 2, y0 + LABEL + 2),
                arrowprops=dict(arrowstyle="-|>", lw=1.2, color="k"))
    ax.text(x0 + LABEL / 2 + 3, y0 + LABEL + 4, "FRONT (drone nose)", fontsize=8, va="center")


fig = plt.figure(figsize=(210 / 25.4, 297 / 25.4))
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 210); ax.set_ylim(0, 297); ax.axis("off")
marker(ax, 60, 175); marker(ax, 60, 50)
ax.text(105, 285, f"ArUco DICT_4X4_50, id {ID}  |  black square {BLACK:.0f} mm, cut along the dashed {LABEL:.0f} mm line",
        ha="center", fontsize=8)
ax.text(105, 279, "Print at 100% / Actual size (no 'fit to page'). Matte paper, no laminate gloss.", ha="center", fontsize=8)
ax.plot([20, 70], [25, 25], "k", lw=1.2); [ax.plot([x, x], [23, 27], "k", lw=1.2) for x in (20, 70)]
ax.text(45, 18, "50 mm: check with a ruler", ha="center", fontsize=7)
ax.text(105, 10, "Second copy is a spare. Mount flat on top, centred, arrow to the nose.", ha="center", fontsize=7)
fig.savefig("top_marker_id1.pdf"); fig.savefig("top_marker_id1.png", dpi=300)
