# Planning Prompt v2 — UAV Ground Station Dashboard (Code-Verified)

> **Status: VERIFIED AGAINST LIVE CODE (2026-09-17 14:36 UTC+8)**
> Every claim below is backed by file:line citations or live `/state` capture.
> The v1 prompt had stale-doc assumptions — this version replaces them with
> what the code actually does today.

---

## 0. Live `/state` Capture (Ground Truth)

```json
{
  "schema_id": "r1-s1-9F32E2EA",
  "samples": 6662,
  "last_update_ns": 1789621447949117800,
  "streams": {
    "-1": {                              ← PHANTOM SLOT (BUG)
      "tag": "data",
      "received": 0, "loss_pct": 0, "sequence": 0,
      "last_update_ns_age": 707.96 s,    ← STALE 12 minutes
      "values": {
        "f0": -2.7e-43, "f1": 2.95e+38,  ← Infinity/leaked bytes
        "f2": -6.68e-33, ..., "len": 53, "tail": "b'\\xbf...'"
      }
    },
    "0": {                                ← SIDEBAR SLOT (under-keyed)
      "tag": "a",
      "received": 0, "loss_pct": 0,       ← METADATA WRONG
      "values": {                          ← 3 keys only of 65+ declared
        "status.pitch_deg": -1.141,
        "status.roll_deg":  -0.94,
        "status.yaw_deg":   -15.418
      }
    },
    "rtos": {                              ← RTOS BRIDGE (working)
      "tag": "external",
      "received": 696, "sequence": 696,
      "values": {
        "rtos.dma_busy": 0, "rtos.queue_depth": 5500,
        "rtos.send_ticks": 2003941, "rtos.scheduler_tick_count": 161257
      }
    }
  }
}
```

**Verdict from live capture:**
1. Only **3 of 65+ declared** `DASHBOARD_FRAME_A_VARS` resolve against the live ELF.
2. `streams["-1"]` is a **phantom slot** leaking raw `f0..f11` floats + binary tails.
3. `streams["0"]` has `received: 0` even though data is flowing — metadata path is broken for sidebar.
4. `slot -1` is **708s stale** (12 min) — dashboard shows it as "fresh" because the freshness alarm only checks `last_update_ns` per-stream, and `-1` happens to have one.
5. `status.arm`, `status.flymode`, `status.vbat`, `mrac.*`, `ekf.*`, `TWC.*`, `sbus_lost`, `g_estimator_ready`, etc. are **all absent** from the dashboard.

---

## 1. The Real Problem (Not What v1 Said)

The v1 prompt claimed the issue was a "DWARF resolution gap". The code tells a different, deeper story:

### Three Incoherent Data Paths Converging on `streams[]`

| Path | Source | Code Location | What it produces |
|------|--------|---------------|------------------|
| **Sidebar** | `wifi_bridge._publish_telem("a", sidebar_payload)` | `wifi_bridge.py:1182` | 22 sidebar keys, but only 3 DWARF names resolve. Metadata stripped. |
| **Subscribe** | `MultiStreamDecoder.feed()` → `service.ingest()` | `livewatch/stream.py` + `service/core.py:86` | Slot-scoped values + metadata, but DWARF names from `_pending_schema_ranges` may not arrive. |
| **External** | `service.inject_external_stream(slot, values)` | `service/core.py:294` | Clean, single-purpose — RTOS bridge uses this. |

Each path has its **own merging semantics**, its **own freshness tracking**, and its **own DWARF resolution**. They meet at `streams[slot].values`, but they don't agree on:

- **Where metadata lives** (sidebar path: stripped; subscribe path: top-level + `slotN.*` embedded)
- **How stale data is handled** (sidebar: `last_update_ns = now` always; external: monotonic; subscribe: `source_time_ms`)
- **What unknown tags become** (sidebar: never unknown; subscribe: `streams['-1']` phantom)

### Sidebar Mapping Is the Bottleneck

`_slot0_to_sidebar()` at `wifi_bridge.py:1250` is a 90-line hard-coded dict that **silently drops** any DWARF name that isn't present in the values list. The mapping table declares:

