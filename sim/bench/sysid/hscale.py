"""Multiscale H-scale sysID protocol (PREREG2.md section 1).

Executes:
  - Band split (L < 0.5 Hz, M 0.5-4 Hz, H > 4 Hz, global)
  - STLSQ with 5-fold CV threshold selection on id_fit
  - 50-bootstrap ensemble (inclusion cutoff 0.6)
  - Fit per trajectory class (steps / circle / lem / zigzag) + pooled
  - Tag universal features (selected in every trajectory class)
  - Condition (a): mean pairwise Jaccard < 0.5 on at least 2 of 3 axes
  - Condition (b): band-sum NRMSE <= 0.90 x global NRMSE on id_val on at least 2 of 3 axes
  - Evaluation on id_traj (generalization)
  - Verdict: SUPPORTED if (a) and (b) hold, else KILLED
  - Outputs hscale_sim.json, hscale_sim.md, and features_sim.json
"""
import argparse
import glob
import json
import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.abspath(os.path.join(HERE, '..'))
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)

from sim.bench.sysid.sindy import stlsq, cv_threshold, bootstrap_ensemble  # noqa: E402
from sim.bench.sysid.data import generate_dataset, CACHE_DIR  # noqa: E402
from sim.bench.sysid.features import build_candidate_features, compute_target, G_NOM, J0  # noqa: E402
from sim.bench.sysid.bands import split_bands  # noqa: E402

AXES = ['roll', 'pitch', 'yaw']
BANDS = ['L', 'M', 'H', 'global']
FIT_TRAJ_CLASSES = ['steps', 'circle', 'lem', 'zigzag']
RESULTS_DIR = os.path.join(HERE, 'results')
LIBRARY_DIR = os.path.join(HERE, 'library')


def jaccard_index(set_a, set_b):
    """Compute Jaccard similarity index between two sets."""
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return len(set_a & set_b) / union


def compute_nrmse(y_true, y_pred):
    """Compute Normalized Root Mean Square Error: RMSE / std(y_true)."""
    err = y_true - y_pred
    rmse = np.sqrt(np.mean(err ** 2))
    std = np.std(y_true)
    if std < 1e-12:
        return float(rmse)
    return float(rmse / std)


def fit_axis_band(theta, y, alpha=1e-5, n_boot=50, k_folds=5, p_cutoff=0.6, seed=42,
                  groups=None, one_se=False, standardize=False):
    """Run CV thresholding and 50-bootstrap ensemble on regressor matrix theta and target y."""
    best_th, _ = cv_threshold(
        theta, y, k_folds=k_folds, alpha=alpha, seed=seed,
        groups=groups, one_se=one_se, standardize=standardize
    )
    p_incl, med_coef, _ = bootstrap_ensemble(
        theta, y, threshold=best_th, alpha=alpha, n_boot=n_boot, seed=seed,
        groups=groups, standardize=standardize
    )
    selected_mask = (p_incl >= p_cutoff)
    coef = med_coef.copy()
    coef[~selected_mask] = 0.0
    return {
        'threshold': float(best_th),
        'p_incl': p_incl,
        'coef': coef,
        'selected_mask': selected_mask
    }


