# Seam design review — firmware and ground station

**2026-09-23.** Written against `90f06ca`, with the drone powered and connected.
Tree at time of writing, all re-run after the last commit: **870 passed, 27 skipped,
0 failed** (123 s); **27/27** node DOM harnesses; `browser_smoke` 11 tabs, ERRORS 0 /
BAD RESPONSES 0, `nan=0` on every tab.

(`.claude_state.md` said 24/24 node harnesses. There are 27. The number was carried
forward rather than re-counted -- which is the same failure mode as section 3, one
directory over.)

This is a design review, not a bug list. It uses the vocabulary deliberately: a
**module** is anything with an interface and an implementation; its **interface** is
everything a caller must know to use it correctly — not just the signature, but
lifecycle, ordering, error modes and configuration; **depth** is how much behaviour a
caller gets per unit of interface they must learn; a **seam** is where that interface
lives.

Every claim below is something I measured this session. Where I am proposing rather
than reporting, it says so.

---

## The headline

**The firmware has better seam discipline than the ground station's HTTP layer.**

That is not the result I expected to write. The C is constrained to ARMCC V5.06, no
C99, declarations at block top — and it has a 946-line module behind twelve functions,
compiled and exercised on the host in the ordinary `pytest` run. The Python service is
2150 lines behind **two** methods, and its route declaration had silently diverged from
its own dispatch.

The rest of this document is the evidence, and what to do about it.

---

## 1. `API/subscribe.c` — a deep module, and why it works

946 lines of implementation. Twelve public functions in `subscribe.h`:

```
Subscribe_ParseRequest        Subscribe_BuildReply       Subscribe_BuildError
Subscribe_ParseStreamRequest  Subscribe_BuildSchema      Subscribe_BuildStreamFrame
Subscribe_ValidateTuple       Subscribe_ValidateRange    Subscribe_StreamBps
Subscribe_StreamTick          Subscribe_StreamOwnsUsart3 Subscribe_TimeMs
```

Nine of those are pure: bytes in, bytes out, no hardware. `Subscribe_StreamTick` is the
one driver. That split is the seam, and it is load-bearing:
`ground_station/livewatch/tests/test_subscribe_c.py` compiles **the real
`API/subscribe.c`** with `-m32` and a widened allowlist and runs it on the host. Two
tests, 1.4 s, green in the normal tree.

The harness comment states the point better than I can:

> The Python tests assert a byte layout; this asserts that the C which will actually
> run on the drone produces it — and that `Subscribe_StreamTick`'s round-robin really
> does keep a fast slot from starving a slow one.

> every subscription here goes through the REAL parser rather than being poked into
> the slot table.

That last line is the discipline. The test crosses the same seam the firmware crosses.
It does not reach past the interface to arrange state.

**Depth check.** Apply the deletion test: delete `subscribe.c` and the complexity does
not vanish, it reappears — frame layout, CRC, allowlist validation, link-budget
arithmetic and round-robin fairness would have to be restated at every call site. It
earns its keep.

### The transport arm inside it is sound — audited, no change made

`Subscribe_TxSend` dispatch is gated by `SUBSCRIBE_UART5_ENABLED`, which is never
`#define`d in the production build. It gates exactly three places, consistently:

| Site | Behaviour when disabled |
| --- | --- |
| `API/subscribe.c:532` | subscribe-time refusal — a UART5 request is rejected, not silently accepted |
| `BSP/usart5.c:160/180` | two definitions of `Uart5_Subscribe_TxSend`; the shipping build links the `{(void)buf; (void)len;}` no-op |
| `BSP/usart5.c:480` | `Handle_UART5_GroundStation_Command` compiled out |

I spent real time trying to turn this into a bug. `TASK/send_data.c:1383` routes
`SendTransactionResult` to the no-op in its else branch, which looks exactly like a
silent data-loss path. It is not: both sites that set `transaction_transport = 0U`
(`BSP/usart4.c:143`, `BSP/usart5.c:380`) also set `transaction_id = 0U`, and the
function returns early on that. The refusal at the subscribe site means the no-op is
unreachable for stream traffic.

And the reply path is correct under concurrency: `BSP/usart5.c:336` latches
`UA5RxSubscribeTransport = Subscribe_RxTransport` inside `__disable_irq()`, which is
what guarantees a reply leaves on the link its request arrived on.

**Verdict: a deliberately dormant but fully implemented adapter behind one flag.** This
is what a second adapter at a seam is supposed to look like. Recorded as sound; nothing
changed.

