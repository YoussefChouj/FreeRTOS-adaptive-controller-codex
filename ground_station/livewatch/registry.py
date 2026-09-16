"""Loader for the standard watch registry (registry.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

_DEFAULT = Path(__file__).with_name("registry.yaml")


class Registry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _DEFAULT
        with open(self.path) as f:
            self._data = yaml.safe_load(f) or {}
        self._groups = self._data.get("groups", {})

    def group_names(self) -> list[str]:
        return sorted(self._groups)

    def vars(self, group: str) -> list[str]:
        if group not in self._groups:
            raise KeyError(f"no watch group {group!r}; have {self.group_names()}")
        return list(self._groups[group].get("vars", []))

    def doc(self, group: str) -> str:
        return self._groups.get(group, {}).get("doc", "")

    def expand(self, tokens: list[str]) -> list[str]:
        """Expand any 'group:<name>' tokens into their vars; pass through plain paths."""
        out: list[str] = []
        for t in tokens:
            if t.startswith("group:"):
                out.extend(self.vars(t[len("group:"):]))
            else:
                out.append(t)
        return out
