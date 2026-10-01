import os
import yaml
import json
import re
import hashlib
from dataclasses import dataclass

@dataclass(frozen=True)
class ProtectedSet:
    paths: list[str]
    regions: list[str]
    functions: list[str]
    param_ids: list[str]
    unresolved: list[str]

DEFAULT_PROTECTED_SET = os.path.join(os.path.dirname(__file__), "protected_set.yaml")

def load_protected_set(path=DEFAULT_PROTECTED_SET) -> ProtectedSet:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ProtectedSet(
        paths=data.get("paths", []),
        regions=data.get("regions", []),
        functions=data.get("functions", []),
        param_ids=data.get("param_ids", []),
        unresolved=data.get("unresolved", []),
    )

@dataclass
class GateResult:
    ok: bool
    step: int | None
    reasons: list[str]
    lkg_hash: str | None = None

class CodeGate:
    def __init__(self, *, build, ram_check, ledger_path, clock, obj_dir,
                 ram_limit_bytes, tolerance_frac, sil=None, custody=None, protected=None, lkg_j=None):
        self.build = build
        self.ram_check = ram_check
        self.is_default_sil = (sil is None)
        self.sil = sil if sil is not None else (lambda c_files: (False, 0.0))
        
        if custody is None:
            import ground_station.flashtool.artifact_custody as artifact_custody
            self.custody = artifact_custody
        else:
            self.custody = custody
            
        self.ledger_path = ledger_path
        self.clock = clock
        self.obj_dir = obj_dir
        self.ram_limit_bytes = ram_limit_bytes
        self.tolerance_frac = tolerance_frac
        self.protected = protected if protected is not None else load_protected_set()
        self.lkg_j = lkg_j
        
        self.pending_change = False
        self.pending_j = None
        self.flights_since_change = 0

    def _log(self, record: dict):
        record["ts"] = self.clock()
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _worse(self, j) -> bool:
        return self.lkg_j is not None and j is not None and j > self.lkg_j * (1 + self.tolerance_frac)

    def check_change(self, diff_text: str, justification: dict, files_after: dict[str, str]) -> GateResult:
        if self.pending_change and self.flights_since_change == 0:
            return GateResult(ok=False, step=6, reasons=["A change is already pending and no flight was recorded since."])

        hunks = self._parse_diff(diff_text)
        paths_touched = set()
        for h in hunks:
            if h[0]:
                paths_touched.add(h[0])
            if h[1]:
                paths_touched.add(h[1])
        
        for pre_path, post_path, h_start, h_len, added_lines, removed_lines in hunks:
            if pre_path in self.protected.paths or post_path in self.protected.paths:
                return GateResult(ok=False, step=1, reasons=[f"Touched protected path: {pre_path or post_path}"])
            
            if post_path and (post_path.endswith(".c") or post_path.endswith(".h")) and post_path not in files_after:
                return GateResult(ok=False, step=1, reasons=[f"Missing file text for {post_path}"])
            
            if post_path and post_path in files_after:
                file_text = files_after[post_path]
                for reg in self.protected.regions:
                    if self._overlaps_region(file_text, reg, h_start, h_len):
                        return GateResult(ok=False, step=1, reasons=[f"Hunk overlaps protected region {reg} in {post_path}"])
                for func in self.protected.functions:
                    if self._overlaps_function(file_text, func, h_start, h_len):
                        return GateResult(ok=False, step=1, reasons=[f"Hunk overlaps protected function {func} in {post_path}"])
            
            for line in added_lines + removed_lines:
                if "PROTECTED BEGIN" in line or "PROTECTED END" in line:
                    return GateResult(ok=False, step=1, reasons=["Touched PROTECTED marker"])
                for param in self.protected.param_ids:
                    if re.search(r'\b' + re.escape(param) + r'\b', line):
                        return GateResult(ok=False, step=1, reasons=[f"Touched param ID {param}"])

        if not justification.get("argument") or not justification.get("predicted_effect") or not justification.get("metric"):
            return GateResult(ok=False, step=2, reasons=["Missing or blank justification fields"])
        
        self._log({"event": "justification", "justification": justification})

        if not self.custody.has_snapshot(self.obj_dir):
            self.custody.snapshot(self.obj_dir)

        error_count, map_path = self.build()
        if error_count != 0:
            return GateResult(ok=False, step=3, reasons=[f"Build failed with {error_count} errors"])
        
        used_ram = self.ram_check(map_path)
        if used_ram > self.ram_limit_bytes:
            return GateResult(ok=False, step=3, reasons=[f"RAM usage {used_ram} exceeds limit {self.ram_limit_bytes}"])

        changed_c_files = [p for p in paths_touched if p and p.endswith(".c")]
        stable, j = self.sil(changed_c_files)
        if self.is_default_sil:
            return GateResult(ok=False, step=4, reasons=["SIL hook not wired"])
        if not stable:
            return GateResult(ok=False, step=4, reasons=["SIL check failed: not stable"])
        if self._worse(j):
            return GateResult(ok=False, step=4, reasons=[f"SIL check failed: j {j} > lkg_j {self.lkg_j} * (1 + {self.tolerance_frac})"])
        
        if not self.custody.has_snapshot(self.obj_dir):
            return GateResult(ok=False, step=5, reasons=["Custody snapshot failed"])

        cache_dir = self.custody.cache_dir(self.obj_dir)
        hex_path = os.path.join(cache_dir, "JX_FLY.hex")
        if not os.path.exists(hex_path):
            return GateResult(ok=False, step=5, reasons=["LKG hex file missing"])
        
        with open(hex_path, "rb") as f:
            lkg_hash = hashlib.sha256(f.read()).hexdigest()
        
        self._log({"event": "lkg", "hash": lkg_hash})
        
        self.pending_change = True
        self.pending_j = j
        self.flights_since_change = 0

        return GateResult(ok=True, step=None, reasons=[], lkg_hash=lkg_hash)

    def next_flight_must_hover(self) -> bool:
        return self.pending_change and self.flights_since_change == 0

    def on_flight_result(self, aborted: bool, j: float | None) -> str:
        revert = False
        if aborted:
            revert = True
        elif self._worse(j):
            revert = True
            
        decision = "revert" if revert else "keep"
        if revert:
            self.custody.restore(self.obj_dir)
        else:
            self.custody.commit(self.obj_dir)
            if j is not None:
                self.lkg_j = j
                
        self.pending_change = False
        
        self._log({"event": "flight_result", "decision": decision})
            
        return decision

    def record_flight(self, flight_id: str, fw_hash: str):
        if self.pending_change:
            self.flights_since_change += 1
        self._log({"event": "flight", "flight_id": flight_id, "fw_hash": fw_hash})

    def _parse_diff(self, diff_text: str):
        hunks = []
        pre_path = None
        post_path = None
        lines = diff_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("--- "):
                p = line[4:].strip()
                pre_path = None if p == "/dev/null" else (p[2:] if p.startswith("a/") else p)
                i += 1
                continue
            if line.startswith("+++ "):
                p = line[4:].strip()
                post_path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
                i += 1
                continue
            
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m and (pre_path or post_path):
                h_start = int(m.group(2))
                h_len = int(m.group(3)) if m.group(3) is not None else 1
                if h_len == 0:
                    h_len = 1
                added = []
                removed = []
                i += 1
                while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("--- "):
                    if lines[i].startswith("+") and not lines[i].startswith("+++"):
                        added.append(lines[i][1:])
                    elif lines[i].startswith("-") and not lines[i].startswith("---"):
                        removed.append(lines[i][1:])
                    i += 1
                hunks.append((pre_path, post_path, h_start, h_len, added, removed))
                continue
                
            i += 1
            
        return hunks

    def _overlaps_region(self, file_text: str, region_name: str, h_start: int, h_len: int) -> bool:
        lines = file_text.splitlines()
        r_start = -1
        r_end = -1
        for i, line in enumerate(lines):
            if f"PROTECTED BEGIN {region_name}" in line:
                r_start = i + 1
            elif f"PROTECTED END {region_name}" in line and r_start != -1:
                r_end = i + 1
                break
        
        if r_start != -1 and r_end != -1:
            h_end = h_start + h_len - 1
            return max(r_start, h_start) <= min(r_end, h_end)
        return False

    def _overlaps_function(self, file_text: str, func_name: str, h_start: int, h_len: int) -> bool:
        lines = file_text.splitlines()
        f_start = -1
        f_end = -1
        brace_count = 0
        in_func = False
        started_braces = False
        
        i = 0
        while i < len(lines):
            line = lines[i]
            if not in_func:
                m = re.search(r'\b' + re.escape(func_name) + r'\s*\(', line)
                if m:
                    j = i
                    text_ahead = line[m.end():]
                    found_open = False
                    while j < len(lines):
                        if '{' in text_ahead or ';' in text_ahead:
                            if next(c for c in text_ahead if c in '{;') == '{':
                                found_open = True
                            break
                        j += 1
                        if j < len(lines):
                            text_ahead += lines[j]
                    if found_open:
                        f_start = i + 1
                        in_func = True
            
            if in_func:
                brace_count += line.count('{')
                if '{' in line:
                    started_braces = True
                brace_count -= line.count('}')
                
                if started_braces and brace_count <= 0:
                    f_end = i + 1
                    break
            i += 1
                    
        if f_start != -1 and f_end != -1:
            h_end = h_start + h_len - 1
            return max(f_start, h_start) <= min(f_end, h_end)
        return False
