# Agent prompts, 2026-10-24 (paste into the Cursor agents window)

Written by the CEO planning session. One prompt per agent. Paste each one as is. Where a prompt says @file, type
`@` and pick the file so it attaches as a chip; if pasted @paths stay plain text, re-type them.

## Cursor setup (once)

- **One Cursor window per worktree folder.** File > Open Folder > the folder in the table. Run that folder's agent in
  that window's Agents panel. Environment: **Local** (not Worktree, not Cloud): the folder already is a worktree.
- **Stop the old firmware agent in the main window now.** Main is still at f29459cd, so it has written nothing and
  nothing is lost. Relaunch it with prompt 1 in the fc-mrac-all-loops window.
- **The host agent keeps running in the main window.** Paste prompt 2 into its chat as a follow-up.
- At most 3 heavy agents at once (the laptop's power is fragile). Keil builds run one at a time behind `ah lock keil`.
- `.cursorindexingignore` in main keeps logs/, OBJ/, .cache/ out of the index.

| Folder | Branch | Prompt |
|---|---|---|
| D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex (main) | main | 2 (follow-up to the running host agent) |
| D:\Projects\UAV_lab\fc-mrac-all-loops | mrac-all-loops | 1 (firmware MRAC, relaunch) |
| D:\Projects\UAV_lab\fc-sysid | sysid-excite | 5 (sysid) |
| D:\Projects\UAV_lab\fc-estimator | est-upgrades | 3 (estimator) |
| D:\Projects\UAV_lab\fc-driftnn | drift-nn-lofo | 4 (drift NN v2) |

All four worktrees were created by the CEO and fast-forwarded to the commit that holds these docs.

## Launch order

| When | What |
|---|---|
| Now | Stop the old firmware agent. Prompt 1 in the fc-mrac-all-loops window. Prompt 2 into the host agent's chat |
| Now (third heavy slot) | Prompt 5, sysid: its step S0 answers "was the 10-21 sysid data good?" |
| When a heavy agent finishes | Prompt 3, estimator |
| Any time (light: E0-E1 run locally and are small; training runs on Kaggle) | Prompt 4, drift NN v2 |

The truth4 batch (`python tools/d5_truth4_batch.py --day <date> --run`) is the operator's job, after the lab. The
drift agent lists the days that need it.

## Common rules (already inside each prompt below)

Git: only `status`, `diff`, `log`, `show`, `grep`, `add <paths>`, `commit`. Never switch, checkout, stash, reset,
rebase, merge, push, `add -A` or `add .`. PowerShell 5.1: chain with `;`, not `&&`. Never flash; never touch the 8081
service or a real drone. No invented numbers: from a named log, or PROPOSED. Blocked more than 20 minutes on one item:
write it in the report, move on.

---

## Prompt 1: firmware MRAC agent (RELAUNCH in D:\Projects\UAV_lab\fc-mrac-all-loops)

> You are the firmware MRAC agent. Your folder is D:\Projects\UAV_lab\fc-mrac-all-loops. First run
> `git branch --show-current`: it must print `mrac-all-loops`. If not, stop and say so.
>
> Read only these, in order, before you start: @docs/agent/2026-10-24-context-map.md (the sections "Rules for every
> agent" and "Firmware MRAC agent"), @docs/design/2026-10-24-all-loop-mrac-layers.md (items 0 to 7), AGENTS.md once.
> Stop reading once you can name the function you change and the test you copy.
>
> Make one to-do per item, 0 to 7. **Item 0 first, as its own commit** (MRAC_VF_REF_ZETA, and the write order: an
> explicit variant write beats the MRAC_XyRefSelect row default). Write its commit hash on line 1 of
> docs/design/2026-10-24-all-loop-mrac-layers-report.md: today's matched flights wait for it. Then items 1 to 7, one
> commit each. Every new layer is off by default; with all of them off the output is bit-identical (write the
> equivalence test before the layer code). Clamp values are PROPOSED; list them first in the report.
>
> Files: only those the spec names. Other agents own campaign_fly.py, decks and campaigns/ (host), API/ekf*, calib*,
> gyro_filter* (estimator) and API/sysid.* (sysid). Build once at the end, behind `ah lock keil` (exit 3 = held by
> another agent: wait and retry): `bash .agent-ops/win.sh "UV4 -b -t JX_FLY -j0 USER/JX_FLY.uvprojx"`. Run only the
> tests of the files you change. Long commands run in the background.
>
> Git: only status, diff, log, show, grep, `add <paths>`, commit. Never switch, checkout, stash, reset, rebase,
> merge, push, `add -A` or `add .`. PowerShell 5.1: chain with `;`, not `&&`. Never flash; never touch the 8081
> service or a real drone. Read logs read-only from D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs and
> never write in that checkout. No invented numbers: from a named log, or PROPOSED. Blocked more than 20 minutes on
> one item: write it in the report, move on. Finish with the report and a 10-line summary.

---

## Prompt 2: host agent follow-up (paste into the RUNNING agent in the main window)

> Three corrections from the CEO. Apply them now, before your next item.
>
> 1. **No drop.** We never drop a flight because firmware is missing. Remove any drop or skip logic you added for
>    Gate W or for a missing field. The matched flights stay in the deck with the note `needs mrac-all-loops item 0`.
>    If the field is missing at run time, the verb fails loudly with an error that names the field. It never skips
>    silently.
> 2. **The firmware is built by another agent, in another folder** (D:\Projects\UAV_lab\fc-mrac-all-loops). Do not
>    edit API/, TASK/, ground_station/platform/firmware_contract.py, ground_station/service/fake_drone.py or
>    ground_station/analysis/mrac_active.py. Look field numbers up by name in firmware_contract.py; in tests, use a
>    fake contract entry. Never run git switch, checkout, stash, reset, rebase, merge or push: this checkout holds
>    12 files with earlier uncommitted edits that must survive.
> 3. **Context.** Read only the sections "Rules for every agent" and "Host agent" of
>    @docs/agent/2026-10-24-context-map.md. Stop reading once you can name the function you change and the test you
>    copy.
>
> Also:
> - Add `lab_d_excite` to today's deck right after the smoke hover: fixed gains, no livetune, MRAC adaptation frozen
>   if an existing verb does that (else write it in the report). It was never flown; it is today's sysid flight.
>   Re-run the parser and the fake-drone soak on it.
> - When you commit a file that already had uncommitted edits before you started (for example campaign_fly.py or
>   service/__main__.py), commit the whole file and add "includes earlier uncommitted edits" to the message. Stage
>   by path only, never `git add -A` or `git add .`.
> - PowerShell 5.1: chain with `;`, not `&&`. Never flash; never touch the 8081 service or a real drone.
> - Your report lists each matched flight and the field it waits for, and says "No flight dropped".

---

## Prompt 3: estimator agent (D:\Projects\UAV_lab\fc-estimator)

> You are the estimator agent. Your folder is D:\Projects\UAV_lab\fc-estimator. First run
> `git branch --show-current`: it must print `est-upgrades`. If not, stop and say so. The worktree already exists:
> ignore the "operator has run git worktree add" sentence of Prompt A.
>
> Read @docs/agent/2026-10-24-context-map.md (the sections "Rules for every agent" and "Estimator agent"), then do
> Prompt A of @docs/design/2026-10-24-state-estimation-upgrades.md: Part A only (Part B is superseded). Step 0 (the
> map) first, then A1 to A7, one to-do and one commit per item. CMD / VAR / field numbers start 8 above the next free
> number at commit f29459cd, so they do not collide with the MRAC agent's; list each number you take.
>
> Build once at the end, behind `ah lock keil` (exit 3 = held by another agent: wait and retry):
> `bash .agent-ops/win.sh "UV4 -b -t JX_FLY -j0 USER/JX_FLY.uvprojx"`. Run only the tests of the files you change.
> Long commands run in the background.
>
> Git: only status, diff, log, show, grep, `add <paths>`, commit. Never switch, checkout, stash, reset, rebase,
> merge, push, `add -A` or `add .`. PowerShell 5.1: chain with `;`, not `&&`. Never flash; never touch the 8081
> service or a real drone. Read logs read-only from D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs and
> never write in that checkout. No invented numbers: from a named log, or PROPOSED and inactive. Blocked more than
> 20 minutes on one item: write it in the report, move on. Finish with the report the spec names and a 10-line
> summary.

---

## Prompt 4: drift NN v2 agent (D:\Projects\UAV_lab\fc-driftnn)

> You are the drift NN agent. Your folder is D:\Projects\UAV_lab\fc-driftnn. First run
> `git branch --show-current`: it must print `drift-nn-lofo`. If not, stop and say so.
>
> Read @docs/agent/2026-10-24-context-map.md (the sections "Rules for every agent" and "Drift NN agent"), then build
> @docs/research/2026-10-24-drift-nn-v2-spec.md in its section 7 order (E0 to E7), one to-do and one commit per
> experiment. Before your first result, copy the win rule (section 2) into the report; do not change it afterwards.
> Be comprehensive: any neural network or other state-of-the-art method that lowers the drift is in scope (section 6);
> the limit is what you can implement and train.
>
> Compute: training runs on Kaggle only, with private datasets and private kernels; the kaggle CLI runs in WSL (see
> tools/kaggle_nnio/stage.sh). Log the GPU-hours of every run. Locally: data prep, tests and smoke runs under
> 2 minutes. No Colab. Never print, log or commit the Kaggle token. Do not run tools/d5_truth4_batch.py: list the
> days that need it, for the operator. Literature: verify every entry (title, venue, year, one quoted key result)
> with web search; drop what you cannot verify.
>
> Git: only status, diff, log, show, grep, `add <paths>`, commit. Never switch, checkout, stash, reset, rebase,
> merge, push, `add -A` or `add .`. PowerShell 5.1: chain with `;`, not `&&`. No firmware, no 8081 service, no
> drone, no flash. Read logs read-only from D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs and never
> write in that checkout. No invented numbers: from a log, a run id, a quoted paper, or PROPOSED. Blocked more than
> 20 minutes on one step: write it in the report, move on. Finish with the report the spec names and a 10-line
> summary.

---

## Prompt 5: sysid agent (D:\Projects\UAV_lab\fc-sysid)

> You are the system identification agent. Your folder is D:\Projects\UAV_lab\fc-sysid. First run
> `git branch --show-current`: it must print `sysid-excite`. If not, stop and say so.
>
> Read @docs/agent/2026-10-24-context-map.md (the sections "Rules for every agent" and "Sysid agent"), then build
> @docs/design/2026-10-24-sysid-excitation.md S0 to S4 in order, one to-do and one commit each. S0 first and fast:
> the operator wants a verdict per axis (usable / marginal / unusable, with numbers) on the 10-21 data. Verify each
> method citation in section 3 with web search, or mark it unverified.
>
> Files: only those the spec lists in section 4. If you change firmware, build once at the end, behind
> `ah lock keil` (exit 3 = held by another agent: wait and retry):
> `bash .agent-ops/win.sh "UV4 -b -t JX_FLY -j0 USER/JX_FLY.uvprojx"`. Run only the tests of the files you change.
> Long commands run in the background.
>
> Git: only status, diff, log, show, grep, `add <paths>`, commit. Never switch, checkout, stash, reset, rebase,
> merge, push, `add -A` or `add .`. PowerShell 5.1: chain with `;`, not `&&`. Never flash; never touch the 8081
> service or a real drone. Read logs read-only from D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs and
> never write in that checkout. No invented numbers: from a named log, or PROPOSED. Blocked more than 20 minutes on
> one item: write it in the report, move on. Finish with the report the spec names and a 10-line summary.