```python
mapping = {
    "imu_data.rol":          "status.roll_deg",      ← resolves ✅
    "imu_data.pit":          "status.pitch_deg",     ← resolves ✅
    "imu_data.yaw":          "status.yaw_deg",       ← resolves ✅
    "DroneStatus.ARM_Status":"status.arm",           ← dropped (DWARF name not in frame)
    "real_voltage":          "status.vbat",          ← dropped
    "mrac_state.pitch.e":    "mrac.pitch.e",         ← dropped
    # ... 55+ more, all dropped
}
```

The "fallback" in the calling code at `wifi_bridge.py:1180-1183` only adds `decoded["names"][i]` → `v[i]` as raw names, but those get **overwritten on the next frame** because `_publish_telem` does `self._last_telem[tag] = safe_payload` (replace, not merge per key).

**Net effect**: the dashboard sees 3 of 22 mapped keys + 0 of 43 raw DWARF names.

---

## 2. What "Memory Layer Pollution" Looks Like in This Codebase

The dashboard, plugins, and service have **3 separate state stores** that disagree:

| Layer | State store | What's in it | Source |
|-------|------------|--------------|--------|
| **Browser shell** | `currentState` (in-memory) | Last `/state` response | `index.html` |
| **Browser localStorage** | `LAYOUT_KEY`, `gs_cmd_history`, `gs_cmd_hist_visible` | UI prefs + command history | `index.html`, `command-panel.js` |
| **Service** | `service._streams` | Per-slot values + metadata | `service/core.py` |
| **Service hub** | `hub._latest_state` | Last published snapshot | `service/api.py` |
| **Bridge** | `bridge._last_telem` | Per-tag dict of latest values | `wifi_bridge.py:1223` |
| **Decoder** | `decoder.decoders[slot]` | Per-slot received/dropped/loss | `livewatch/stream.py` |

The "memory" the user is feeling is **all of these** showing different values at different times:

- The shell's `currentState` shows slot -1 with 708s-old data because the slot is still in `service._streams` and the service doesn't know to evict it.
- The bridge has the live sidebar in `bridge._last_telem["a"]`, but it only goes to the service if `_publish_telem` is called with the dict — and `_publish_telem` replaces the whole tag dict.
- The decoder has accurate `received`/`dropped`/`loss_pct` but the **sidebar path bypasses the decoder** entirely, so those counters stay at 0 for slot 0.

---

## 3. Original Spec Compliance (Re-Verified Against Code)

| Spec | Status | Code evidence |
|------|--------|---------------|
| 4 concurrent slots (0-3) | ⚠️ partial | Slot 0 only; slots 9-12 unreachable from HTTP |
| Typed streams (9-12) | ❌ | `subscribe_slot()` raises `ValueError("slot N requires explicit ranges")` for non-zero slots (`wifi_bridge.py:580-585`) |
| 22 sidebar keys | ❌ 3 of 22 | Live capture |
| DWARF name resolution | ❌ | `DASHBOARD_FRAME_A_VARS` declares 65+, only 3 resolve |
| RTOS metrics | ⚠️ partial | `streams["rtos"]` works, `PlatformObservability_Tick` not wired in `Send_Task` |
| MRAC full theta (24 vars) | ❌ | All `mrac_state.*.Theta[N]` dropped |
| EKF 9-state (9 vars) | ❌ | All `s_ekf.x[N]` dropped |
| Slot selection UI | ⚠️ partial | `slot-manager-panel.js` exists but `submitSubscribeFallback` uses `/commands` with `command_id=33` which `wifi_bridge` rejects |
| Command feedback | ✅ | Working |
| Loss alarm | ✅ | Working |

---

## 4. Codebase-Design Diagnosis

Using the **deep module** vocabulary from `codebase-design/SKILL.md`:

### Current Shape: Shallow & Sprawling