**Forward-looking.** If UART5 subscribe is ever brought live, the work is not writing
the adapter — it exists. It is: define the flag, then extend
`test_subscribe_c.py` to compile the harness a second time with
`-DSUBSCRIBE_UART5_ENABLED=1` and assert the refusal at `subscribe.c:532` flips to an
acceptance. One extra compile in a test that already exists. That is the dividend of
having put the seam in the right place.

---

## 2. `ground_station/livewatch/transport.py` — the seam was right, the lifecycle was not

Fixed this session (`cafd660`). Recorded here because the lesson generalises.

`LiveTransport` declares a lifecycle: construction is cheap, I/O begins at `connect()`.
`SwdCmsisDap` and `Uart5LongRange` honoured it. `Usart3LongRange` inverted it — its
`__init__` built a `UdpDataPort`, which **bound UDP 14550 and transmitted the nudge
datagram to the drone**, while its `connect()` was `return self`.

The interface was fine. The adapter contradicted it. And because an interface includes
lifecycle, that contradiction is an interface violation even though every type checked.

The cost was not theoretical:

- A test whose entire claim was *"the CLI picks this class"* transmitted to the flight
  controller, and failed on any machine actually flying, because the live
  `wifi_bridge` already held 14550.
- The workaround — patching `socket.socket` at module import — leaked across the whole
  single-process pytest session and **broke 68 tests in unrelated files**.

The fix was not new machinery. `Uart5LongRange` already had the pattern: a
`serial_factory` constructor parameter. Adding `udp_factory` made the seam reachable
from the outside, and three subscribe tests each shed six lines of setup that existed
only to undo what `__init__` had already done.

It is pinned by a parametrized contract test over all three adapters, so the next
transport is covered by adding one line:

```python
@pytest.mark.parametrize("build", [
    lambda: SwdCmsisDap(),
    lambda: Uart5LongRange("COM42"),
    lambda: Usart3WifiSubscribeTransport(14550, "192.168.4.1"),
], ids=["swd", "uart5", "wifi"])
def test_constructing_a_transport_touches_no_hardware(build, monkeypatch):
```

**Generalised lesson.** When an ABC states a lifecycle, nothing enforces it. If one
adapter inverts it, the seam becomes uncrossable for callers *and* tests, and the
workaround lands in the test infrastructure rather than in the offending adapter —
which is why the damage surfaced three directories away from its cause.

---

## 3. `ground_station/service/api.py` — the shallow module

This is the finding that matters most, and the one I am not fixing in place today.

| Measure | Value |
| --- | --- |
| File | 2150 lines |
| Handler methods | **2** (`do_GET`, `do_POST`) |
| `do_GET` | lines 1016–1650 = **635 lines**, ~40 chained `elif route == ...` branches |
| `do_POST` | lines 1652–2036 = **385 lines**, ~19 branches |
| Declared routes | 37 GET, 17 POST = **54** |
| `self.rfile.read` call sites | 14 |

That is a **large interface over an implementation with no internal seam**. 54
distinct behaviours, each reachable only by falling through a linear chain, with every
handler body inlined. It is the textbook shallow shape: the interface is as complex as
the implementation, and callers get no leverage from it.

The point is not aesthetic. It has cost real defects this session and last:

**(a) The declaration drifted from the dispatch.** `_ROUTE_MAP` is a hand-maintained
parallel list; its own comment says *"Keep in sync with the dispatch in do_GET/do_POST."*
That instruction is the defect. `GET /api/agent/stream` (server-sent events) and
`GET /api/agent/messages/wait` (long-poll) were dispatched but undeclared — the
service's **entire push and long-poll surface** was missing from `/api/routes`, which is
precisely what `AGENT_GUIDE.md` tells an agent to read to discover the service. An agent
reading the map would conclude no push channel existed.

The existing test walked *declared → answers*. That direction catches a route you
documented but never built; it is blind to a route you built but never documented. And
both missing routes block, so the answers-walk could never have reached them even in
principle. Fixed in `90f06ca` by reading the dispatch out of the AST — a second
hand-written list would drift exactly the way the first one did.

Note what was *not* broken: the manifest chain downstream of `_ROUTE_MAP` is sound.
`check_manifest_drift` caught the regeneration correctly the moment the map changed.
The single unguarded link was dispatch → declaration.

**(b) Cross-cutting fixes have nowhere to go but the top of the chain.** 13 POST routes
each read an uncapped `Content-Length`. The fix had to be a `_content_length()` helper
plus a central 413/400 in `do_POST`, because there is no per-route seam to put it
behind. Same story for `_json_safe` (NaN/Infinity make `json.dumps` emit bare tokens
that `JSON.parse` rejects — every server-side test passes while the browser sees an
unparseable document): it had to be threaded through `_json` by hand.