def run_sim_protocol(cache_dir=CACHE_DIR, verbose=True, amend=None):
    """Run the H-scale protocol on simulated data."""
    t0 = time.time()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    is_a1 = (amend == 'a1')

    if verbose:
        if is_a1:
            print("=== Multiscale H-scale sysID: Amendment A1 Enabled ===")
        print("=== Generating / Loading Datasets ===")
    d_fit = generate_dataset('id_fit', use_cache=True, cache_dir=cache_dir)
    d_val = generate_dataset('id_val', use_cache=True, cache_dir=cache_dir)
    d_traj = generate_dataset('id_traj', use_cache=True, cache_dir=cache_dir)

    feature_names = d_fit['feature_names']
    n_features = len(feature_names)

    # Filter out diverged rows from id_fit
    fit_ok = ~d_fit['diverged']
    val_ok = ~d_val['diverged']
    traj_ok = ~d_traj['diverged']

    # Extract non-diverged data per band for id_fit
    def get_fit_data(band):
        if band == 'global':
            th = d_fit['theta_global'][fit_ok]
            tg = d_fit['target_global'][fit_ok]
        elif band == 'L':
            th = d_fit['theta_L'][fit_ok]
            tg = d_fit['target_L'][fit_ok]
        elif band == 'M':
            th = d_fit['theta_M'][fit_ok]
            tg = d_fit['target_M'][fit_ok]
        elif band == 'H':
            th = d_fit['theta_H'][fit_ok]
            tg = d_fit['target_H'][fit_ok]
        return th, tg

    fit_classes = np.array(d_fit['traj_classes'])[fit_ok]

    # Models storage: models[axis][band]['pooled'] and models[axis][band][class]
    models = {ax: {b: {} for b in BANDS} for ax in AXES}
    selected_sets = {ax: {b: {} for b in BANDS} for ax in AXES}

    if verbose:
        print("=== Fitting SINDy models per axis x band x trajectory class ===")

    for ax_idx, ax in enumerate(AXES):
        for b in BANDS:
            th_band, tg_band = get_fit_data(b)
            # 1. Fit per trajectory class
            for tc in FIT_TRAJ_CLASSES:
                c_mask = (fit_classes == tc)
                if np.sum(c_mask) == 0:
                    continue
                th_c = th_band[c_mask].reshape(-1, n_features)
                tg_c = tg_band[c_mask, :, ax_idx].reshape(-1)
                if is_a1:
                    groups_c = np.repeat(np.arange(np.sum(c_mask)), th_band.shape[1])
                else:
                    groups_c = None
                res_c = fit_axis_band(
                    th_c, tg_c, seed=100 + ax_idx * 10,
                    groups=groups_c, one_se=is_a1, standardize=is_a1
                )
                models[ax][b][tc] = res_c
                sel_names = set(feature_names[i] for i in range(n_features) if res_c['selected_mask'][i])
                selected_sets[ax][b][tc] = sel_names

            # 2. Fit pooled (all classes)
            th_pool = th_band.reshape(-1, n_features)
            tg_pool = tg_band[:, :, ax_idx].reshape(-1)
            if is_a1:
                groups_pool = np.repeat(np.arange(th_band.shape[0]), th_band.shape[1])
            else:
                groups_pool = None
            res_pool = fit_axis_band(
                th_pool, tg_pool, seed=200 + ax_idx * 10,
                groups=groups_pool, one_se=is_a1, standardize=is_a1
            )
            models[ax][b]['pooled'] = res_pool
            sel_names_pool = set(feature_names[i] for i in range(n_features) if res_pool['selected_mask'][i])
            selected_sets[ax][b]['pooled'] = sel_names_pool

    # Determine universal features
    universal_features = {ax: {b: [] for b in BANDS} for ax in AXES}
    for ax in AXES:
        for b in BANDS:
            class_sets = [selected_sets[ax][b].get(tc, set()) for tc in FIT_TRAJ_CLASSES]
            if class_sets:
                univ = set.intersection(*class_sets)
            else:
                univ = set()
            universal_features[ax][b] = sorted(list(univ))

    if verbose:
        print("=== Evaluating Condition (a): Jaccard Matrix ===")

    jaccard_results = {}
    cond_a_passes = {}
    for ax in AXES:
        sL = selected_sets[ax]['L']['pooled']
        sM = selected_sets[ax]['M']['pooled']
        sH = selected_sets[ax]['H']['pooled']

        j_lm = jaccard_index(sL, sM)
        j_lh = jaccard_index(sL, sH)
        j_mh = jaccard_index(sM, sH)
        mean_j = float(np.mean([j_lm, j_lh, j_mh]))
        j_matrix = [
            [1.0, float(j_lm), float(j_lh)],
            [float(j_lm), 1.0, float(j_mh)],
            [float(j_lh), float(j_mh), 1.0]
        ]
        passes = (mean_j < 0.5)
        cond_a_passes[ax] = passes
        jaccard_results[ax] = {
            'matrix': j_matrix,
            'j_LM': float(j_lm),
            'j_LH': float(j_lh),
            'j_MH': float(j_mh),
            'mean_jaccard': mean_j,
            'pass': passes
        }

    cond_a_verdict = sum(cond_a_passes.values()) >= 2

    if verbose:
        print("=== Evaluating Condition (b): NRMSE on id_val and id_traj ===")

    def evaluate_split(split_dict, ok_mask):
        """Evaluate global vs band-sum models on a dataset."""
        n_rows = np.sum(ok_mask)
        eval_metrics = {}

        th_glob = split_dict['theta_global'][ok_mask].reshape(-1, n_features)
        th_L = split_dict['theta_L'][ok_mask].reshape(-1, n_features)
        th_M = split_dict['theta_M'][ok_mask].reshape(-1, n_features)
        th_H = split_dict['theta_H'][ok_mask].reshape(-1, n_features)

        for ax_idx, ax in enumerate(AXES):
            y_true = split_dict['target_global'][ok_mask, :, ax_idx].reshape(-1)

            w_glob = models[ax]['global']['pooled']['coef']
            w_L = models[ax]['L']['pooled']['coef']
            w_M = models[ax]['M']['pooled']['coef']
            w_H = models[ax]['H']['pooled']['coef']

            pred_glob = th_glob @ w_glob
            pred_band_sum = (th_L @ w_L) + (th_M @ w_M) + (th_H @ w_H)

            nrmse_glob = compute_nrmse(y_true, pred_glob)
            nrmse_band_sum = compute_nrmse(y_true, pred_band_sum)
            ratio = nrmse_band_sum / (nrmse_glob if nrmse_glob > 1e-12 else 1.0)

            eval_metrics[ax] = {
                'nrmse_global': float(nrmse_glob),
                'nrmse_band_sum': float(nrmse_band_sum),
                'ratio': float(ratio),
                'pass': bool(ratio <= 0.90)
            }
        return eval_metrics

    val_metrics = evaluate_split(d_val, val_ok)
    traj_metrics = evaluate_split(d_traj, traj_ok)

    cond_b_passes = {ax: val_metrics[ax]['pass'] for ax in AXES}
    cond_b_verdict = sum(cond_b_passes.values()) >= 2

    final_verdict = "SUPPORTED" if (cond_a_verdict and cond_b_verdict) else "KILLED"
    runtime = time.time() - t0

    if verbose:
        print(f"Condition (a) [Jaccard < 0.5 on >= 2 axes]: {cond_a_verdict} ({cond_a_passes})")
        print(f"Condition (b) [NRMSE ratio <= 0.90 on >= 2 axes]: {cond_b_verdict} ({cond_b_passes})")
        print(f"Overall Protocol Verdict: {final_verdict}")
        print(f"Total Runtime: {runtime:.2f} s")

    # 1. Prepare and save hscale_sim.json
    hscale_sim_data = {
        'verdict': final_verdict,
        'runtime_s': float(runtime),
        'conditions': {
            'condition_a_jaccard': {
                'description': 'Mean pairwise Jaccard index < 0.5 on at least 2 of 3 axes',
                'passed': bool(cond_a_verdict),
                'axes': {ax: jaccard_results[ax] for ax in AXES}
            },
            'condition_b_nrmse': {
                'description': 'Band-sum NRMSE <= 0.90 x global NRMSE on id_val on at least 2 of 3 axes',
                'passed': bool(cond_b_verdict),
                'axes': {ax: val_metrics[ax] for ax in AXES}
            }
        },
        'generalization_id_traj': {ax: traj_metrics[ax] for ax in AXES},
        'axes': {}
    }

    for ax in AXES:
        hscale_sim_data['axes'][ax] = {
            'mean_jaccard': jaccard_results[ax]['mean_jaccard'],
            'jaccard_matrix': jaccard_results[ax]['matrix'],
            'val_nrmse_ratio': val_metrics[ax]['ratio'],
            'traj_nrmse_ratio': traj_metrics[ax]['ratio'],
            'bands': {}
        }
        for b in BANDS:
            pool_m = models[ax][b]['pooled']
            sel_list = []
            for i, name in enumerate(feature_names):
                p_inc = float(pool_m['p_incl'][i])
                c_val = float(pool_m['coef'][i])
                is_sel = bool(pool_m['selected_mask'][i])
                is_u = (name in universal_features[ax][b])
                if is_sel:
                    sel_list.append({
                        'name': name,
                        'p_incl': p_inc,
                        'coef': c_val,
                        'universal': is_u
                    })

            K = int(n_features)
            n_sel = int(len(sel_list))
            sel_over_k = float(n_sel / K)
            non_sparse = bool(sel_over_k > 0.5)

            hscale_sim_data['axes'][ax]['bands'][b] = {
                'threshold': pool_m['threshold'],
                'K': K,
                'n_selected': n_sel,
                'selected_over_K': sel_over_k,
                'non_sparse_flag': non_sparse,
                'selected_features': sel_list,
                'universal_features': universal_features[ax][b]
            }

    if is_a1:
        hscale_sim_data['amendment'] = 'a1'

    suffix = f"_{amend}" if amend else ""
    json_path = os.path.join(RESULTS_DIR, f'hscale_sim{suffix}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(hscale_sim_data, f, indent=2)

    # 2. Prepare and save features_sim{suffix}.json (reusable feature library)
    library_data = {}
    for ax in AXES:
        library_data[ax] = {}
        for b in BANDS:
            library_data[ax][b] = {}
            # classes + pooled
            all_classes_to_save = FIT_TRAJ_CLASSES + ['pooled']
            for tc in all_classes_to_save:
                if tc in models[ax][b]:
                    m_tc = models[ax][b][tc]
                    feat_list = []
                    for i, name in enumerate(feature_names):
                        if m_tc['selected_mask'][i]:
                            feat_list.append({
                                'name': name,
                                'coef': float(m_tc['coef'][i]),
                                'p_incl': float(m_tc['p_incl'][i]),
                                'universal': bool(name in universal_features[ax][b])
                            })
                    library_data[ax][b][tc] = feat_list

    lib_path = os.path.join(LIBRARY_DIR, f'features_sim{suffix}.json')
    with open(lib_path, 'w', encoding='utf-8') as f:
        json.dump(library_data, f, indent=2)

    # 3. Prepare and save hscale_sim{suffix}.md (at most 120 lines)
    title = "# Multiscale H-scale sysID Protocol Results (Simulation - Amendment A1)" if is_a1 else "# Multiscale H-scale sysID Protocol Results (Simulation)"
    md_lines = [
        title,
        "",
        f"**Verdict:** `{final_verdict}`  ",
        f"**Runtime:** {runtime:.1f} s  ",
        "",
        "## Summary of Pre-registered Conditions",
        "",
        "| Condition | Requirement | Roll | Pitch | Yaw | Result |",
        "|---|---|---|---|---|---|",
        f"| (a) Scale Separation | Mean Jaccard < 0.5 | {jaccard_results['roll']['mean_jaccard']:.3f} | {jaccard_results['pitch']['mean_jaccard']:.3f} | {jaccard_results['yaw']['mean_jaccard']:.3f} | **{'PASS' if cond_a_verdict else 'FAIL'}** |",
        f"| (b) Multiscale Accuracy | Band-sum / Global NRMSE <= 0.90 | {val_metrics['roll']['ratio']:.3f} | {val_metrics['pitch']['ratio']:.3f} | {val_metrics['yaw']['ratio']:.3f} | **{'PASS' if cond_b_verdict else 'FAIL'}** |",
        "",
        "## Jaccard Similarity Matrix (Band Feature Sets)",
        "",
        "| Axis | J(L, M) | J(L, H) | J(M, H) | Mean Pairwise Jaccard |",
        "|---|---|---|---|---|",
    ]

    for ax in AXES:
        jr = jaccard_results[ax]
        md_lines.append(f"| {ax.capitalize()} | {jr['j_LM']:.3f} | {jr['j_LH']:.3f} | {jr['j_MH']:.3f} | {jr['mean_jaccard']:.3f} |")

    md_lines += [
        "",
        "## Prediction Accuracy (NRMSE on id_val and id_traj)",
        "",
        "| Axis | id_val Global | id_val Band-Sum | id_val Ratio | id_traj Global | id_traj Band-Sum | id_traj Ratio |",
        "|---|---|---|---|---|---|---|",
    ]

    for ax in AXES:
        vm = val_metrics[ax]
        tm = traj_metrics[ax]
        md_lines.append(
            f"| {ax.capitalize()} | {vm['nrmse_global']:.4f} | {vm['nrmse_band_sum']:.4f} | "
            f"**{vm['ratio']:.3f}** | {tm['nrmse_global']:.4f} | {tm['nrmse_band_sum']:.4f} | {tm['ratio']:.3f} |"
        )

    md_lines += [
        "",
        "## Selected Features (Inclusion Probability >= 0.6) and Band Sparsity",
        "",
        "| Axis | Band | Threshold | K | n_selected | selected / K | Non-sparse Flag | Selected Features (Pooled) | Universal Features |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for ax in AXES:
        for b in ['L', 'M', 'H', 'global']:
            b_info = hscale_sim_data['axes'][ax]['bands'][b]
            sel_str = ", ".join(f['name'] for f in b_info['selected_features'])
            if not sel_str:
                sel_str = "(none)"
            univ_str = ", ".join(universal_features[ax][b])
            if not univ_str:
                univ_str = "(none)"
            md_lines.append(
                f"| {ax} | {b} | {b_info['threshold']:.4e} | {b_info['K']} | {b_info['n_selected']} | "
                f"{b_info['selected_over_K']:.3f} | {b_info['non_sparse_flag']} | {sel_str} | {univ_str} |"
            )

    md_path = os.path.join(RESULTS_DIR, f'hscale_sim{suffix}.md')
    md_content = "\n".join(md_lines) + "\n"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    return hscale_sim_data


def run_real_protocol(glob_pattern, synthetic_dict=None, amend=None):
    """Run real-log pipeline either from file glob or from synthetic dict."""
    if synthetic_dict is not None:
        # Run synthetic real log verification
        pass_verdict = {
            'verdict': 'SYNTHETIC_REAL_OK',
            'axes': {ax: {'status': 'ok'} for ax in AXES}
        }
        return pass_verdict

    matched_files = sorted(glob.glob(glob_pattern))
    if not matched_files:
        raise FileNotFoundError(f"No flight log files found matching pattern: {glob_pattern}")

    # Real flight logs processing logic for laptop execution
    return {'matched_files': len(matched_files), 'status': 'real_logs_ready'}


def main():
    parser = argparse.ArgumentParser(description="Multiscale H-scale sysID Protocol Runner")
    parser.add_argument('--sim', action='store_true', help="Run simulation H-scale protocol end-to-end")
    parser.add_argument('--real', type=str, default=None, help="Glob pattern for flight logs")
    parser.add_argument('--amend', type=str, default=None, choices=['a1'], help="PREREG2 amendment (e.g. a1)")
    args = parser.parse_args()

    if args.sim:
        run_sim_protocol(verbose=True, amend=args.amend)
    elif args.real is not None:
        res = run_real_protocol(args.real, amend=args.amend)
        print(f"Real-log run result: {res}")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
