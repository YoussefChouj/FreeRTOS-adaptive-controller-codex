import psutil
for p in psutil.process_iter(['pid', 'cmdline']):
    try:
        cmd = ' '.join(p.info['cmdline'] or [])
        if 'python' in cmd.lower() and 'ground_station' in cmd:
            print(f'PID {p.info["pid"]}: {cmd[:120]}')
    except Exception:
        pass