**(c) The 413-without-drain bug.** `/api/agent/plans/<id>/cancel` answered 423 without
draining the body, producing `WinError 10053`. A per-route seam with a uniform
request-object contract makes that class of bug unrepresentable; a 635-line chain makes
it a per-branch judgement call, 54 times.

### The shape I would move it to

*Proposal, not yet implemented.* Make the declaration **be** the dispatch:

```python
ROUTES = {
    ("GET", "/health"): Route(
        handler=_health,
        doc="service liveness + bridge connection"),
    ("POST", "/api/agent/plans/<id>/cancel"): Route(
        handler=_cancel_plan, doc="...", body=Json(max_bytes=1 << 20)),
}
```

`do_GET`/`do_POST` become a lookup plus a uniform pre/post: resolve, drain-and-cap the
body once, dispatch, serialise with `_json_safe` once. `/api/routes` is then generated
from the same structure it dispatches on, and drift stops being *checkable* and starts
being *impossible* — which is strictly better than the AST test I just added. That test
is a guard rail on a road that should not have a cliff.

What this buys, in the vocabulary: `api.py` goes from one shallow module to a thin
dispatcher plus 54 small deep ones. **Leverage** — a new route declares its body
contract instead of re-implementing it. **Locality** — the Content-Length cap, the
drain rule and the NaN guard each live in exactly one place instead of being a rule 54
authors must remember.

### Why not today

Three reasons, in order of weight:

1. **The drone is powered and the service on 8081 is live** with a session holding
   ~512 k samples. A 2150-line restructure is not something to land against running
   hardware.
2. **The running service is already 3 commits behind** (`702cd47` vs HEAD). Adding a
   large refactor widens a gap the operator has to close.
3. The tests cross the seam at HTTP, not at the handler, so they would survive the
   refactor unchanged — which is good news for doing it later, and removes any urgency
   to do it now.

One caveat on the evidence above: `browser_smoke` runs against the **live 8081
service**, which is 3 commits behind. It therefore confirms the shell and the running
service are healthy; it does not exercise the route declaration fixed in `90f06ca`.
That is covered by the python tree, which runs its own `ApiServer`.

**Migration path.** Incremental, one route family at a time: stand up `ROUTES` beside
the chain, move `/api/agent/*` first (13 routes, best-tested, most agent-facing), let
the chain fall through to the table, and delete branches as they move. The AST drift
test keeps the map honest throughout, and flips from a guard to a tautology as the last
family lands — at which point delete it.

---

## 4. What I deliberately did not do

- **Did not restart the 8081 service.** It is 3 commits behind and its only functional
  gap is the NaN guard — and I proved that guard is not firing right now by regexing
  the raw text of `/state`, `/api/view-model`, `/health` and `/slots` for
  `NaN|-?Infinity`: **zero tokens in all four**, and `browser_smoke` independently
  reports `nan=0` on all 11 tabs. The `NOT PUBLISHED` sidebar values are
  simply keys the current 3-key typed slot does not carry, not a serialisation failure.
  Restarting would kill a live session against a powered drone to fix a bug that is not
  occurring. Operator's call; flagged in `.claude_state.md`.
- **Did not touch `s_ekf`.** Shadow mode, unchanged.
- **Did not manufacture a firmware finding** out of the `Uart5_Subscribe_TxSend` no-op,
  though it looked like one for a while. Section 1 records why it is not.
- **Did not refactor `api.py`.** Section 3 says why, and how.

---

## 5. Open, operator-only

| Item | Why it needs the operator |
| --- | --- |
| Restart 8081 to pick up the NaN guard | Kills a live ~395 k-sample session; nothing the service exposes reports arm state |
| Prune orphaned `.worktrees/2026092*` | Disk hygiene; may hold work I cannot see |
| API-key rotation | Keys live only in WSL `~/.config/agent-keys.env` |
| End-to-end slot *subscribe* | Requires a POST to the live service; forbidden to me outside the dashboard MCP, which failed to connect this session |

---

## Summary table

| Module | Interface | Shape | Status |
| --- | --- | --- | --- |
| `API/subscribe.c` | 12 functions, 9 pure | **Deep** | Sound. Host-compiled in the normal test run |
| `SUBSCRIBE_UART5_ENABLED` arm | one flag, three sites | **Deep** | Sound. Dormant, complete, consistently gated |
| `livewatch/transport.py` | `LiveTransport`, 3 adapters | **Deep** | Fixed `cafd660` — lifecycle now honoured by all three |
| `service/api.py` | 54 routes, 2 methods | **Shallow** | Drift closed `90f06ca`; restructure proposed, not done |
