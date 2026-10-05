"""FROLS: forward regression with orthogonal least squares and the error reduction ratio (ERR).

Billings, Nonlinear System Identification: NARMAX Methods (Wiley 2013), ch. 3. Each step orthogonalises every
remaining candidate against the terms already chosen (modified Gram-Schmidt) and picks the one whose orthogonal
part explains the largest share of y'y. That share is the term's ERR; the ERRs of the chosen terms add up to
1 - |y - Theta c|^2 / |y|^2 of the least-squares fit on them. STLSQ (sindy.py) says which terms survive a
threshold; FROLS says in which order they matter and how much of the target each one explains.

ERR is relative to y'y, not to the variance: a bias column, when it is a candidate, gets the mean's share.
"""
import numpy as np


def frols(Theta, y, max_terms=None, esr_tol=1e-3, min_err=0.0, collinear_tol=1e-10):
    """Rank the columns of Theta (N, M) by ERR for one target y (N,).

    Stops when the unexplained share 1 - sum(ERR) is below esr_tol, when the best remaining ERR is below
    min_err, or after max_terms terms. A candidate whose part orthogonal to the chosen terms has a squared norm
    below collinear_tol times its own is skipped (it adds nothing new).

    Returns dict: order (column indices, as chosen), err (ERR of each, same order), coef (M,) least-squares
    coefficients on the chosen columns (zero elsewhere), esr (1 - sum(err)).
    """
    Theta = np.asarray(Theta, float)
    y = np.asarray(y, float).ravel()
    N, M = Theta.shape
    max_terms = M if max_terms is None else min(max_terms, M)
    yy = float(y @ y)
    if yy == 0.0:
        return dict(order=[], err=np.zeros(0), coef=np.zeros(M), esr=0.0)
    P = Theta.copy()                      # remaining candidates, orthogonalised in place as terms are chosen
    norm0 = np.einsum('ij,ij->j', Theta, Theta)
    alive = norm0 > 0
    order, err, W, g = [], [], [], []
    while len(order) < max_terms:
        ww = np.einsum('ij,ij->j', P, P)
        ok = alive & (ww > collinear_tol * norm0)
        if not ok.any():
            break
        wy = P.T @ y
        e = np.where(ok, wy ** 2 / np.where(ok, ww, 1.0) / yy, -1.0)
        # Ties (equal columns; BLAS rounding differs by column) go to the lowest index, so the order is reproducible.
        m = int(np.flatnonzero(e >= e.max() * (1 - 1e-12))[0])
        if e[m] < min_err:
            break
        w = P[:, m].copy()
        order.append(m); err.append(float(e[m])); W.append(w); g.append(wy[m] / ww[m])
        alive[m] = False
        P -= np.outer(w, (w @ P) / ww[m])  # modified Gram-Schmidt: remove the new direction from every candidate
        if 1.0 - sum(err) < esr_tol:
            break
    k = len(order)
    coef = np.zeros(M)
    if k:
        # Theta[:, order] = W A with A unit upper triangular; Theta c = W g, so A c_sel = g.
        A = np.eye(k)
        for j in range(k):
            for i in range(j + 1, k):
                A[j, i] = (W[j] @ Theta[:, order[i]]) / (W[j] @ W[j])
        c_sel = np.linalg.solve(A, np.array(g))
        coef[order] = c_sel
    return dict(order=order, err=np.array(err), coef=coef, esr=1.0 - float(sum(err)))


def err_table(result, names):
    """(name, ERR, coefficient) rows in the order chosen: the per-flight dominant-term report."""
    return [(names[m], float(e), float(result['coef'][m])) for m, e in zip(result['order'], result['err'])]
