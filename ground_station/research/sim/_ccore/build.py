import pathlib
import subprocess
import sys


def build_pid_lib() -> pathlib.Path | None:
    ccore_dir = pathlib.Path(__file__).parent
    build_dir = ccore_dir / "build"
    build_dir.mkdir(exist_ok=True)
    
    pid_c_src = ccore_dir.parent.parent.parent.parent / "API" / "pid.c"
    pid_c_dst = build_dir / "pid.c"
    
    with open(pid_c_src, "r") as f:
        content = f.read()
    with open(pid_c_dst, "w") as f:
        f.write(content)
            
    pid_h = ccore_dir / "pid.h"
    with open(pid_h, "w") as f:
        f.write("#include \"robot_types.h\"\n#include \"global_declare.h\"\n#include \"SINS.h\"\nextern CtrlerTypeDef Ctrler;\n")
        
    stubs_c = build_dir / "stubs.c"
    with open(stubs_c, "w") as f:
        f.write("""
#include "robot_types.h"
#include "SINS.h"
float Sin_Yaw = 0.0f;
float Cos_Yaw = 1.0f;
float Sin_Pitch = 0.0f;
float Cos_Pitch = 1.0f;
float Sin_Roll = 0.0f;
float Cos_Roll = 1.0f;
// Ctrler defined in pid.c
""")
    
    if sys.platform == "win32":
        so_path = build_dir / "libpid.dll"
    else:
        so_path = build_dir / "libpid.so"
    try:
        subprocess.run(
            ["gcc", "-O2", "-shared", "-fPIC", "-I", str(ccore_dir), str(pid_c_dst), str(stubs_c), "-o", str(so_path)],
            check=True,
            capture_output=True
        )
    except FileNotFoundError:
        return None
    except subprocess.CalledProcessError as e:
        print("gcc failed:", e.stderr.decode(), file=sys.stderr)
        return None
            
    return so_path

if __name__ == "__main__":
    p = build_pid_lib()
    print(f"Built at {p}")