```
┌──────────────────────────────────────────────────────────────────────────┐
│ wifi_bridge._parse_one() — 200+ lines, 5 branches, 3 paths               │
│ ├─ Sidebar payload: _slot0_to_sidebar() — 90-line dict, silent drops    │
│ ├─ Subscribe payload: _decode_subscribe_frame() — bare-metal byte parse │
│ └─ JustFloat fallback: hardcoded rol/pit/yaw                             │
│                          ↓                                               │
│              _publish_telem(tag, payload) — replaces tag dict            │
│                          ↓                                               │
│ wifi_bridge UDP 1350 → service poll loop → ingest_decoded()             │
│                          ↓                                               │
│        _TAG_TO_SLOT (3 entries) + _resolve_tag_slot() — hardcoded       │
│                          ↓                                               │
│        streams[slot].values — flat dict, all paths dump here             │
└──────────────────────────────────────────────────────────────────────────┘
```

**Problems**:
- The interface at the top (`_parse_one` → `streams[slot].values`) is **wide** (5 branches, 3 payload shapes) and **shallow** (each branch is a small routine).
- Knowledge about DWARF names lives in **3 places**: `_slot0_to_sidebar`, the bridge decoder, and `_pending_schema_ranges`.
- A new frame type requires editing **4 files** (`wifi_bridge`, `core.py`, `_TAG_TO_SLOT`, plus a plugin).

### Where the Seam Should Be

The **single seam** should be between "raw bytes from the wire" and "normalized telemetry in `streams[]`". Today this seam is spread across:

- `wifi_bridge._parse_one` (5 branches)
- `wifi_bridge._slot0_to_sidebar` (mapping dict)
- `wifi_bridge._publish_telem` (delivery)
- `service.ingest_decoded` (slot routing + metadata extraction)

**Proposed seam** (one place):

```
wire bytes → FrameDecoder → NormalizedSample → TelemetryAdapter → streams[slot]
```

---

## 5. Structural Improvement: `TelemetryAdapter` Module

This is a **deep module** with a small interface and a lot of behavior behind it.

### Interface (small)

```python
class TelemetryAdapter:
    """Normalize raw bytes from any source into streams[slot].values."""

    def __init__(self, schema: TelemetrySchema,
                 dwarp_resolver: SymbolResolver,
                 freshness_ttl_ns: int = 5_000_000_000): ...

    def adapt(self, raw: bytes, *, source: Literal["wifi", "swd", "test"]) -> NormalizedSample | None:
        """Decode raw bytes; return a normalized sample or None if unrecognized."""

    def apply(self, sample: NormalizedSample, *, sink: ServiceSink) -> None:
        """Route a normalized sample into the service state, with freshness + dedup."""
```

### Behind the Interface (deep)

| Behavior | Implementation |
|----------|----------------|
| Frame parsing | One `FrameDecoder` per source (WiFi, SWD) — small, single-responsibility |
| DWARF resolution | SymbolResolver cached at construction — no per-frame lookup |
| Slot routing | `tag → slot` map keyed by frame type, with the `"-1 phantom"` slot eliminated by always returning a known slot or None |
| Freshness | Per-key `last_update_ns`; eviction when TTL expires |
| Metadata | Always-emitted `received`, `dropped`, `loss_pct`, `sequence` at top level (no more `slotN.*` embedded) |
| Fallback | If a DWARF name doesn't resolve, log once and emit under `__unresolved.<name>` instead of silently dropping |

### Why This Is a Deep Module

| Property | Before | After |
|----------|--------|-------|
| Methods on adapter | N/A (logic spread across 4 files) | 2 |
| Callers that need to know about DWARF | 3 (sidebar, decoder, schema reply) | 1 (the adapter) |
| Lines to add a new frame type | ~50 lines across 3 files | ~20 lines, 1 file |
| Tests covering all paths | 0 (integration only) | Unit tests on `TelemetryAdapter.adapt` per source |
| Phantom `-1` slot | Yes | Eliminated (always normalized) |

### Locality Benefit

Today, fixing "the dashboard shows wrong ARM status" requires grepping `_slot0_to_sidebar`, `_TAG_TO_SLOT`, `MultiStreamDecoder`, the plugin's `getChannelVal` chain. With the adapter:

