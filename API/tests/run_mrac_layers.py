#!/usr/bin/env python3
import os
import sys
import subprocess
import glob
import tempfile
import shutil

CFLAGS = [
    "-std=c99", "-O2", "-msse2", "-mfpmath=sse", "-ffp-contract=off", "-fno-fast-math",
    "-Wall", "-Wextra", "-pedantic"
]

def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    driver_path = os.path.join(repo_root, "API/tests/test_mrac_layers.c")
    stubs_dir = os.path.join(repo_root, "API/tests/stubs")
    api_dir = os.path.join(repo_root, "API")

    variants = [
        {"id": 0, "flags": []},
        {"id": 4, "flags": []},
        {"id": 5, "flags": []},
        {"id": 6, "flags": []},
    ]

    total_checks = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for f in glob.glob(os.path.join(api_dir, "mrac*.c")) + glob.glob(os.path.join(api_dir, "mrac*.h")):
            shutil.copy2(f, tmpdir)

        c_files = glob.glob(os.path.join(tmpdir, "mrac*.c"))
        out_bin = os.path.join(tmpdir, "test_mrac_layers.bin")
        base_warnings = None

        for v in variants:
            v_id = v["id"]
            flags = [f"-DMRAC_VARIANT={v_id}"] + v["flags"]
            
            cmd = ["gcc"] + CFLAGS + flags + [driver_path] + c_files + [
                "-I", tmpdir, "-I", stubs_dir, "-lm", "-o", out_bin
            ]
            
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            warnings = [line for line in res.stderr.split('\n') if 'warning:' in line]
            
            if v_id == 0:
                base_warnings = warnings
            else:
                for w in warnings:
                    if w not in base_warnings:
                        print(f"Compilation warning for variant {v_id} with flags {flags}:\n{w}", file=sys.stderr)
                        sys.exit(1)
                        
            if res.returncode != 0:
                print(f"Compilation failed for variant {v_id} with flags {flags}:\n{res.stderr}", file=sys.stderr)
                sys.exit(1)
                
            res = subprocess.run([out_bin], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            lines = res.stdout.strip().split("\n")
            
            for line in lines:
                if not line: continue
                if "FAIL" in line:
                    print(f"Test failed for variant {v_id}:\n{res.stdout}", file=sys.stderr)
                    sys.exit(1)
                if "PASS" in line:
                    total_checks += 1
                if "SIZEOF" in line:
                    print(line)
                    
    print(f"LAYERS OK: {len(variants)} builds, {total_checks} checks")

if __name__ == "__main__":
    main()
