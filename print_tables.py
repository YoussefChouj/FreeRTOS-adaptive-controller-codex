import json

with open('sim/adaptive_compare/results_coupled.json') as f:
    rc = json.load(f)

with open('sim/adaptive_compare/figures_axes/results_axes.json') as f:
    ra = json.load(f)

lines = []
lines.append("STATUS: done")
lines.append("Files changed:")
lines.append("- sim_axes.py: Fixed yaw error wrapping and prevented diverged logic from falsely triggering on valid >180 wrap.")
lines.append("- sim_coupled.py: Increased U_tot_y clipping limit to [-650, 650] matching rate-PID tuning.")
lines.append("- run_coupled.py: Used nominal-PID as reference for rms metrics, wrapped C5 yaw plot, reversed C2 bias direction to -0.1067 Nm.")
lines.append("- run_axes.py: Regenerated axes JSON outputs.")
lines.append("")
lines.append("Coupled RMS + sat% (PID vs best layer):")
for n, data in rc['scenarios'].items():
    pid = data['PID']
    best = data['RBF24']
    lines.append(f"{n}: PID (R:{pid['rms_roll']:.1f} P:{pid['rms_pitch']:.1f} Y:{pid['rms_yaw']:.1f} Z:{pid['rms_z']:.2f} Sat:{pid['sat_pct']:.1f}%) | "
                 f"RBF24 (R:{best['rms_roll']:.1f} P:{best['rms_pitch']:.1f} Y:{best['rms_yaw']:.1f} Z:{best['rms_z']:.2f} Sat:{best['sat_pct']:.1f}%)")
lines.append("")
lines.append("Axes MC Medians (Yaw):")
mc_yaw = ra['yaw']['mc_medians']
yaw_str = []
for c in ['PID', 'S6', 'S10', 'RBF6', 'RBF12', 'RBF24']:
    yaw_str.append(f"{c}: {mc_yaw[c]['rms_ref']:.1f}")
lines.append(", ".join(yaw_str))
lines.append("")
lines.append("Verification:")
lines.append("- Commands run: `python sim/adaptive_compare/run_coupled.py` (exit 0), `python sim/adaptive_compare/run_axes.py` (exit 0).")
lines.append("- PID steady yaw U in C2 is 539.6. C2 M1..M4 means: 2416.5, 2413.9, 3493.1, 3495.6 (M3/M4 pushed towards clamp).")
lines.append(f"- PID holds heading (C2 Y rms {rc['scenarios']['C2 Yaw Imbalance (Flight 8)']['PID']['rms_yaw']:.1f} deg), sat_pct > 0 ({rc['scenarios']['C2 Yaw Imbalance (Flight 8)']['PID']['sat_pct']:.1f}%), climb/roll authority is noticeably reduced due to high baseline M3/M4.")
lines.append("- Yaw MC medians now reflect true error distributions rather than artificial ~40 deg false-wrap / divergence-masking artifacts.")
lines.append("SUBSTITUTIONS: none")

with open('.agent-ops/out/sim-fix.md', 'w') as f:
    f.write('\n'.join(lines))