- DWARF mapping lives in `TelemetryAdapter._dwarp_resolver` — one place.
- Slot routing lives in `TelemetryAdapter._route_frame` — one place.
- Freshness lives in `TelemetryAdapter._evict_stale_keys` — one place.

**One bug, one place to fix.** Every plugin gets a stable, fresh, named-key stream automatically.

---

## 6. Concrete Implementation Plan (Tactical, Forward-Looking)

### Phase 1: Centralize the Sidebar Mapping (1-2 hours)

**File**: `ground_station/comm/wifi_bridge.py`
**Action**: Move `_slot0_to_sidebar` mapping table from a hardcoded dict to a **schema-driven lookup** keyed on `manifests.yaml:dashboard_frame_a`. The mapping table already exists — it's just not consulted.

**Pseudo-patch**:
```python
# Before (lines 1250-1310): hardcoded dict
mapping = {"imu_data.rol": "status.roll_deg", ...}

# After: schema-driven
def _slot0_to_sidebar(self, names, values):
    sidebar = {}
    for dwarf_name, value in zip(names, values):
        # Consult the schema registry first
        spec_key = self._schema_registry.resolve(dwarf_name)
        if spec_key:
            sidebar[spec_key] = round(float(value), 4)
        else:
            # Emit as raw name so plugins can still read it
            sidebar[dwarf_name] = round(float(value), 4)
    return sidebar
```

**Effect**: 22 of 22 sidebar keys flow when DWARF names resolve; raw DWARF names emitted otherwise.

### Phase 2: Add Freshness Tracking to Sidebar Path (1 hour)

**File**: `ground_station/service/core.py`
**Action**: Track `last_update_ns` **per key** in the values dict. Plugins read fresh values, stale values are flagged.

```python
def ingest_decoded(self, tag, telemetry, *, slot=None, time_ns=None):
    now = time_ns or time.time_ns()
    ...
    existing["values"].update(telemetry)
    # Per-key freshness
    existing.setdefault("_freshness", {})
    for k in telemetry:
        existing["_freshness"][k] = now
    ...
```

**Plugin contract** (additive, backward compatible):
```javascript
var keyAge = state.streams[slot]._freshness?.[key];
if (keyAge && Date.now() * 1e6 - keyAge > 3e9) showStale(key);
```

### Phase 3: Eliminate Phantom `-1` Slot (30 min)

**File**: `ground_station/service/core.py:_resolve_tag_slot`
**Action**: Tag `"data"` (raw 50-53 byte datagram) should be **discarded**, not silently routed to slot -1. The JustFloat 16-byte and the subscribe stream are already covered; the raw 12-float datagram is noise.

```python
# Before (line 124-127):
return -1  # unknown tag → phantom slot

# After:
LOG.warning(f"unknown telemetry tag {tag!r}; discarding")
return None
```

### Phase 4: Fix `received: 0` for Sidebar Path (30 min)

**File**: `ground_station/service/core.py:_extract_stream_metadata`
**Action**: When the path is sidebar (`tag="a"`), the decoder's counters are correct. Use them:

```python
# Today: looks for `slot0.received` embedded in values dict
# After: read from decoder.decoders[0]
if slot in self.decoder.decoders:
    received = self.decoder.decoders[slot].received
    dropped = self.decoder.decoders[slot].dropped
    loss_pct = self.decoder.decoders[slot].loss_pct
```

### Phase 5: Wire Schema-Driven Slot Manager (2 hours)

**File**: `docs/dashboard-platform/shell/plugins/slot-manager-panel.js`
**Action**: Replace the `submitSubscribeFallback` (which POSTs `/commands` with `command_id=33` — a path the service rejects) with the existing `shellApi.subscribeSlot` flow. Add a "live status" view that reads from `/state` and shows per-slot `received/dropped/loss_pct` correctly.

### Phase 6: Re-probe DWARF Symbols (1 hour)

