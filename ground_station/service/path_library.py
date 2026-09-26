from __future__ import annotations

import json
import uuid
from pathlib import Path

# The schema definition for a path
# {
#   "id": "uuid",
#   "name": "string",
#   "created_at": 1234567890,
#   "points": [{"x": float, "y": float, "z": float}],
#   "type": "custom",
#   "spacing": 5.0
# }

PATHS_DIR = Path("logs/paths")

def _init_dir() -> None:
    PATHS_DIR.mkdir(parents=True, exist_ok=True)

def list_paths() -> list[dict]:
    _init_dir()
    paths = []
    for f in PATHS_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as file:
                paths.append(json.load(file))
        except Exception:
            pass
    return sorted(paths, key=lambda x: x.get("created_at", 0), reverse=True)

def get_path(path_id: str) -> dict | None:
    _init_dir()
    f = PATHS_DIR / f"{path_id}.json"
    if not f.exists():
        return None
    try:
        with open(f, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return None

def save_path(path_data: dict) -> dict:
    _init_dir()
    if "id" not in path_data or not path_data["id"]:
        path_data["id"] = str(uuid.uuid4())
    import time
    if "created_at" not in path_data:
        path_data["created_at"] = time.time()
    
    f = PATHS_DIR / f"{path_data['id']}.json"
    with open(f, "w", encoding="utf-8") as file:
        json.dump(path_data, file, indent=2)
    
    return path_data

def delete_path(path_id: str) -> bool:
    _init_dir()
    f = PATHS_DIR / f"{path_id}.json"
    if f.exists():
        f.unlink()
        return True
    return False

def resample_path(points: list[dict], spacing: float) -> list[dict]:
    """Resample a path to have points roughly `spacing` meters apart."""
    if len(points) < 2:
        return points
    
    import math
    out = [points[0]]
    for i in range(1, len(points)):
        p1 = out[-1]
        p2 = points[i]
        dx = p2["x"] - p1["x"]
        dy = p2["y"] - p1["y"]
        dz = p2.get("z", 0) - p1.get("z", 0)
        dist = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        while dist > spacing:
            t = spacing / dist
            nx = p1["x"] + dx * t
            ny = p1["y"] + dy * t
            nz = p1.get("z", 0) + dz * t
            new_pt = {"x": nx, "y": ny, "z": nz}
            out.append(new_pt)
            p1 = new_pt
            dx = p2["x"] - p1["x"]
            dy = p2["y"] - p1["y"]
            dz = p2.get("z", 0) - p1.get("z", 0)
            dist = math.sqrt(dx*dx + dy*dy + dz*dz)
            
        if dist > spacing * 0.5:
             out.append(p2)
             
    # Ensure last point is always included
    if out[-1] != points[-1]:
        out.append(points[-1])
        
    return out

def smooth_path(points: list[dict], iterations: int = 1, alpha: float = 0.5) -> list[dict]:
    """Simple Laplacian smoothing for the path."""
    if len(points) < 3:
        return points
        
    out = [dict(p) for p in points]
    for _ in range(iterations):
        temp = [dict(p) for p in out]
        for i in range(1, len(out) - 1):
            temp[i]["x"] = out[i]["x"] * (1 - alpha) + (out[i-1]["x"] + out[i+1]["x"]) / 2 * alpha
            temp[i]["y"] = out[i]["y"] * (1 - alpha) + (out[i-1]["y"] + out[i+1]["y"]) / 2 * alpha
            temp[i]["z"] = out[i].get("z", 0) * (1 - alpha) + (out[i-1].get("z", 0) + out[i+1].get("z", 0)) / 2 * alpha
        out = temp
    return out
