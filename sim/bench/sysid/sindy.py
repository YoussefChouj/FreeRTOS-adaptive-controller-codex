"""NumPy-only SINDy / SINDYc library, STLSQ, cross-validation, and bootstrap ensemble.

Implements:
  - Library matrix builder for polynomial and control-augmented features (SINDYc)
  - STLSQ (Sequentially Thresholded Least Squares) with ridge regularization option
  - 5-fold CV threshold selection
  - 50-bootstrap ensemble returning inclusion probabilities and median coefficients
"""
import numpy as np


def build_poly_library(X, feature_names=None, U=None, u_names=None, poly_order=2, include_bias=True):
    """Build polynomial regressor library matrix Theta from states X and control inputs U.

    Parameters:
        X: ndarray of shape (N, D) representing states
        feature_names: list of D names for states
        U: optional ndarray of shape (N, M) or (N,) representing control inputs
        u_names: list of M names for control inputs
        poly_order: max polynomial order (1 or 2)
        include_bias: whether to include constant term '1'

    Returns:
        Theta: ndarray of shape (N, K)
        names: list of K strings naming each feature column
    """
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    N, D = X.shape

    if feature_names is None:
        feature_names = [f'x{i+1}' for i in range(D)]

    vars_list = [X[:, i] for i in range(D)]
    var_names = list(feature_names)

    if U is not None:
        U = np.asarray(U, dtype=float)
        if U.ndim == 1:
            U = U[:, None]
        M = U.shape[1]
        if u_names is None:
            u_names = [f'u{i+1}' if M > 1 else 'u' for i in range(M)]
        for i in range(M):
            vars_list.append(U[:, i])
            var_names.append(u_names[i])

    cols = []
    names = []

    if include_bias:
        cols.append(np.ones((N, 1)))
        names.append('1')

    # First-order linear terms
    for v, name in zip(vars_list, var_names):
        cols.append(v[:, None])
        names.append(name)

    # Second-order interaction and squared terms
    if poly_order >= 2:
        K = len(vars_list)
        for i in range(K):
            for j in range(i, K):
                cols.append((vars_list[i] * vars_list[j])[:, None])
                if i == j:
                    names.append(f'{var_names[i]}^2')
                else:
                    names.append(f'{var_names[i]}*{var_names[j]}')

    Theta = np.hstack(cols)
    return Theta, names


def stlsq(Theta, Y, threshold=0.1, alpha=1e-5, max_iter=25, standardize=False, G=None, B=None, scales=None):
    """Sequentially Thresholded Least Squares with ridge regularization.

    Solves argmin_w ||Y - Theta w||^2 + alpha ||w||^2 subject to sparsity via thresholding.

    Parameters:
        Theta: (N, K) regressor matrix
        Y: (N,) or (N, D) target vector/matrix
        threshold: absolute coefficient cutoff below which weights are zeroed
        alpha: ridge regularization parameter (L2 penalty)
        max_iter: maximum thresholding iterations
        standardize: if True, scale columns to unit std, threshold standardized coefficients,
                     and return de-standardized coefficients
        G: optional precomputed Gram matrix (K, K)
        B: optional precomputed projection matrix (K, D)
        scales: optional precomputed column scales (K,)

    Returns:
        w: (K,) or (K, D) sparse coefficient array
    """
    Theta = np.asarray(Theta, dtype=float)
    Y = np.asarray(Y, dtype=float)
    N, K = Theta.shape

    is_1d = (Y.ndim == 1)
    if is_1d:
        Y_proc = Y[:, None]
    else:
        Y_proc = Y
    D = Y_proc.shape[1]

    if scales is None:
        if standardize:
            std = np.std(Theta, axis=0)
            scales = np.where(std < 1e-12, 1.0, std)
        else:
            scales = np.ones(K)

    if G is None or B is None:
        if standardize:
            Theta_scaled = Theta / scales
        else:
            Theta_scaled = Theta
        G = Theta_scaled.T @ Theta_scaled
        B = Theta_scaled.T @ Y_proc

    w_out = np.zeros((K, D))

    for d in range(D):
        yd = Y_proc[:, d]
        bd = B[:, d]
        active = np.ones(K, dtype=bool)
        w = np.zeros(K)

        for _ in range(max_iter):
            idx = np.where(active)[0]
            if len(idx) == 0:
                w.fill(0.0)
                break

            G_sub = G[np.ix_(idx, idx)]
            b_sub = bd[idx]
            reg = alpha * np.eye(len(idx))
            try:
                coef = np.linalg.solve(G_sub + reg, b_sub)
            except np.linalg.LinAlgError:
                T_sub = (Theta[:, idx] / scales[idx]) if standardize else Theta[:, idx]
                coef, *_ = np.linalg.lstsq(T_sub, yd, rcond=None)

            w.fill(0.0)
            w[idx] = coef

            new_active = np.abs(w) >= threshold
            if np.array_equal(active, new_active):
                break
            active = new_active

        w_destd = w / scales
        w_out[:, d] = w_destd

    return w_out[:, 0] if is_1d else w_out


