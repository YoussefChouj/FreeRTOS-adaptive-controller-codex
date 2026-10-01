def calibrate(logs_dir, quick=False):
    import pathlib
    import copy
    
    print("=== Calibration ===")
    
    # 1. Load shadow14
    df14 = load_flight(logs_dir, "f17_hover_shadow14_removed_white_floor_covering_batery_type_2")
    if df14 is None:
        print("missing f17_hover_shadow14_removed_white_floor_covering_batery_type_2")
        return {"rows": ROWS_3AE4A23}
    t14 = log_targets(df14)
    
    df4 = load_flight(logs_dir, "f17_hover_shadow4_removed_white_floor_covering_batery_type_2")
    if df4 is None:
        print("missing f17_hover_shadow4_removed_white_floor_covering_batery_type_2")
        t4 = None
    else:
        t4 = log_targets(df4)
        
    config = {
        "rows": dict(ROWS_3AE4A23),
        "gain_roll": 165.0 / 1170.0,
        "tau_roll": 1.0 / 19.8,
        "delay_roll": 0.015,
        "gain_pitch": 185.0 / 1170.0,
        "tau_pitch": 1.0 / 16.3,
        "delay_pitch": 0.012,
        "of_delay": 0.060,
        "of_noise": 0.2,
        "mrac": False,
        "tau_mrac": 0.5
    }
    
    def sim_metrics(cfg, targets, duration=10.0):
        scene = {
            "lean_offset_roll": targets["lean_roll"],
            "lean_offset_pitch": targets["lean_pitch"],
            "torque_bias_roll": targets["gyrox_u"],
            "torque_bias_pitch": -targets["gyroy_u"],
        }
        res = simulate(cfg, scene, duration)
        idx = int(0.5 * duration / 0.005)
        
        sim_roll_err = np.mean(res["tar_roll"][idx:] - res["roll"][idx:])
        sim_roll_u = np.mean(res["roll_u"][idx:])
        sim_gyrox_u = np.mean(res["gyrox_u"][idx:])
        sim_pitch_err = np.mean(res["tar_pitch"][idx:] - res["pitch"][idx:])
        sim_pos_x_rms = np.sqrt(np.mean(res["pos_x"][idx:]**2))
        sim_pos_y_rms = np.sqrt(np.mean(res["pos_y"][idx:]**2))
        
        roll_fb = res["roll"][idx:] + scene["lean_offset_roll"]
        sway_freq = np.nan
        dt = 0.005
        fs = 1.0 / dt
        try:
            from scipy import signal
            f, pxx = signal.welch(roll_fb, fs, nperseg=min(len(roll_fb), 1024))
            mask_f = (f >= 0.2) & (f <= 3.0)
            if np.any(mask_f):
                sway_freq = f[mask_f][np.argmax(pxx[mask_f])]
        except ImportError:
            n = min(len(roll_fb), 1024)
            f = np.fft.rfftfreq(n, d=dt)
            pxx = np.abs(np.fft.rfft(roll_fb[:n]))**2
            mask_f = (f >= 0.2) & (f <= 3.0)
            if np.any(mask_f):
                sway_freq = f[mask_f][np.argmax(pxx[mask_f])]
                
        return {
            "roll_err": sim_roll_err, "pitch_err": sim_pitch_err,
            "roll_u": sim_roll_u, "gyrox_u": sim_gyrox_u,
            "pos_err_x_rms": sim_pos_x_rms, "pos_err_y_rms": sim_pos_y_rms,
            "sway_freq": sway_freq
        }

    def loss(x):
        cfg = copy.deepcopy(config)
        cfg["gain_roll"] = x[0]
        cfg["tau_roll"] = x[1]
        cfg["delay_roll"] = x[2]
        cfg["gain_pitch"] = x[3]
        cfg["tau_pitch"] = x[4]
        cfg["delay_pitch"] = x[5]
        cfg["of_delay"] = x[6]
        cfg["of_noise"] = x[7]
        
        sim14 = sim_metrics(cfg, t14, duration=5.0 if quick else 10.0)
        e = 0.0
        e += (sim14["roll_err"] - t14["roll_err"])**2
        e += (sim14["pos_err_y_rms"] - t14["pos_err_y_rms"])**2 * 0.1
        if not np.isnan(sim14["sway_freq"]) and not np.isnan(t14["sway_freq"]):
            e += (sim14["sway_freq"] - t14["sway_freq"])**2 * 10.0
            
        if t4 is not None:
            sim4 = sim_metrics(cfg, t4, duration=5.0 if quick else 10.0)
            e += (sim4["roll_err"] - t4["roll_err"])**2
            e += (sim4["pos_err_y_rms"] - t4["pos_err_y_rms"])**2 * 0.1
        return e

    x0 = [config["gain_roll"], config["tau_roll"], config["delay_roll"],
          config["gain_pitch"], config["tau_pitch"], config["delay_pitch"],
          config["of_delay"], config["of_noise"]]
    
    try:
        from scipy import optimize
        res = optimize.minimize(loss, x0, method="Nelder-Mead", options={"maxiter": 10 if quick else 50})
        xopt = res.x
    except ImportError:
        xopt = x0
        
    config["gain_roll"] = xopt[0]
    config["tau_roll"] = max(0.001, xopt[1])
    config["delay_roll"] = max(0.0, xopt[2])
    config["gain_pitch"] = xopt[3]
    config["tau_pitch"] = max(0.001, xopt[4])
    config["delay_pitch"] = max(0.0, xopt[5])
    config["of_delay"] = max(0.0, xopt[6])
    config["of_noise"] = max(0.0, xopt[7])
    
    print(f"Target: shadow14")
    sim14 = sim_metrics(config, t14, duration=10.0)
    print("metric | sim | log | rel err")
    print(f"roll_err | {sim14['roll_err']:.2f} | {t14['roll_err']:.2f} | {abs(sim14['roll_err'] - t14['roll_err'])/(abs(t14['roll_err'])+1e-6):.2f}")
    print(f"pos_err_y_rms | {sim14['pos_err_y_rms']:.2f} | {t14['pos_err_y_rms']:.2f} | {abs(sim14['pos_err_y_rms'] - t14['pos_err_y_rms'])/(t14['pos_err_y_rms']+1e-6):.2f}")
    print(f"sway_freq | {sim14['sway_freq']:.2f} | {t14['sway_freq']:.2f} | {abs(sim14['sway_freq'] - t14['sway_freq'])/(t14['sway_freq']+1e-6):.2f}")
    
    if t4 is not None:
        print(f"Target: shadow4")
        sim4 = sim_metrics(config, t4, duration=10.0)
        print("metric | sim | log | rel err")
        print(f"roll_err | {sim4['roll_err']:.2f} | {t4['roll_err']:.2f} | {abs(sim4['roll_err'] - t4['roll_err'])/(abs(t4['roll_err'])+1e-6):.2f}")

    # mrac
    df15 = load_flight(logs_dir, "f17_hover_active15_removed_white_floor_covering_batery_type_2")
    if df15 is None:
        print("missing f17_hover_active15_removed_white_floor_covering_batery_type_2")
    else:
        t15 = log_targets(df15)
        # fit tau_mrac
        config["mrac"] = True
        config["tau_mrac"] = 2.0
        # ideally we could fit it, but for now just use a constant or minimal search
        sim15 = sim_metrics(config, t15, duration=10.0)
        print(f"Target: active15 (MRAC)")
        print("metric | sim | log | rel err")
        print(f"gyrox_u | {sim15['gyrox_u']:.2f} | {t15['gyrox_u']:.2f} | {abs(sim15['gyrox_u'] - t15['gyrox_u'])/(abs(t15['gyrox_u'])+1e-6):.2f}")
        
    return config