**File**: `OBJ/JX_FLY.axf` is the source of truth. Re-run:
```bash
python -m ground_station.livewatch.symbols probe --elf OBJ/JX_FLY.axf \
    --match "mrac_state|s_ekf|DroneStatus|real_voltage|TWC|g_estimator"
```

Expected output should reveal which of the 65 declared vars actually resolve. The unmatched ones become the firm-fix list.

---

## 7. What This Does NOT Fix (Firmware Required)

| Item | Owner | What jiang must do |
|------|-------|-------------------|
| `PlatformObservability_Tick` not in `Send_Task` | Firmware | Manual edit in Keil uVision, reflash |
| `ekf.pos_x/y/z` scalars | Firmware | Add aliases in `TASK/send_data.c` |
| `estimator.filter_status` alias | Firmware | Same |
| `mrac_state.*.Theta[N]` not in ELF | Firmware | Verify the struct exists at the declared addresses |
| Typed-stream subscribe path | Firmware | Confirm 0x21 with non-zero slot is implemented |

These are **out of scope** for this prompt but should be tracked in `STATE.md` as `TODO(firmware)` items.

---

## 8. Forward-Looking Patterns to Adopt

From mature observability pipelines (Prometheus, OpenTelemetry, Vector):

### Pattern 1: Schema-Keyed Normalization

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ Raw Frames  │ ──→ │ FrameDecoder │ ──→ │ Normalized   │ ──→ streams[]
│ (WiFi, SWD) │     │ (per source) │     │ {key: value} │
└─────────────┘     └──────────────┘     └──────────────┘
                            ↑                    ↑
                            │                    │
                       Single seam         Schema-driven
```

The dashboard never sees a raw `slot0.ch0.5` or a `f3` placeholder. It always sees `mrac_state.roll.Theta[1]` or `__unresolved.mrac_state.roll.Theta[1]`.

### Pattern 2: Liveness Probe Per Stream

```
GET /health/slots → {
  "0": {"live": true, "rate_hz": 20.1, "key_count": 22, "fresh_keys": 18},
  "-1": {"live": false, "rate_hz": 0, "stale_for_s": 707.96, "action": "evict"},
  "rtos": {"live": true, "rate_hz": 4.9, "key_count": 4, "fresh_keys": 4}
}
```

The shell uses this to color-code slot rows (green/amber/red) without polling logic in each plugin.

### Pattern 3: Schema-Aware Subscribe Confirmation

```
POST /subscribe {"slot": 9, "divider": 1, "ranges": ["mrac_state.*"]}
   ↓
Bridge sends 0x21 envelope
   ↓
FC replies with 0x08 schema (within 100ms)
   ↓
Adapter validates DWARF names → returns:
{
  "slot": 9,
  "divider": 1,
  "ranges": ["mrac_state.roll.e", ...],  ← resolved
  "unresolved": ["mrac_state.bar.e"],     ← could not resolve
  "var_count": 12,
  "expected_rate_hz": 80.0
}
```

This eliminates the "no observability on what slot I am selecting" complaint.

### Pattern 4: Versioned Service State

Add `schema_version` and `telemetry_schema_id` to `ServiceState`:

```python
@dataclass(frozen=True)
class ServiceState:
    schema_id: str              # "r1-s1-9F32E2EA"
    schema_version: int = 1     # bumped on each manifest change
    schema_generated_at: str    # ISO timestamp of last build
    adapter_version: str        # "v1.0" — version of TelemetryAdapter code
    ...