def cv_threshold(Theta, Y, thresholds=None, k_folds=5, alpha=1e-5, seed=42, groups=None, one_se=False, standardize=False):
    """Select optimal STLSQ threshold using k-fold cross-validation.

    Parameters:
        Theta: (N, K) regressor matrix
        Y: (N,) target vector
        thresholds: candidate thresholds list/array. If None, auto-generated.
        k_folds: number of CV folds (default 5)
        alpha: ridge penalty parameter
        seed: random seed for fold permutation
        groups: optional array of group identifiers for group k-fold CV
        one_se: if True, select largest candidate threshold within 1 SE of minimum CV error
        standardize: if True, evaluate STLSQ on standardized columns

    Returns:
        best_threshold: float
        cv_scores: dict mapping threshold -> mean validation MSE
    """
    Theta = np.asarray(Theta, dtype=float)
    Y = np.asarray(Y, dtype=float)
    N, K = Theta.shape

    if standardize:
        std_all = np.std(Theta, axis=0)
        scales_all = np.where(std_all < 1e-12, 1.0, std_all)
        Theta_init = Theta / scales_all
    else:
        scales_all = np.ones(K)
        Theta_init = Theta

    if thresholds is None:
        reg = alpha * np.eye(K)
        try:
            w_base = np.linalg.solve(Theta_init.T @ Theta_init + reg, Theta_init.T @ Y)
        except np.linalg.LinAlgError:
            w_base, *_ = np.linalg.lstsq(Theta_init, Y, rcond=None)
        max_coef = np.max(np.abs(w_base))
        if max_coef <= 1e-12:
            max_coef = 1.0
        thresholds = np.logspace(np.log10(max_coef * 1e-4), np.log10(max_coef * 1.2), 30)

    rng = np.random.default_rng(seed)
    val_and_train = []
    if groups is None:
        indices = np.arange(N)
        rng.shuffle(indices)
        folds = np.array_split(indices, k_folds)
        for i in range(len(folds)):
            val_idx = folds[i]
            train_idx = np.setdiff1d(indices, val_idx)
            val_and_train.append((train_idx, val_idx))
    else:
        groups = np.asarray(groups)
        unique_groups = np.unique(groups)
        n_groups = len(unique_groups)
        actual_k = min(k_folds, n_groups)
        perm_groups = unique_groups.copy()
        rng.shuffle(perm_groups)
        group_folds = np.array_split(perm_groups, actual_k)
        for g_fold in group_folds:
            val_idx = np.where(np.isin(groups, g_fold))[0]
            train_idx = np.where(~np.isin(groups, g_fold))[0]
            val_and_train.append((train_idx, val_idx))

    # Precompute fold Gram matrices and targets
    fold_data = []
    for train_idx, val_idx in val_and_train:
        T_tr = Theta[train_idx]
        Y_tr = Y[train_idx]
        if standardize:
            s_tr = np.std(T_tr, axis=0)
            s_tr = np.where(s_tr < 1e-12, 1.0, s_tr)
            T_tr_s = T_tr / s_tr
        else:
            s_tr = np.ones(K)
            T_tr_s = T_tr
        G_tr = T_tr_s.T @ T_tr_s
        B_tr = T_tr_s.T @ Y_tr[:, None]
        fold_data.append((train_idx, val_idx, G_tr, B_tr, s_tr))

    all_fold_errs = {float(th): [] for th in thresholds}
    for train_idx, val_idx, G_tr, B_tr, s_tr in fold_data:
        T_val = Theta[val_idx]
        Y_val = Y[val_idx]
        for th in thresholds:
            th_val = float(th)
            w = stlsq(Theta[train_idx], Y[train_idx], threshold=th_val, alpha=alpha,
                      standardize=standardize, G=G_tr, B=B_tr, scales=s_tr)
            pred = T_val @ w
            mse = float(np.mean((Y_val - pred) ** 2))
            all_fold_errs[th_val].append(mse)

    cv_scores = {}
    for th_val in all_fold_errs:
        cv_scores[th_val] = float(np.mean(all_fold_errs[th_val]))

    min_th = min(cv_scores, key=cv_scores.get)
    if not one_se:
        best_th = float(min_th)
    else:
        min_errs = all_fold_errs[min_th]
        min_se = float(np.std(min_errs, ddof=1) / np.sqrt(len(min_errs))) if len(min_errs) > 1 else 0.0
        cutoff = cv_scores[min_th] + min_se
        eligible = [th for th in cv_scores if cv_scores[th] <= cutoff]
        best_th = float(max(eligible))

    return best_th, cv_scores


