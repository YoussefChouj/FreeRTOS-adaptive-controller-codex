"""Load docs/agent-map/modules.yaml (the only human-authored input)."""
from __future__ import annotations

from pathlib import Path

import yaml

from .paths import MODULES_YAML


class Modules:
    """Maps a firmware file to {module, tier}.

    Built from modules.yaml. A DRAFT file (``confirmed: false``) loads fine but
    carries the draft flag so callers — and the honesty rules — know the tiers
    have not been operator-confirmed.
    """

    def __init__(self, path: Path = MODULES_YAML):
        self.path = Path(path)
        self.confirmed: bool = False
        self.draft: bool = True
        self._by_file: dict[str, dict] = {}
        self._by_symbol: dict[str, int] = {}
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.confirmed = bool(raw.get("confirmed"))
        self.draft = bool(raw.get("draft", not self.confirmed))
        for m in raw.get("modules") or []:
            p = (m.get("path") or "").replace("\\", "/").lstrip("/")
            self._by_file[p] = {
                "module": m.get("module"),
                "tier": m.get("tier"),
            }
        for sym in raw.get("symbols") or []:
            if sym.get("name") is not None:
                self._by_symbol[str(sym["name"])] = sym.get("tier")

    def lookup(self, rel_path: str) -> dict:
        """Best module/tier match for a repo-relative path.

        ``ground_station/**`` is matched with a prefix wildcard so an arbitrary
        symbol file in the tooling tree still lands on tier 2.
        """
        norm = rel_path.replace("\\", "/").lstrip("/")
        if norm in self._by_file:
            return dict(self._by_file[norm])
        # longest prefix match (directories + trailing /**, exact file match
        # already handled above).
        best: tuple[int, dict] | None = None
        for p, info in self._by_file.items():
            if p.endswith("/**"):
                prefix = p[:-3]
                if norm.startswith(prefix):
                    n = len(prefix)
                    if best is None or n > best[0]:
                        best = (n, info)
        return dict(best[1]) if best else {}

    def symbol_tier(self, name: str, file_tier):
        """Per-symbol override (``symbols:`` in the yaml), else the file tier."""
        return self._by_symbol.get(name, file_tier)

    def file_meta(self, rel_path: str) -> dict:
        result = self.lookup(rel_path)
        return {"module": result.get("module"), "tier": result.get("tier")}