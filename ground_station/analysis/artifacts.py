"""Artifact indexing and storage for experiment runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def index_artifacts(root: Path = Path("ground_station/results")) -> list[dict]:
    """Index all JSON result files under root.

    Returns: [{path, session_id, schema_id, created, metrics: {...}}, ...]
    """
    index: list[dict] = []
    if not root.exists():
        return index
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record: dict[str, object] = {
                "path": str(path),
                "session_id": data.get("session_id", ""),
                "schema_id": data.get("schema_id", ""),
                "created": data.get("created", ""),
                "metrics": data.get("metrics", {}),
                "fingerprint": compute_fingerprint(data),
            }
            index.append(record)
        except Exception:
            # Skip files that can't be parsed as JSON
            continue
    return index


def compute_fingerprint(data: dict) -> str:
    """SHA-256 hex digest of canonical JSON of data."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


def find_similar(root: Path = Path("ground_station/results"),
                 threshold: float = 0.95) -> list[tuple[str, str, float]]:
    """Find pairs of result files with similar fingerprints.

    Returns: [(path_a, path_b, similarity_score), ...]
    """
    artifacts = index_artifacts(root)
    pairs: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(artifacts):
        fp_a = a.get("fingerprint", "")
        for b in artifacts[i + 1:]:
            fp_b = b.get("fingerprint", "")
            if not fp_a or not fp_b:
                continue
            # Similarity = fraction of matching bytes in the fingerprint
            # i.e., count matching nibbles / total nibbles
            matches = sum(1 for x, y in zip(fp_a, fp_b) if x == y)
            similarity = matches / max(len(fp_a), len(fp_b), 1)
            key = (a["path"], b["path"])
            rev = (b["path"], a["path"])
            if similarity >= threshold and key not in seen and rev not in seen:
                seen.add(key)
                pairs.append((a["path"], b["path"], round(similarity, 4)))
    return pairs