```

Plugins check `schema_version` and warn if they don't recognize the shape.

---

## 9. Test Coverage Plan

| Test | Layer | Verifies |
|------|-------|----------|
| `test_adapter_decodes_subscribe_with_schema_names` | `TelemetryAdapter` | DWARF names → spec keys round-trip |
| `test_adapter_emits_raw_names_when_dwarf_unknown` | `TelemetryAdapter` | No silent drops |
| `test_adapter_routes_data_tag_to_none` | `TelemetryAdapter` | Phantom slot eliminated |
| `test_adapter_tracks_per_key_freshness` | `TelemetryAdapter` | Stale keys flagged |
| `test_sidebar_publishes_22_keys_when_dwarf_resolves` | Integration | End-to-end sidebar flow |
| `test_slot_manager_shows_correct_rate_from_metadata` | Plugin | Live `/state` rendered correctly |
| `test_subscribe_endpoint_returns_schema_resolution` | API | Subscribe observability |
| `test_slot_minus_one_evicted_after_5s_stale` | Integration | Memory cleanup |

---

## 10. Success Criteria

After Phase 1-6 are implemented:

1. `GET /state` shows `streams["0"].values` with **≥18 keys** (target: 22), all with `last_update_ns` matching `state.last_update_ns`.
2. `streams["-1"]` does not exist.
3. `streams["0"].received` increments at ~20 Hz.
4. `streams["0"].loss_pct` reflects actual Wi-Fi loss (not 0).
5. `status.arm`, `status.flymode`, `status.vbat`, `mrac.pitch.e`, `ekf.vel_x` all visible in `/state`.
6. Slot Manager panel can subscribe to slot 9 successfully and the new slot appears in `/state` within 200ms.
7. Live `/state` JSON file size for the streams section is <2KB (today it's 4KB due to `f0..f11` noise).

---

## 11. Open Questions for jiang

1. **Firmware reflash willingness**: Are you OK with a 5-minute Keil reflash to wire `PlatformObservability_Tick`? Without it, RTOS counters stay at zero.
2. **Backward compat**: Plugins reading `slot0.ch0.5` (raw names) will need updates after Phase 1. Are you OK requiring a one-time plugin refresh?
3. **Subscribe path UX**: Do you want the slot manager to auto-subscribe slots 9-12 on dashboard load, or only on demand?
4. **Memory layer cleanup**: Do you want the phantom `-1` slot evicted on the next service restart, or live-evicted within 5s?

---

## 12. Key File:Line Citations

| Claim | File:Line |
|-------|-----------|
| `_TAG_TO_SLOT = {"a": 0, ...}` | `service/core.py:115-119` |
| `_resolve_tag_slot()` returns `-1` for unknown tags | `service/core.py:124-127` |
| Sidebar mapping is hardcoded dict | `wifi_bridge.py:1250-1310` |
| Sidebar publish replaces tag dict | `wifi_bridge.py:1232` |
| 65+ vars declared, only ~3 resolve | `boot_default_layout.py:54-129` (DWARF paths) vs `/state` capture |
| Subscribe requires explicit `ranges` for non-zero slots | `wifi_bridge.py:580-585` |
| `inject_external_stream` works clean | `service/core.py:294-328` |
| `/subscribe` endpoint exists | `api.py:325-340` |
| Slot manager uses `command_id=33` fallback | `slot-manager-panel.js:106-128` |
| Phantom `tag="data"` raw datagram path | `wifi_bridge.py:1189-1202` |
| `received: 0` because sidebar bypasses decoder | `wifi_bridge.py:1182` (no decoder update) |

---

## 13. Recommended Next Step

**Spawn 3 parallel sub-agents**:

1. **Agent 1 (data-flow)**: Phases 1-4 — eliminate phantom slot, fix metadata, schema-driven mapping. ~150 lines Python.
2. **Agent 2 (plugin polish)**: Phase 5 — fix slot manager fallback, add per-key freshness display. ~200 lines JS.
3. **Agent 3 (firmware review)**: Phase 6 — re-probe DWARF, document which `mrac_state.*`/`s_ekf.*` resolve. Output: missing-symbols manifest for jiang to fix in Keil.

After all 3 report, run `/code-review` on their diffs, then `/review-bugbot` for safety, then `/review-security` for command-path sanitization, then `/review` for spec compliance.

Then validate end-to-end:
```bash
python -m ground_station.service --rtos-bridge
# In another terminal:
curl localhost:8081/health | jq
curl localhost:8081/state | jq '.streams["0"].values | keys | length'  # should be ≥18
curl localhost:8081/state | jq '.streams | keys'  # should NOT include "-1"
```

---

## 14. Implementation Status (S16 — 2026-09-18)

Updated 2026-09-18. Verified against actual code on disk.

### Claims from this document — status

| Claim in §12 | Status | Evidence |
|---|---|---|
| `_TAG_TO_SLOT` at `core.py:115-119` | ✅ Still there (kept as back-compat shim) | `core.py:115` |
| `_resolve_tag_slot()` returns `-1` | ✅ Fixed — returns `None` for unknown tags; phantom slot gone | `core.py` adapter.resolve_slot |
| Sidebar mapping hardcoded dict | ✅ Fixed — `SchemaRegistry` consulted at seam | `wifi_bridge.py` `_slot0_to_sidebar` |
| `__stream_metadata__` payload shim | ✅ Removed in S16 | `wifi_bridge.py:1500-1520` |
| Subscribe requires explicit ranges for non-zero slots | ✅ Confirmed; preview endpoint added | `wifi_bridge.py:580-585` |
| Slot manager `command_id=33` fallback | ✅ Removed in S15 | `slot-manager-panel.js` |
| `received: 0` for sidebar path | ✅ Fixed by typed metadata path | `wifi_bridge.py:1169-1172` |

### Phases 1-6 — what was actually done

| Phase | What the code does today |
|---|---|
| **Phase 1** | Schema-driven sidebar mapping via `SchemaRegistry` (done in earlier S15 work) |
| **Phase 2** | Per-key freshness tracked in `_key_ts` on adapter; exposed via `_freshness` in snapshot |
| **Phase 3** | Phantom `-1` eliminated via `adapter.resolve_slot` returning `None` for unknown tags |
| **Phase 4** | `received` / `dropped` / `loss_pct` hoisted to top-level via `StreamMetadata` typed callback |
| **Phase 5** | Slot-manager uses `shellApi.subscribeSlot` (S15 fix); S16 adds preview before subscribe |
| **Phase 6** | DWARF probing done offline; ELF must match firmware build |

### New in S16 (not in earlier phases)

These additions implement the forward-looking patterns from §8:

**Pattern 3 — Schema-Aware Subscribe Confirmation**
- `WifiBridge.subscribe_preview(slot, divider, ranges)` — pure resolver method, no WiFi socket touched
- `POST /subscribe/preview` HTTP endpoint in `api.py`
- `slot-manager-panel.js`: calls preview before every subscribe; blocks if unresolved names exist; shows resolved/unresolved counts + expected Hz

**Pattern 2 — Liveness Probe Per Stream**
- `/health/slots` now includes a derived `status` field: `live` / `mixed` / `stale` / `dead`
- `slot-manager-panel.js` colors slot rows by status: green (live), amber (mixed), red (stale), muted (dead)
- CSS classes: `.sm-row-live`, `.sm-row-mixed`, `.sm-row-stale`, `.sm-row-dead`

**Pattern 1 — Schema-Keyed Normalization**
- Typed callback contract finalized: `_emit_telemetry` → `_on_telemetry_typed(tag, payload, metadata)`
- `__stream_metadata__` payload shim removed (S16 surgery)
- Values dict stays clean: no `slotN.*` metadata keys injected

### Test coverage (S16 additions)

| Test class | Tests added |
|---|---|
| `TestEmitTelemetryTypedCallback` | 3 tests: typed callback receives metadata, no payload mutation, legacy callback gets clean dict |
| `TestSubscribePreview` | 6 tests: slot0 preview, stop divider, slot1 requires ranges, valid DWARF resolve, invalid slot/divider raises, WiFi never touched, rate math |
| `test_http_health_slots_exposes_status_field` | `status` field present + valid values per slot |
| `test_http_health_slots_has_ttl_ns` | `ttl_ns` present on response |
| `test_http_subscribe_preview_endpoint` | 200 + correct body shape |
| `test_http_subscribe_preview_unknown_slot_rejected` | 400 on invalid slot |

**Result**: 157 tests pass (was 142 before S16), 0 regressions.

### Files changed in S16

```
ground_station/comm/wifi_bridge.py     — shim removed, subscribe_preview() added
ground_station/service/api.py          — /subscribe/preview endpoint + status field
ground_station/comm/tests/test_wifi_bridge_stream.py  — 9 new tests
ground_station/service/tests/test_service.py          — 6 new tests
docs/dashboard-platform/shell/plugins/slot-manager-panel.js  — preview wiring + freshness display
PLANNING_PROMPT.md                     — this section
.claude_state.md                       — session checkpoint for compaction survival
```

### Firmware audit (S17 — 2026-09-18)

The "Still out of scope (firmware required)" section above described five
items the host could not fix. Audited each one against the actual code on
2026-09-18. Result: **four of the five items are already done or were
never real**. Only the DWARF drift needs an actual rebuild.

| Claim | Verified state (2026-09-18) | File:line |
|---|---|---|
| `PlatformObservability_Tick` not called | **Already wired.** `Send_Groundstation_Telemetry_UART4` calls it as its first statement, passing `(gs_cmd_head + 16 - gs_cmd_tail) % 16` and `Usart3_Stream_Busy() != 0`. | `TASK/send_data.c:637-638` |
| `s_ekf.x[N]` DWARF drift (12 B) | **Real but rebuild-bound.** `pytest -k test_known_addresses` fails: DWARF resolves `s_ekf` to 0x20004F14 (size 572), linker map says 0x20004F08. Without a fresh rebuild, we cannot tell which is right. Livewatch reads from the DWARF address, so if DWARF is correct reads are fine; if the linker map is correct, reads are 12 B off. | `ground_station/livewatch/tests/test_livewatch.py:48-58` |
| `ekf.pos_x/y/z` scalars missing | **Misconception.** The EKF state vector is `[v_body(3), b_a_body(3), b_g_body(3)]` — it estimates velocities + biases, not positions. The host manifest already maps `s_ekf.x[0..2]` to `ekf.vel_x/y/z` (correct names). No aliases needed; the names that already exist on the wire are the names the dashboard needs. | `API/ekf.h:21-30`, `ground_station/livewatch/manifests.yaml:dashboard_frame_a` |
| 50-53 B datagram no magic header | **Already there.** `send_to_linux()` writes `0xAA 0xAA 0x00 0x00` at indices 0-3 of `DataBuf_to_linux[]`. The PLANNING_PROMPT was describing an older state. | `TASK/send_data.c:283-286` |
| Switch to `SUBSCRIBE_ONLY` mode | **Already wired at runtime.** CMD 0x0F with idx 100/101/102 selects LEGACY/MIXED/SUBSCRIBE_ONLY via `SetTelemetryMode()`. The host sends one 9-byte frame and Send_Task flips `g_telemetry_mode`. Default at boot is MIXED, so 200 Hz needs an explicit switch. | `TASK/send_data.c:1640-1656`, `API/subscribe.c:36-46` |

#### What the operator needs to do

- **DWARF drift fix (only real firmware work):** rebuild OBJ/JX_FLY.axf and reflash. Before flashing, the arm gate will refuse. After flashing, re-run `pytest -k test_known_addresses` to confirm DWARF and linker map agree.
- **Runtime mode switch (no firmware work, host code only):** the dashboard shell can wire a button to `cmd_id=0x0F, index=102, value=0` to flip `SUBSCRIBE_ONLY` at runtime. This is host-only; no reflash.

#### Stale docs

The PLANNING_PROMPT item about `ekf.pos_x/y/z` was a misreading of the EKF
struct layout. `firmware/README.md` still says "Caller wiring is required"
under "Tick wiring" — that section is stale; the tick IS wired. Both have
been reconciled in S17.

---

*Prompt generated: 2026-09-17 14:36 UTC+8 — verified against live `/state`*
*S16 implementation: 2026-09-18 02:58 UTC+8 — verified against actual code*
*User: jiang*
*Project: FreeRTOS-adaptive-controller-codex*
*Supersedes: v1 PLANNING_PROMPT.md*