def bootstrap_ensemble(Theta, Y, threshold, alpha=1e-5, n_boot=50, seed=42, groups=None, standardize=False):
    """Run bootstrap ensemble of STLSQ fits to compute inclusion probabilities and median coefficients.

    Parameters:
        Theta: (N, K) regressor matrix
        Y: (N,) target vector
        threshold: STLSQ threshold
        alpha: ridge penalty parameter
        n_boot: number of bootstrap resamples (default 50)
        seed: random seed for bootstrap
        groups: optional array of group identifiers for whole-group resampling
        standardize: if True, evaluate STLSQ on standardized columns

    Returns:
        p_incl: (K,) inclusion probability per feature in [0, 1]
        median_coef: (K,) median coefficient across active fits (0 if never active)
        coefs: (n_boot, K) array of all bootstrap coefficients
    """
    Theta = np.asarray(Theta, dtype=float)
    Y = np.asarray(Y, dtype=float)
    N, K = Theta.shape

    rng = np.random.default_rng(seed)
    coefs = np.zeros((n_boot, K))

    if groups is None:
        for b in range(n_boot):
            boot_idx = rng.choice(N, size=N, replace=True)
            coefs[b] = stlsq(Theta[boot_idx], Y[boot_idx], threshold=threshold, alpha=alpha, standardize=standardize)
    else:
        groups = np.asarray(groups)
        unique_groups = np.unique(groups)
        n_groups = len(unique_groups)
        group_to_indices = {g: np.where(groups == g)[0] for g in unique_groups}
        for b in range(n_boot):
            sampled_groups = rng.choice(unique_groups, size=n_groups, replace=True)
            boot_idx = np.concatenate([group_to_indices[g] for g in sampled_groups])
            coefs[b] = stlsq(Theta[boot_idx], Y[boot_idx], threshold=threshold, alpha=alpha, standardize=standardize)

    # Inclusion probability: fraction of bootstrap fits where coefficient is non-zero
    p_incl = np.mean(np.abs(coefs) > 1e-9, axis=0)

    median_coef = np.zeros(K)
    for j in range(K):
        active_vals = coefs[:, j][np.abs(coefs[:, j]) > 1e-9]
        if len(active_vals) > 0:
            median_coef[j] = float(np.median(active_vals))
        else:
            median_coef[j] = 0.0

    return p_incl, median_coef, coefs


class SINDYc:
    """SINDYc estimator with automated threshold tuning and bootstrap ensembling."""

    def __init__(self, threshold=None, alpha=1e-5, n_boot=50, k_folds=5, p_inclusion_cutoff=0.6,
                 groups=None, one_se=False, standardize=False):
        self.threshold = threshold
        self.alpha = alpha
        self.n_boot = n_boot
        self.k_folds = k_folds
        self.p_inclusion_cutoff = p_inclusion_cutoff
        self.groups = groups
        self.one_se = one_se
        self.standardize = standardize
        self.selected_mask = None
        self.p_incl = None
        self.coef = None
        self.feature_names = None

    def fit(self, Theta, Y, feature_names=None, thresholds=None, groups=None):
        """Fit model with CV threshold selection (if threshold not given) and bootstrap ensemble."""
        Theta = np.asarray(Theta, dtype=float)
        Y = np.asarray(Y, dtype=float)
        if feature_names is not None:
            self.feature_names = list(feature_names)
        else:
            self.feature_names = [f'feat_{i}' for i in range(Theta.shape[1])]

        grp = groups if groups is not None else self.groups

        if self.threshold is None:
            self.threshold, _ = cv_threshold(
                Theta, Y, thresholds=thresholds, k_folds=self.k_folds, alpha=self.alpha,
                groups=grp, one_se=self.one_se, standardize=self.standardize
            )

        self.p_incl, self.coef, _ = bootstrap_ensemble(
            Theta, Y, threshold=self.threshold, alpha=self.alpha, n_boot=self.n_boot,
            groups=grp, standardize=self.standardize
        )

        self.selected_mask = (self.p_incl >= self.p_inclusion_cutoff)
        # Features below cutoff are set to 0.0
        self.coef[~self.selected_mask] = 0.0
        return self

    def predict(self, Theta):
        """Predict target given regressor matrix Theta."""
        Theta = np.asarray(Theta, dtype=float)
        return Theta @ self.coef
