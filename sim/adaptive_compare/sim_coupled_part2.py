def simulate(ctrl, P, gamma, t_c, cmd_r, cmd_p, cmd_y, cmd_z, ref_log):
    B = len(P['Jr'])
    n_c = len(t_c)
    n_p = n_c * SUB
    
    phi_m = ref_log['phi'][:, 0]
    p_m = ref_log['p'][:, 0]
    theta_m = ref_log['theta'][:, 0]
    q_m = ref_log['q'][:, 0]
    psi_m = ref_log['psi'][:, 0]
    r_m = ref_log['r'][:, 0]
    z_m = ref_log['z'][:, 0]
    vz_m = ref_log['vz'][:, 0]

    gust_r = make_disturbance(B, n_p, P['gust_r'], P['seed'])
    gust_p = make_disturbance(B, n_p, P['gust_p'], P['seed']+1)
    gust_y = make_disturbance(B, n_p, P['gust_y'], P['seed']+2)
    
    rng = np.random.default_rng(P['seed'] + 7)
    noise = lambda a: rng.standard_normal((n_c, B)) * np.deg2rad(a)[None, :]
    n_pm, n_qm, n_rm = noise(P['noise_p']), noise(P['noise_p']), noise(P['noise_p'])
    n_phm, n_thm, n_psm = noise(P['noise_phi']), noise(P['noise_phi']), noise(P['noise_phi'])
    n_zm = rng.standard_normal((n_c, B)) * P['noise_z'][None, :]
    
    Jxt = Jx * P['Jr']
    Jyt = Jy * P['Jr']
    Jzt = Jz * P['Jr']
    d_steps = int(round(DELAY / DT_P))

    ang_r, rate_r = PID(ANG_PR, B), PID(RATE_PR, B)
    ang_p, rate_p = PID(ANG_PR, B), PID(RATE_PR, B)
    ang_y, rate_y = PID(ANG_Y, B), PID(RATE_Y, B)
    pos_z, rate_z = PID(POS_Z, B), PID(RATE_Z, B)
    
    mrac_r = MRAC(ctrl, B)
    mrac_p = MRAC(ctrl, B)
    mrac_y = MRAC(ctrl, B)
    
    phi, theta, psi, z = np.zeros(B), np.zeros(B), np.zeros(B), np.zeros(B)
    p, q, r, vz = np.zeros(B), np.zeros(B), np.zeros(B), np.zeros(B)
    
    buf1 = np.ones((d_steps + 1, B)) * 2000
    buf2 = np.ones((d_steps + 1, B)) * 2000
    buf3 = np.ones((d_steps + 1, B)) * 2000
    buf4 = np.ones((d_steps + 1, B)) * 2000
    bi = 0
    
    M1_act, M2_act, M3_act, M4_act = np.ones(B)*2000, np.ones(B)*2000, np.ones(B)*2000, np.ones(B)*2000

    L = {k: np.zeros((n_c, B)) for k in ('phi', 'theta', 'psi', 'z', 'p', 'q', 'r', 'vz', 
                                         'sat', 'M1', 'M2', 'M3', 'M4', 'U_pid_r', 'U_pid_y')}
    diverged = np.zeros(B, bool)
    
    for k in range(n_c):
        tk = t_c[k]
        phm_meas = phi + n_phm[k]
        thm_meas = theta + n_thm[k]
        psm_meas = psi + n_psm[k]
        pm_meas = p + n_pm[k]
        qm_meas = q + n_qm[k]
        rm_meas = r + n_rm[k]
        zm_meas = z + n_zm[k]
        
        U_pid_r = rate_r.step(ang_r.step(cmd_r[k] - np.rad2deg(phm_meas)) - np.rad2deg(pm_meas))
        U_pid_p = rate_p.step(ang_p.step(cmd_p[k] - np.rad2deg(thm_meas)) - np.rad2deg(qm_meas))
        U_pid_y = rate_y.step(ang_y.step(cmd_y[k] - np.rad2deg(psm_meas)) - np.rad2deg(rm_meas))
        U_pid_z = rate_z.step(pos_z.step(cmd_z[k] - zm_meas) - vz)
        
        u_ad_r = mrac_r.step(pm_meas, phm_meas, U_pid_r, p_m[k], phi_m[k], qm_meas, rm_meas, gamma)
        u_ad_p = mrac_p.step(qm_meas, thm_meas, U_pid_p, q_m[k], theta_m[k], pm_meas, rm_meas, gamma)
        u_ad_y = mrac_y.step(rm_meas, psm_meas, U_pid_y, r_m[k], psi_m[k], pm_meas, qm_meas, gamma)
        
        U_tot_r = np.clip(U_pid_r + u_ad_r, -500, 500)
        U_tot_p = np.clip(U_pid_p + u_ad_p, -500, 500)
        U_tot_y = np.clip(U_pid_y + u_ad_y, -500, 500)
        Thr_out = np.clip(U_pid_z + 2950, 2000, 4000)
        
        M1 = Thr_out + U_tot_p - U_tot_r - U_tot_y
        M2 = Thr_out - U_tot_p + U_tot_r - U_tot_y
        M3 = Thr_out + U_tot_p + U_tot_r + U_tot_y
        M4 = Thr_out - U_tot_p - U_tot_r + U_tot_y
        
        M1_c = np.clip(M1, 2000, 4000)
        M2_c = np.clip(M2, 2000, 4000)
        M3_c = np.clip(M3, 2000, 4000)
        M4_c = np.clip(M4, 2000, 4000)
        
        sat = (M1_c != M1) | (M2_c != M2) | (M3_c != M3) | (M4_c != M4)
        L['sat'][k] = sat
        
        faulted = tk >= P['t_f']
        lam = np.where(faulted, P['lam_f'], 1.0)
        
        for j in range(SUB):
            buf1[bi] = M1_c
            buf2[bi] = M2_c
            buf3[bi] = M3_c
            buf4[bi] = M4_c
            bi = (bi + 1) % (d_steps + 1)
            
            M1_act += DT_P / TAU_M * (buf1[bi] - M1_act)
            M2_act += DT_P / TAU_M * (buf2[bi] - M2_act)
            M3_act += DT_P / TAU_M * (buf3[bi] - M3_act)
            M4_act += DT_P / TAU_M * (buf4[bi] - M4_act)
            
            # Apply fault (e.g. M1 loses thrust)
            M1_eff = 2000 + (M1_act - 2000) * lam
            M2_eff = 2000 + (M2_act - 2000)
            M3_eff = 2000 + (M3_act - 2000)
            M4_eff = 2000 + (M4_act - 2000)
            
            tau_x = c_PR * (M2_eff + M3_eff - M1_eff - M4_eff) + gust_r[k * SUB + j] + P['bias_r']
            tau_y = c_PR * (M1_eff + M3_eff - M2_eff - M4_eff) + gust_p[k * SUB + j] + P['bias_p']
            tau_z = c_Y * (M3_eff + M4_eff - M1_eff - M2_eff) + gust_y[k * SUB + j] + P['bias_y']
            F_z = c_T * (M1_eff + M2_eff + M3_eff + M4_eff - 4*2000)
            
            # Drag
            tau_x -= P['drag'] * p * np.abs(p)
            tau_y -= P['drag'] * q * np.abs(q)
            tau_z -= P['drag'] * r * np.abs(r)
            
            p += DT_P * (tau_x - (Jzt - Jyt) * q * r) / Jxt
            q += DT_P * (tau_y - (Jxt - Jzt) * p * r) / Jyt
            r += DT_P * (tau_z - (Jyt - Jxt) * p * q) / Jzt
            vz += DT_P * (F_z - M_mass * g) / M_mass
            phi += DT_P * p
            theta += DT_P * q
            psi += DT_P * r
            z += DT_P * vz
            
        bad = (np.abs(phi) > np.deg2rad(120)) | (np.abs(theta) > np.deg2rad(120))
        diverged |= bad
        phi = np.where(bad, np.sign(phi) * np.deg2rad(120), phi)
        theta = np.where(bad, np.sign(theta) * np.deg2rad(120), theta)
        
        L['phi'][k] = np.rad2deg(phi)
        L['theta'][k] = np.rad2deg(theta)
        L['psi'][k] = np.rad2deg(psi)
        L['z'][k] = z
        L['p'][k] = np.rad2deg(p)
        L['q'][k] = np.rad2deg(q)
        L['r'][k] = np.rad2deg(r)
        L['vz'][k] = vz
        L['M1'][k] = M1_c
        L['M2'][k] = M2_c
        L['M3'][k] = M3_c
        L['M4'][k] = M4_c
        L['U_pid_r'][k] = U_pid_r
        L['U_pid_y'][k] = U_pid_y
        
    L['diverged'] = diverged
    return L
