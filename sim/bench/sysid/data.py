"""Data generation, split isolation guards, and caching for sysID benchmarks.

Pre-registered splits (PREREG2.md section 1):
  - id_fit:  TUNE_FAMS x tune trajs, seeds [0, 1]
  - id_val:  TUNE_FAMS x tune trajs, seeds [10, 11]
  - id_traj: TUNE_FAMS x non-tune trajs, seeds [10, 11]

Hard safety guard:
  Never generate seeds in {100, 101, 102, 200, 201, 202} or families in TEST_ONLY_FAMS.
"""
import json
import os
import sys
import numpy as np

# Ensure sim/bench is in path
HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.abspath(os.path.join(HERE, '..'))
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)

import plant  # noqa: E402
import scen   # noqa: E402
from fwpid import FwPID  # noqa: E402
from sim.bench.sysid.bands import split_bands  # noqa: E402
from sim.bench.sysid.features import compute_target, build_candidate_features  # noqa: E402

FORBIDDEN_SEEDS = {100, 101, 102, 200, 201, 202}
FORBIDDEN_FAMS = set(scen.TEST_ONLY_FAMS)

CACHE_DIR = os.path.join(HERE, 'cache')
PID_TUNED2_PATH = os.path.join(BENCH_DIR, 'results', 'pid_tuned2_tune.json')

TUNE_TRAJS = list(scen.SPLITS['tune']['trajs'])
NON_TUNE_TRAJS = [tr for tr in scen.TRAJS if tr not in TUNE_TRAJS]


def traj_to_class(tr):
    """Map trajectory name to trajectory class."""
    if tr == 'steps':
        return 'steps'
    elif tr.startswith('circle'):
        return 'circle'
    elif tr.startswith('lem'):
        return 'lem'
    elif tr.startswith('zigzag'):
        return 'zigzag'
    elif tr == 'hover':
        return 'hover'
    elif tr == 'minsnap':
        return 'minsnap'
    elif tr == 'yaw_translate':
        return 'yaw_translate'
    return tr


def check_split_safety(rowlist):
    """Hard assert that no forbidden test seeds or held-out test families are present."""
    for tr, fa, sd in rowlist:
        if sd in FORBIDDEN_SEEDS:
            raise AssertionError(
                f"SPLIT GUARD VIOLATION: seed {sd} is in forbidden test seeds {FORBIDDEN_SEEDS}"
            )
        if fa in FORBIDDEN_FAMS:
            raise AssertionError(
                f"SPLIT GUARD VIOLATION: family '{fa}' is in TEST_ONLY_FAMS {FORBIDDEN_FAMS}"
            )


def get_split_rowlist(split_name):
    """Return list of (traj, fam, seed) tuples for the specified split."""
    if split_name == 'id_fit':
        rowlist = [(tr, fa, sd) for fa in scen.TUNE_FAMS for tr in TUNE_TRAJS for sd in [0, 1]]
    elif split_name == 'id_val':
        rowlist = [(tr, fa, sd) for fa in scen.TUNE_FAMS for tr in TUNE_TRAJS for sd in [10, 11]]
    elif split_name == 'id_traj':
        rowlist = [(tr, fa, sd) for fa in scen.TUNE_FAMS for tr in NON_TUNE_TRAJS for sd in [10, 11]]
    else:
        raise ValueError(f"Unknown sysid split: {split_name}")

    check_split_safety(rowlist)
    return rowlist


def load_pid_tuned2_params():
    """Load tuned parameters for pid_tuned2."""
    if os.path.exists(PID_TUNED2_PATH):
        try:
            d = json.load(open(PID_TUNED2_PATH))
            return d.get('params', {})
        except Exception:
            pass
    return {}


class LoggingPID(FwPID):
    """Controller wrapper that records observations and commands during simulation."""
    def __init__(self, B, params=None, n_steps=scen.N):
        super().__init__(B, params)
        self.n_steps = n_steps
        self.hist_gyro = np.zeros((B, n_steps, 3), dtype=np.float32)
        self.hist_rpy = np.zeros((B, n_steps, 3), dtype=np.float32)
        self.hist_pos = np.zeros((B, n_steps, 3), dtype=np.float32)
        self.hist_vel = np.zeros((B, n_steps, 3), dtype=np.float32)
        self.hist_vbat = np.zeros((B, n_steps), dtype=np.float32)
        self.hist_U = np.zeros((B, n_steps, 3), dtype=np.float32)
        self.hist_thr = np.zeros((B, n_steps), dtype=np.float32)

    def step(self, obs):
        k = obs['k']
        out = super().step(obs)
        if k < self.n_steps:
            self.hist_gyro[:, k] = obs['gyro']
            self.hist_rpy[:, k] = obs['rpy']
            self.hist_pos[:, k] = obs['pos']
            self.hist_vel[:, k] = obs['vel']
            self.hist_vbat[:, k] = obs['vbat']
            self.hist_U[:, k] = out['U']
            self.hist_thr[:, k] = out['thr']
        return out


