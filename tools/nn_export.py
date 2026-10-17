import argparse
import sys
import os
import json
import hashlib
from pathlib import Path
import math

# Add repo root to sys.path to allow importing from ground_station
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def array_to_c_string(name, data, indent=0):
    flat = data.flatten()
    s = " " * indent + f"static const float {name}[{len(flat)}] = {{\n"
    s += " " * (indent + 4)
    for i, val in enumerate(flat):
        if math.isnan(val):
            s += "NAN"
        elif math.isinf(val):
            s += "INFINITY" if val > 0 else "-INFINITY"
        else:
            s += repr(float(val)) + "f"

        if i < len(flat) - 1:
            s += ", "
        if (i + 1) % 4 == 0 and i < len(flat) - 1:
            s += "\n" + " " * (indent + 4)
    s += "\n" + " " * indent + "};\n"
    return s

def export_model(ckpt_path, out_path):
    import torch
    import numpy as np

    out_path = Path(out_path)

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)

    cfg = ckpt.get("cfg", {})
    kind = cfg.get("kind", "gru")
    layers = cfg.get("layers", 1)
    rate_hz = ckpt.get("rate_hz", 20)
    H = cfg.get("H", 32)
    D = cfg.get("D", 17)
    head = cfg.get("head", "scale")
    feats = ckpt.get("feats", list(range(D)))
    channels = ckpt.get("channels", [])

    if kind != "gru":
        print(f"Error: unsupported kind '{kind}'", file=sys.stderr)
        sys.exit(1)
    if layers > 2:
        print(f"Error: unsupported layers {layers}", file=sys.stderr)
        sys.exit(1)
    if rate_hz != 20:
        print(f"Error: unsupported rate {rate_hz}", file=sys.stderr)
        sys.exit(1)

    state_dict = ckpt["state_dict"]
    mu = ckpt["mu"]
    sd = ckpt["sd"]
    sha256 = ckpt.get("sha256", "unknown")

    # Rebuild model to generate golden vectors
    from ground_station.analysis.nn_io import build
    model = build(D, kind=kind, H=H, head=head, layers=layers, drop=0.0)
    model.load_state_dict(state_dict)
    model.eval()

    with open(out_path, "w") as f:
        f.write("#ifndef NN_VEL_WEIGHTS_H\n")
        f.write("#define NN_VEL_WEIGHTS_H\n\n")
        f.write("#include <stdint.h>\n")
        f.write("#include <math.h>\n\n")

        if channels:
            f.write("/* Channels: " + ", ".join(channels) + " */\n\n")

        f.write(f"#define NN_VEL_D {D}\n")
        f.write(f"#define NN_VEL_H_DIM {H}\n")
        f.write(f"#define NN_VEL_LAYERS {layers}\n")
        f.write(f"#define NN_VEL_HEAD_{head.upper()} 1\n\n")
        f.write(f'static const char* const NN_VEL_SHA256 = "{sha256}";\n\n')

        # Weights and Biases
        total_floats = 0
        def write_arr(name, tensor):
            nonlocal total_floats
            arr = tensor.numpy()
            f.write(array_to_c_string(name, arr))
            total_floats += arr.size

        # mu and sd
        # Assuming feats defines standardisation on feats, but nn_vel_accum takes 17 channels
        # For simplicity, if mu and sd correspond to the D features
        write_arr("nn_vel_mu", torch.tensor(mu))
        write_arr("nn_vel_sd", torch.tensor(sd))

        write_arr("nn_vel_inp_w", state_dict["inp.weight"])
        write_arr("nn_vel_inp_b", state_dict["inp.bias"])

        for i in range(layers):
            write_arr(f"nn_vel_gru_{i}_w_ih", state_dict[f"rnn.weight_ih_l{i}"])
            write_arr(f"nn_vel_gru_{i}_w_hh", state_dict[f"rnn.weight_hh_l{i}"])
            write_arr(f"nn_vel_gru_{i}_b_ih", state_dict[f"rnn.bias_ih_l{i}"])
            write_arr(f"nn_vel_gru_{i}_b_hh", state_dict[f"rnn.bias_hh_l{i}"])

        write_arr("nn_vel_out_w", state_dict["out.weight"])
        write_arr("nn_vel_out_b", state_dict["out.bias"])

        f.write(f"\n// Computed memory requirements (H={H}, D={D}, layers={layers}):\n")
        f.write(f"// Flash bytes (weights): {total_floats * 4}\n")
        # MACs per step:
        # inp: D * H
        # gru cell: 3 * (H*H + H*H) = 6 * H^2 (approx, wait, input is H for ih? wait, for layer 0 input is H (from inp), for layer 1 input is H (from layer 0))
        # out: H * nout (where nout is 4 for scale, 2 for add)
        nout = 4 if head == "scale" else 2
        macs = D * H + layers * 3 * H * 2 * H + H * nout # simple approximation
        f.write(f"// MACs per step: approx {macs}\n")
        # RAM: h state per layer (H floats) = layers * H * 4 bytes + inputs/outputs
        f.write(f"// RAM state bytes: {layers * H * 4}\n")

        f.write("\n#endif // NN_VEL_WEIGHTS_H\n")

    # Generate Golden vectors
    torch.manual_seed(42)
    seq_len = 200
    # Add a NaN and an empty block
    # golden inputs: 200 steps.
    golden_v = torch.randn(seq_len, 2)
    golden_x = torch.randn(seq_len, D)

    # Introduce NaN
    golden_x[50, 0] = float('nan')

    golden_x[100] = golden_x[99]
    golden_x_is_empty = [1 if i == 100 else 0 for i in range(seq_len)]

    golden_x_clean = golden_x.clone()
    golden_x_clean[torch.isnan(golden_x_clean)] = 0.0

    X = golden_x_clean.unsqueeze(0)
    V = golden_v.unsqueeze(0)

    with torch.no_grad():
        out = model(X, V)[0] # (T, 2)

    golden_h_path = out_path.parent / "tests" / "test_nn_vel_golden.h"
    golden_h_path.parent.mkdir(exist_ok=True, parents=True)
    with open(golden_h_path, "w") as f:
        f.write("#ifndef TEST_NN_VEL_GOLDEN_H\n")
        f.write("#define TEST_NN_VEL_GOLDEN_H\n\n")
        f.write("#include <math.h>\n\n")
        f.write(f"#define GOLDEN_STEPS {seq_len}\n\n")
        f.write(array_to_c_string("golden_x", golden_x.numpy()))
        f.write(array_to_c_string("golden_v", golden_v.numpy()))
        f.write(array_to_c_string("golden_out", out.numpy()))
        f.write(array_to_c_string("golden_is_empty", np.array(golden_x_is_empty, dtype=np.float32)))
        f.write("\n#endif\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ckpt")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    export_model(args.ckpt, args.output)
