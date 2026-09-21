import re, os, subprocess, sys

html_path = r'C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\docs\dashboard-platform\shell\index.html'
html = open(html_path, 'r', encoding='utf-8').read()
m = re.search(r'<script type="module">([\s\S]*?)<\/script>', html)
if not m:
    print('no type=module script found')
    sys.exit(1)
src = m.group(1)
tmp = os.path.join(os.environ['TEMP'], 'shell_module_test.mjs')
with open(tmp, 'w') as f:
    f.write(src)

print(f'Written {len(src)} chars to {tmp}')

# Check syntax
result = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
if result.returncode == 0:
    print('OK: index.html inline ES module script is syntactically valid')
else:
    print('FAIL:', result.stderr[:500])