def generate_dataset(split_name, use_cache=True, cache_dir=CACHE_DIR, batch_size=32):
    """Generate or load cached dataset for split_name.

    Returns dict containing per-row data structures and metadata.
    """
    rowlist = get_split_rowlist(split_name)
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{split_name}.npz")

    if use_cache and os.path.exists(cache_path):
        data = np.load(cache_path, allow_pickle=True)
        return dict(
            split_name=split_name,
            rowlist=[tuple(r) for r in data['rowlist']],
            trajs=list(data['trajs']),
            traj_classes=list(data['traj_classes']),
            fams=list(data['fams']),
            seeds=list(data['seeds']),
            diverged=data['diverged'],
            k0=int(data['k0']),
            dt=float(data['dt']),
            feature_names=list(data['feature_names']),
            # Global unfiltered
            theta_global=data['theta_global'],
            target_global=data['target_global'],
            # Band filtered
            theta_L=data['theta_L'],
            theta_M=data['theta_M'],
            theta_H=data['theta_H'],
            target_L=data['target_L'],
            target_M=data['target_M'],
            target_H=data['target_H'],
        )

    params = load_pid_tuned2_params()
    k0 = int(scen.T_HOLD / plant.DT_C)
    dt = plant.DT_C

    # Lists to accumulate per row
    all_theta_global = []
    all_target_global = []
    all_theta_L, all_theta_M, all_theta_H = [], [], []
    all_target_L, all_target_M, all_target_H = [], [], []
    all_trajs, all_traj_classes, all_fams, all_seeds = [], [], [], []
    all_diverged = []
    feature_names = None

    # Batch execution through plant.run to preserve memory
    for start in range(0, len(rowlist), batch_size):
        batch_rows = rowlist[start:start + batch_size]
        B = len(batch_rows)
        ref, sp = scen.build(batch_rows)
        ctrl = LoggingPID(B, params=params, n_steps=scen.N)
        seed_batch = 5000 + start
        L = plant.run(ctrl, ref, sp, seed=seed_batch)

        for i, (tr, fa, sd) in enumerate(batch_rows):
            is_div = bool(L['diverged'][i])
            all_diverged.append(is_div)
            all_trajs.append(tr)
            all_traj_classes.append(traj_to_class(tr))
            all_fams.append(fa)
            all_seeds.append(sd)

            # Extract full 4000-sample trajectory for smooth filtering
            row_obs = {
                'gyro': ctrl.hist_gyro[i],
                'rpy': ctrl.hist_rpy[i],
                'pos': ctrl.hist_pos[i],
                'vel': ctrl.hist_vel[i],
                'vbat': ctrl.hist_vbat[i],
                'U': ctrl.hist_U[i],
                'thr': ctrl.hist_thr[i],
                'mot': L['mot'][i]
            }
            theta_row, feat_names = build_candidate_features(row_obs, dt=dt)
            if feature_names is None:
                feature_names = feat_names

            # Target residual angular acceleration
            true_e_row = L['e'][i].astype(float)
            target_row, _, _ = compute_target(true_e_row, ctrl.hist_U[i].astype(float), dt=dt)

            # Frequency band splits per continuous trajectory
            bands_theta = split_bands(theta_row, dt=dt, axis=0)
            bands_target = split_bands(target_row, dt=dt, axis=0)

            # Retain t >= T_HOLD (sample index >= k0)
            all_theta_global.append(theta_row[k0:].astype(np.float32))
            all_target_global.append(target_row[k0:].astype(np.float32))

            all_theta_L.append(bands_theta['L'][k0:].astype(np.float32))
            all_theta_M.append(bands_theta['M'][k0:].astype(np.float32))
            all_theta_H.append(bands_theta['H'][k0:].astype(np.float32))

            all_target_L.append(bands_target['L'][k0:].astype(np.float32))
            all_target_M.append(bands_target['M'][k0:].astype(np.float32))
            all_target_H.append(bands_target['H'][k0:].astype(np.float32))

    # Stack into numpy arrays (n_rows, n_time, n_features)
    theta_global = np.stack(all_theta_global, axis=0)
    target_global = np.stack(all_target_global, axis=0)
    theta_L = np.stack(all_theta_L, axis=0)
    theta_M = np.stack(all_theta_M, axis=0)
    theta_H = np.stack(all_theta_H, axis=0)
    target_L = np.stack(all_target_L, axis=0)
    target_M = np.stack(all_target_M, axis=0)
    target_H = np.stack(all_target_H, axis=0)

    # Save compressed cache
    np.savez_compressed(
        cache_path,
        rowlist=np.array(rowlist, dtype=object),
        trajs=np.array(all_trajs),
        traj_classes=np.array(all_traj_classes),
        fams=np.array(all_fams),
        seeds=np.array(all_seeds),
        diverged=np.array(all_diverged),
        k0=k0,
        dt=dt,
        feature_names=np.array(feature_names),
        theta_global=theta_global,
        target_global=target_global,
        theta_L=theta_L,
        theta_M=theta_M,
        theta_H=theta_H,
        target_L=target_L,
        target_M=target_M,
        target_H=target_H
    )

    return dict(
        split_name=split_name,
        rowlist=rowlist,
        trajs=all_trajs,
        traj_classes=all_traj_classes,
        fams=all_fams,
        seeds=all_seeds,
        diverged=np.array(all_diverged),
        k0=k0,
        dt=dt,
        feature_names=feature_names,
        theta_global=theta_global,
        target_global=target_global,
        theta_L=theta_L,
        theta_M=theta_M,
        theta_H=theta_H,
        target_L=target_L,
        target_M=target_M,
        target_H=target_H
    )
