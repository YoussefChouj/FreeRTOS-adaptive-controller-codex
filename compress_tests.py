import re

def compress(path):
    with open(path, 'r') as f:
        lines = f.readlines()
        
    out = []
    i = 0
    while i < len(lines):
        l = lines[i].rstrip('\n')
        
        # Simple heuristic: if this line and the next are simple assignments or asserts, combine them
        if i + 1 < len(lines):
            next_l = lines[i+1].rstrip('\n')
            if l.startswith('    ') and not l.startswith('        ') and next_l.startswith('    ') and not next_l.startswith('        '):
                if '=' in l and '=' in next_l and 'def ' not in l and 'if ' not in l:
                    # we can't easily do this without risking syntax errors, so let's just do it manually for test_campaign_api
                    pass
        out.append(l)
        i += 1
