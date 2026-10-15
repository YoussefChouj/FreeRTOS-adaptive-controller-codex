# Gemini 4 Argon + Antigravity CLI (agy): what the CEO can use it for (2026-10-15)

Sources: two agy runs (gemini-4-argon-medium, plan mode, web research only), plus CEO spot checks with WebFetch.
Measured run cost: general run 523k in / 25.9k out / 3.72M cache-read, 1151 s. Robotics run 361k in / 9.6k out /
1.48M cache-read, 735 s. Open-ended web research is the most expensive way to use Argon. Ask one narrow question per run.

## CEO-verified on 2026-10-15
- Drone-Bench is real. It is published by Andon Labs and asks a model to write Python for a DJI Tello on a slow, indoor
  surveillance task. The andonlabs.com home page says "Highest score by Claude Opus 5.5". Gemini 4 Argon is not on the
  leaderboard; the newest Google model listed is gemini-3.1. The digest's 97% (Opus 5.5) and 69% (gemini-3.1) were
  not re-checked.
- The Andon Labs home page names Gemini 4 Argon as the Blueprint-Bench leader. That benchmark turns photos into a 2D
  floorplan.
- ERQA is 87%, described as "outperforming every other frontier model" (developers.googleblog.com/gemini-4-argon-for-robotics).
  That post does not mention drones or UAVs; its robots are arms, the Spot quadruped and humanoids.
- Auto-compaction: the docs say agy "automatically compacts older turns into checkpoint summaries", but they do not
  say when it triggers, and no setting changes it. The binary has a `/compact` command and an OnCompaction hook (strings
  probe). The old gemini CLI had `model.compressionThreshold`; agy settings.json does not have one. Conclusion: you
  cannot set a ceiling. Run `/compact` by hand, or `/clear` between tasks.

## How to use it (CEO plan)
- Use it as a second-opinion verifier: `agy -p "<claim + files>" --mode plan --output-format json --json-schema '<verdict schema>'`.
  The parsed answer is in `.structured_output`. Its strength here is a 15% hallucination rate on AA-Omniscience, the
  lowest Artificial Analysis lists; that figure is not CEO-verified.
- Use it to cross-check long logs or large diffs. It scores 84.2% on GraphWalks 256k-1M, against Opus 5.5 at 66.8%.
- Use it to review flight video offline. LVBench is 91.7%, but measured at 1 FPS, so it is not frame-accurate on fast
  motion.
- Do not use it as the coder for terminal-heavy or drone-code work. On Terminal-bench 4.0 it scores 57.4% against
  Opus 5.5 at 66.4%, and Opus 5.5 leads Drone-Bench.
- Harness features worth using: `--json-schema`, custom subagents (`.agents/agents/*.md`, model flash or pro), hooks
  (`.agents/hooks.json`), `/btw` for side questions that add no history, `/usage` for quota (5-hour and weekly
  windows), and `/rewind` and `/fork`.

---
# Raw digest 1: benchmarks, harness, context, quota (Argon run, unedited)

### 1. Benchmark Comparison: Gemini 4 Argon vs. Claude Opus 5.5 vs. OpenAI Best

| Benchmark | Argon | Opus 5.5 | OpenAI Best (GPT-6 Astra / 6.1 Sol) | Source & Quote (max 15 words) |
| :--- | :--- | :--- | :--- | :--- |
| **DeepSWE v1.1** (Coding) | **77.9%** | 74.2% | 74.1% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"DeepSWE v1.1 Agentic coding | 77.9% | 74.2% | 67.4% | 74.1%"` |
| **FrontierSWE v2** (Coding) | 55.0% | 62.3% | **65.5%** (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"FrontierSWE v2 Agentic coding | 55.0% | 62.3% | 51.3% | 65.5%"` |
| **Vibe Code Bench** (Web Apps) | **91.9%** | 90.3% | 89.6% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Vibe Code Bench Agentic Web App Development | 91.9% | 90.3% | ... | 89.6%"` |
| **SWE-bench Verified / Pro** | `UNVERIFIED` | `UNVERIFIED` | `UNVERIFIED` | Replaced by DeepSWE v1.1 & FrontierSWE v2 in [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/): `"Gemini 4 Argon results as of October, 2026 are below"` |
| **Terminal-bench 4.0** (Shell) | 57.4% | **66.4%** | 58.2% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Terminal-bench 4.0 Terminal agentic coding | 57.4% | 66.4% | 52.3% | 58.2%"` |
| **PostTrainBench** (ML Eng.) | 45.3% | **49.3%** | 44.3% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"PostTrainBench Agentic ML Engineering | 45.3% | 49.3% | 34.4% | 44.3%"` |
| **CWE-bench v1** (Security) | **68.0%** | 67.0% | **68.0%** (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"CWE-bench v1 Agentic Cybersecurity | 68.0% | 67.0% | 65.0% | 68.0%"` |
| **AA Intelligence Index** | 53 (high) | `UNVERIFIED` | 53 (Astra max) / 52 (Sol max) | [Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon) — `"scores 53 on the Artificial Analysis Intelligence Index, matching GPT-6 Astra (max, 53)"` |
| **Terminal-Bench Science 0.1** | 57.6% | 63.3% | **68.1%** (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Terminal-Bench Science 0.1 Agentic science | 57.6% | 63.3% | 51.3% | 68.1%"` |
| **LABBench 2** (Bio/Lab) | **88.8%** | 73.1% | 85.4% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"LABBench 2 Biology agentic tool use | 88.8% | 73.1% | 74.2% | 85.4%"` |
| **GPQA / HLE / ARC-AGI** | `UNVERIFIED` | `UNVERIFIED` | `UNVERIFIED` | Omitted from October 2026 [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/): `"All Gemini 4 Argon scores are pass @1 except where otherwise noted"` |
| **RiemannBench** (Math) | **76.0%** | 69.6% | 72.0% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"RiemannBench Mathematical reasoning | 76.0% | 69.6% | 62.7% | 72.0%"` |
| **GraphWalks <=128k** (Context) | **99.7%** | 90.6% | 98.7% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"GraphWalks (Up to 128k) Long context (BFS, F1) | 99.7% | 90.6% | ... | 98.7%"` |
| **GraphWalks 256k–1M** (1M Ctx) | **84.2%** | 66.8% | 71.8% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"GraphWalks (256k to 1M) Long context (BFS, F1) | 84.2% | 66.8% | ... | 71.8%"` |
| **MRCR at 1M** | `UNVERIFIED` | `UNVERIFIED` | `UNVERIFIED` | Replaced by GraphWalks 256k–1M in [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/): `"GraphWalks (256k to 1M) Long context (BFS, F1)"` |
| **Chartography** (Charts) | **71.6%** | 66.3% | 71.0% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Chartography Multimodal chart understanding | 71.6% | 66.3% | 69.8% | 71.0%"` |
| **LVBench** (Long Video) | **91.7%** | 83.7% | 87.5% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"LVBench Long video understanding | 91.7% | 83.7% | 84.8% | 87.5%"` |
| **Agent's Last Exam** (GUI/Screen) | **39.5%** | 38.2% | 34.2% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Agent's Last Exam Computer Use (Pass Rate) | 39.5% | 38.2% | 30.3% | 34.2%"` |
| **OSWorld-2.0** (Screen/OS) | 69.2% | `UNVERIFIED` | **72.6%** (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"OSWorld-2.0 (Offline subset) Computer Use (Partial Score) | 69.2% | - | 64.9% | 72.6%"` |
| **AA-Omniscience Hallucination** | **15%** (Best) | `UNVERIFIED` | 51% (Astra) / 54% (Sol) | [Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon) — `"On AA-Omniscience, Gemini 4 Argon has a 15% hallucination rate, the lowest"` |
| **AA-Omniscience Accuracy** | 50% (Score 42) | `UNVERIFIED` | **63%** (Astra, Score 43) | [Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon) — `"On accuracy, Gemini 4 Argon scores 50%... 13 points below GPT-6 Astra"` |
| **SimpleQA / BrowseComp** | `UNVERIFIED` | `UNVERIFIED` | `UNVERIFIED` | Not reported for Argon; evaluated via AA-Omniscience on [Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon): `"AA-Omniscience Score (ranging from -100 to 100) of 42"` |
| **Vals Index** (Agentic Work) | **68.9%** | 67.0% | 63.1% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Vals Index Economically valuable tasks | 68.9% | 67.0% | 59.9% | 63.1%"` |
| **AutomationBench** (Zapier) | **51.3%** | 42.5% | 41.4% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"AutomationBench (Zapier private set) Agentic workflows | 51.3% | 42.5% | 35.1% | 41.4%"` |
| **AutomationBench-AA** (Tool Use) | **78%** (#1) | `UNVERIFIED` (Sonnet 5.5: 71%) | `UNVERIFIED` | [Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon) — `"ranks #1 on AutomationBench-AA at 78%, 7 points ahead of Claude Sonnet 5.5"` |
| **Vals Finance Agent v2** | **65.4%** | 58.6% | 53.5% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Vals Finance Agent v2 Financial analysis | 65.4% | 58.6% | 49.0% | 53.5%"` |
| **Harvey Legal Agent** | **19.6%** | 3.8% | 5.4% (Astra) | [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/) — `"Harvey's Legal Agent Benchmark Legal workflows | 19.6% | 3.8% | 2.2% | 5.4%"` |
| **tau-bench** | `UNVERIFIED` | `UNVERIFIED` | `UNVERIFIED` | Not reported for Argon; tool use tested on AutomationBench in [DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/): `"AutomationBench (Zapier private set) Agentic workflows"` |

---

### 2. Antigravity CLI (`agy`) Features & `settings.json` Reference

| Feature / Setting | Status in `agy` | Details, Flags, or Default Value | Source & Quote (max 15 words) |
| :--- | :--- | :--- | :--- |
| **Subagents** | Supported | Built-in (`research`, `browser`, `self`) + custom `.agents/agents/<name>.md`; `invoke_subagent`, `/agents`, `Alt+J`, `Ctrl+K` | [Antigravity Subagents](https://antigravity.google/docs/subagents) — `"parent agent calls the invoke_subagent tool to spawn a new concurrent session"` |
| **Skills** | Supported | Folder bundles with `SKILL.md` in `.agents/skills/` and `~/.gemini/antigravity-cli/skills/`; `/skills` | [Antigravity Skills](https://antigravity.google/docs/skills) — `"A skill is a folder containing a SKILL.md file with instructions"` |
| **Hooks** | Supported | 6 events (`SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`) in `.agents/hooks.json`; `/hooks` | [Antigravity Hooks](https://antigravity.google/docs/hooks) — `"Hooks allow you to run custom scripts or shell commands at specific points"` |
| **MCP Client Support** | Supported | `stdio`, `SSE`, `Streamable HTTP` via `serverUrl` in `.agents/mcp_config.json` & `~/.gemini/config/mcp_config.json`; `/mcp` | [Antigravity MCP](https://antigravity.google/docs/mcp) — `"Antigravity CLI supports both local stdio processes and remote host MCP server configurations."` |
| **Run as MCP / ACP Server** | `UNVERIFIED` in `agy` (Legacy `gemini --acp`) | Legacy `gemini` CLI supports `gemini --acp`; native MCP/ACP server mode in `agy` is `UNVERIFIED` | [Gemini CLI ACP Docs](https://geminicli.com/docs/cli/acp-mode/) — `"To start Gemini CLI in ACP mode, use the --acp flag"` |
| **Headless / JSON Output** | Supported | `agy -p` (`--print`, `--prompt`) with `--output-format text\|json\|stream-json` (NDJSON events) | [Antigravity Headless](https://antigravity.google/docs/cli/headless) — `"The --output-format flag controls the shape of stdout. It accepts three values"` |
| **`--json-schema`** | Supported | Enforces JSON schema in headless mode; parsed output in `.structured_output` | [Antigravity Headless](https://antigravity.google/docs/cli/headless) — `"Pass --json-schema to constrain the answer to a schema."` |
| **Plan Mode** | Supported | `/plan`, `/planning`, `--mode plan`, or `Shift+Tab` (`default` > `accept-edits` > `plan`) | [Antigravity CLI Modes](https://antigravity.google/docs/cli/modes) — `"Press Shift+Tab inside the prompt box to cycle through the active sequence"` |
| **Checkpoints / Rewind** | Supported | Automatic compaction checkpoints; `/rewind` (alias `/undo`) and `/fork` (alias `/branch`) | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"/rewind | Conversations | /undo | Roll back your conversation history to a previous message."` |
| **Browser Agent** | Supported | Invoked via `/browser`; runs isolated Chrome profile, captures screenshots/videos | [Antigravity Slash Commands](https://antigravity.google/docs/slash-commands) — `"Spawns a sandboxed Chrome browser subagent capable of navigating web pages"` |
| **Google Search Grounding** | Supported | Built-in `search_web` and `read_url_content` tools | [Antigravity Hooks](https://antigravity.google/docs/hooks) — `"search_web: Perform a general web search. Arguments: query, domain (optional)"` |
| **Image / Video / PDF Input** | Supported | `Ctrl+V` pastes PNG/JPEG/GIF/WebP/BMP/TIFF/SVG & MP4/MOV/WebM/AVI; `view_file` reads PDFs/images; `@` paths | [Antigravity Prompting](https://antigravity.google/docs/cli/prompting) — `"Images: PNG, JPEG, GIF, WebP, BMP, TIFF, and SVG. Videos: MP4, MOV, WebM"` |
| **`settings.json` Location** | Supported | `~/.gemini/antigravity-cli/settings.json` (sparse JSON; edited via `/config` or `/settings`) | [Antigravity Settings](https://antigravity.google/docs/settings) — `"The persistent settings are saved in a plain JSON format: ~/.gemini/antigravity-cli/settings.json"` |
| **`toolPermission`** | `settings.json` key | `"request-review"` (default), `"proceed-in-sandbox"`, `"always-proceed"`, `"strict"` | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Global safety presets: 'request-review'... 'proceed-in-sandbox'... 'always-proceed'... or 'strict'"` |
| **`artifactReviewPolicy`** | `settings.json` key | `"asks-for-review"` (default), `"agent-decides"`, `"always-proceed"` | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Code review policy: 'asks-for-review'... 'agent-decides'... or 'always-proceed'"` |
| **`enableTerminalSandbox`** | `settings.json` key | `false` (default); OS-native containment (`nsjail`, `sandbox-exec`, `AppContainer`) | [Antigravity CLI Features](https://antigravity.google/docs/cli/features) — `"uses native operating system features (nsjail on Linux, sandbox-exec on macOS, and AppContainer"` |
| **`allowNonWorkspaceAccess`** | `settings.json` key | `false` (default); restricts file reads/writes outside workspace root | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Permits the agent’s file read and write tools to navigate outside recognized Git/workspace"` |
| **`permissions`** | `settings.json` key | Object with `"allow"` and `"deny"` arrays (e.g., `"command(git)"`) | [Antigravity CLI Features](https://antigravity.google/docs/cli/features) — `"Fine-grained permissions: Instead of global levels, define specific allowed or denied commands"` |
| **`altScreenMode` / `colorScheme`** | `settings.json` keys | `altScreenMode`: `"default"`, `"always"`, `"never"`; `colorScheme`: `"terminal"` (default), `"tokyo night"`, etc. | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Screen buffer usage: 'default' (adaptive inline/altscreen), 'always'... or 'never'"` |
| **`verbosity` / `runningLightSpeed`** | `settings.json` keys | `verbosity`: `"high"` (default) or `"low"`; `runningLightSpeed`: `"fast"`, `"medium"` (default), `"slow"`, `"off"` | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Visual verbosity level: 'high' (renders full thoughts and tool outputs) or 'low'"` |
| **`editor` / `editorMode` / `vimInsertFirst`** | `settings.json` keys | `editor`: `"auto"` (default); `editorMode`: `"default"` or `"vim"`; `vimInsertFirst`: `false` | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Prompt editing model: 'default' (flat text editing) or 'vim' (modal editing)."` |
| **`useG1Credits` / `enableTelemetry`** | `settings.json` keys | `useG1Credits`: `false` (default); `enableTelemetry`: `true`; `notifications`: `false`; `showTips`: `true`; `showFeedbackSurvey`: `true` | [Antigravity CLI Reference](https://antigravity.google/docs/cli/reference) — `"Uses personal AI credits for model calls once plan quotas are exhausted."` |

---

### 3. Context Management & Ultra Quota Summary

| Capability / Setting | `agy` (Antigravity CLI) | Legacy `gemini` CLI (`geminicli.com`) | Source & Quote (max 15 words) |
| :--- | :--- | :--- | :--- |
| **Auto-Compaction** | Automatic into checkpoint summaries; tracked in `/context` | Automatic at `model.compressionThreshold` | [Antigravity Context Docs](https://antigravity.google/docs/cli/commands/context) — `"Antigravity automatically compacts older turns into checkpoint summaries"` |
| **Compaction Threshold** | `UNVERIFIED` (not exposed in `agy` `settings.json`) | `model.compressionThreshold` (default `0.5`) | [Gemini CLI Config](https://geminicli.com/docs/reference/configuration/) — `"model.compressionThreshold (number): The fraction of context usage at which to trigger context compression"` |
| **Manual Compaction** | `/compact` (or `/clear`, `/btw` for zero-history side queries) | `/compress` | [Google Developers Blog](https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/) — `"preserves the goal across context compactions so the agent keeps running"` |
| **Max-Context / Turn Cap** | `UNVERIFIED` in `agy` `settings.json` | `model.maxSessionTurns` (default `-1`), `model.summarizeToolOutput` | [Gemini CLI Config](https://geminicli.com/docs/reference/configuration/) — `"model.maxSessionTurns (number): Maximum number of user/model/tool turns to keep"` |
| **Ignore Files** | `.gitignore` in strict mode; `.geminiignore` in `agy` is `UNVERIFIED` | `.geminiignore` (`context.fileFiltering.respectGeminiIgnore`: `true`) | [Gemini Ignore Docs](https://geminicli.com/docs/cli/gemini-ignore/) — `"Adding paths to your .geminiignore file will exclude them from tools"` |
| **Ultra Plan Quota** | Highest quota, refreshed every **5 hours** + **weekly** limit | Based on `"Work Done"`; exact token/request numbers `UNVERIFIED` | [Antigravity Plans](https://antigravity.google/docs/plans) — `"The highest, most generous quota, refreshed every five hours"` |
| **Checking Quota** | `/usage` (alias `/quota`), `/credits`, statusline indicator | `/stats` | [Antigravity Usage Docs](https://antigravity.google/docs/cli/commands/usage) — `"Antigravity CLI provides the /usage command (alias /quota) to help you monitor"` |

---

### Detailed Findings

#### 1. Benchmarks: Where Argon Is Clearly Best vs. Clearly Behind
* **Clearly Best — Long-Context Reasoning at 1M Tokens**: Argon leads Opus 5.5 by +17.4 points and GPT-6 Astra by +12.4 points on `GraphWalks (256k to 1M)` (84.2% vs. 66.8% and 71.8%) ([DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/), `"GraphWalks (256k to 1M) Long context (BFS, F1) | 84.2% | 66.8%"`).
* **Clearly Best — Epistemic Calibration & Low Hallucination (Verifier Role)**: Argon achieves a 15% hallucination rate on `AA-Omniscience`—the lowest of 45+ models tested—compared to 51% for GPT-6 Astra ([Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon), `"Gemini 4 Argon has a 15% hallucination rate, the lowest"`). It abstains rather than guessing when uncertain ([Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon), `"acknowledges when it does not know an answer rather than guessing"`).
* **Clearly Best — Multimodal Video/Chart & Enterprise Tool Use**: Argon leads on `LVBench` long video (91.7% vs. Opus 5.5's 83.7%), `Chartography` (71.6% vs. 66.3%), `RiemannBench` math (76.0% vs. 69.6%), `LABBench 2` (88.8% vs. 73.1%), `AutomationBench-AA` (#1 at 78%), and `Harvey Legal` (19.6% vs. 3.8%) ([DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/), `"LVBench Long video understanding | 91.7% | 83.7% | 84.8% | 87.5%"`).
* **Clearly Behind — Interactive Terminal & Bleeding-Edge Repo SWE**: Argon trails Claude Opus 5.5 on `Terminal-bench 4.0` by 9.0 points (57.4% vs. 66.4%) and trails both GPT-6 Astra (65.5%) and Opus 5.5 (62.3%) on `FrontierSWE v2` (55.0%) ([DeepMind Model Card](https://deepmind.google/models/model-cards/gemini-4-argon/), `"Terminal-bench 4.0 Terminal agentic coding | 57.4% | 66.4%"`).
* **Clearly Behind — Parametric Trivia Recall & Token Verbosity**: On `AA-Omniscience` raw accuracy, Argon scores 50% vs. GPT-6 Astra's 63%, and uses 62k output tokens across the Intelligence Index vs. 27k for GPT-6 Astra ([Artificial Analysis](https://artificialanalysis.ai/models/gemini-4-argon), `"used 62k output tokens across the Intelligence Index, more than double GPT-6 Astra"`).

#### 2. Antigravity CLI (`agy`) Features
* **Transition Architecture**: Google rebuilt the Node.js `gemini` CLI into `agy`, a native Go binary sharing the Antigravity harness ([Google Developers Blog](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli), `"rebuilt the CLI from the ground up as a native, high-performance binary written in Go"`).
* **Headless Verification & Schema Enforcement**: Claude Code can invoke `agy -p "<prompt>" --output-format json --json-schema '<schema>'` as a non-interactive verifier; `stdout` emits clean JSON with `.structured_output` while diagnostics go to `stderr` ([Antigravity Headless](https://antigravity.google/docs/cli/headless), `"The response goes to stdout. Diagnostics—errors, authentication prompts, progress, and permission notices—go to stderr."`).
* **Subagents & Tiers**: Custom subagents in `.agents/agents/<name>.md` specify `tools`, `commandExecutionPolicy` (`off`, `auto`, `eager`, `sandbox`), and `model` (`inherit`, `flash`, `pro`), where local fast agents run on **Gemini 3.8 Flash** ([Antigravity Home](https://antigravity.google/docs/home), `"Gemini 3.8 Flash: Powers all local agents with state-of-the-art speed"`).

#### 3. Context Management in `agy`
* **Visual Tracking & Compaction**: `/context` renders a grid of active tokens (`◉`), free capacity (`□`), and a separate `⊠` **Checkpoint buffer** for compacted history ([Antigravity Context Docs](https://antigravity.google/docs/cli/commands/context), `"Checkpoint buffer: tokens held by checkpoint summaries, which stand in for older turns"`).
* **Zero-Pollution Side Queries**: Use `/btw <query>` to ask side questions without adding tokens to the persistent conversation history ([Antigravity Context Docs](https://antigravity.google/docs/cli/commands/context), `"so the question and answer aren’t added to your main conversation history"`).
* **Settings Gap vs. Gemini CLI**: While legacy `gemini` CLI exposes `model.compressionThreshold` (`0.5`), `model.maxSessionTurns` (`-1`), and `.geminiignore`, those keys are `UNVERIFIED` in `agy`'s sparse `~/.gemini/antigravity-cli/settings.json`; `agy` enforces `.gitignore` in strict mode ([Antigravity Settings](https://antigravity.google/docs/settings), `"The agent respects .gitignore rules, preventing it from accessing ignored files."`).

#### 4. Google AI Ultra Plan Limits for Argon
* **Availability**: Gemini 4 Argon is exclusive to **Google AI Ultra** and **Enterprise** plans in the Antigravity app, CLI, and IDE extensions (not in standalone Antigravity IDE) ([Antigravity Models](https://antigravity.google/docs/models), `"Not available in Antigravity IDE. Available in the Antigravity app, Antigravity CLI"`).
* **5-Hour & Weekly Windows**: Ultra provides the highest quota, refreshed **every 5 hours**, plus a **weekly rate limit** governed by **"Work Done"** (reasoning effort/compute) rather than fixed prompt/token numbers (exact numerical caps are `UNVERIFIED` / undisclosed) ([Antigravity Plans](https://antigravity.google/docs/plans), `"rate limits directly correlate with the amount of work done by the agent"`).
* **Monitoring & Overages**: Run `/usage` (or `/quota`) to refresh per-model quota in the TUI, `/credits` to inspect AI credits, and set `"useG1Credits": true` in `settings.json` for overage fallback ([Antigravity Credits](https://antigravity.google/docs/cli/credits), `"The right side of the CLI statusline displays your remaining credit count"`).

#### 5. Best Practices: Argon as Verifier / Second Opinion & Subagent Manager
* **Subtraction & Audit Discipline**: In Google's overnight optimization of the 200,000-line Python ADK repo with Argon, engineers recommend pairing agent exploration with human/lead-agent subtraction; Argon naturally self-audits diffs before finishing ([Google Developers Blog](https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/), `"Even three minutes before finishing... Argon was still auditing its own diff"`).
* **Progressive Disclosure in `SKILL.md`**: Keep top-level `SKILL.md` files concise and link to `references/` files so subagents only load domain rules when needed ([Google Developers Blog](https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/), `"Each skill starts with a short SKILL.md index that points to focused references/"`).
* **Adversarial Verification Gates (`/boost` & `/teamwork-preview`)**: Separate implementation from verification using read-only `Explorer` (`research`), `Critic` (code review), `Challenger` (adversarial edge cases), and `Auditor` (verifying real test outputs) roles ([Antigravity Teamwork](https://antigravity.google/docs/teamwork), `"checks test evidence against real command output, ensuring tests genuinely passed"`).
* **Enforce Verification Loops via Hooks**: Configure `PostToolUse` hooks in `.agents/hooks.json` or test commands so Argon verifies every diff against local tests before returning ([Antigravity Best Practices](https://antigravity.google/docs/cli/best-practices), `"provide the agent with a local verification mechanism (such as unit tests"`).

---

### Sources
* [Gemini 4 Argon Model Card (Google DeepMind)](https://deepmind.google/models/model-cards/gemini-4-argon/)
* [Gemini 4 Argon Evaluation Methodology PDF (Google DeepMind)](https://deepmind.google/models/evals-methodology/gemini-4-argon)
* [Gemini 4 Argon API Documentation (Google AI for Developers)](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon)
* [Gemini 4 Argon Independent Evaluation (Artificial Analysis)](https://artificialanalysis.ai/models/gemini-4-argon)
* [Optimizing a 200,000-Line Python Codebase Overnight with Gemini 4 Argon (Google Developers Blog)](https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/)
* [Transitioning Gemini CLI to Antigravity CLI (Google Developers Blog)](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli)
* [Google Antigravity Documentation — Home, Models & Plans](https://antigravity.google/docs/plans)
* [Google Antigravity CLI Reference, Headless Mode, Settings & Modes](https://antigravity.google/docs/cli/reference)
* [Google Antigravity Customizations — Subagents, Skills, Hooks, MCP & Teamwork](https://antigravity.google/docs/subagents)
* [Gemini CLI Configuration, ACP Mode & `.geminiignore` Reference](https://geminicli.com/docs/reference/configuration/)


---
# Raw digest 2: robotics, 3D, Drone-Bench, video (Argon run, unedited)

| Claim | Value | Source |
| :--- | :--- | :--- |
| Gemini 4 Argon ERQA (Embodied Reasoning QA) score | **87%** (no tools; #1 reported by Google) | [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/) — *"Using pure reasoning, Argon scores 87%, outperforming every other frontier model."* |
| ERQA numeric scores for Claude Opus 5.5 & GPT-6 Astra | **UNVERIFIED** (embedded chart image only; omitted from evals table) | [Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon) — *"Methodology and Details for the Gemini 4 Argon Evaluation Report"* |
| Andon Labs Blueprint-Bench 2 (2D floorplan from ~20 photos) | **Argon #1 (0.544)** vs. Opus 5.5 (**0.512**), GPT-6 Astra (**0.497**) | [Andon Labs](https://andonlabs.com/) — *"Blueprint-Bench Can AI agents understand space? Highest score by Gemini 4 Argon"* |
| Real2Sim MuJoCo self-practice gain on physical arm | **56% → 75%** success; **35s → 24s** execution time | [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/) — *"boosting real-world physical robot Success Rate from 56% to 75%"* |
| Gemini Robotics-ER 1.5 pointing, 2D/3D boxes & benchmarks | Normalized `0–1000` 2D points/boxes, 3D boxes; leads 15 ER benchmarks | [DeepMind Blog](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world) — *"achieves the highest aggregated performance on 15 academic embodied reasoning benchmarks"* |
| Gemini Robotics ER 2 base model & safety restriction | Based on Gemini 3.5 Flash; bars safety-critical transportation use | [ER 2 Card](https://deepmind.google/models/model-cards/gemini-robotics-er-2) — *"Gemini Robotics ER 2 is based on the Gemini 3.5 Flash."* |
| Is Argon reported best on **Drone-Bench**? | **No (Not Evaluated)**; **Claude Opus 5.5 leads (97% avg)**, GPT-6 Astra **95%** | [Andon Labs](https://andonlabs.com/) — *"Drone-Bench Can AI models code autonomous drones? Highest score by Claude Opus 5.5"* |
| Best Google model on Drone-Bench (`gemini-3.1`) | **69% avg** (Reconstruct 10.7%, Localize 40.7%, Navigate 78.3%, Detect 82.4%, Follow 66.2%) | [Drone-Bench](https://andonlabs.com/evals/drone-bench) — *"measuring how well AI models can write code to perform a simple surveillance task"* |
| Long-video benchmark (LVBench, evaluated at **1 FPS**) | **Argon 91.7%** vs. GPT-6 Astra (**87.5%**), Opus 5.5 (**83.7%**) | [Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon) — *"LVBench results are self computed without tools. We use 1FPS for Gemini"* |
| High-FPS fast-moving aerial/flight video tracking benchmark | **Does not exist / UNVERIFIED** for Argon | [Argon Card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-4-Argon-Model-Card.pdf) — *"Evaluation results across key capabilities may be found in our release blog"* |
| Video temporal segmentation & multi-camera progress scoring | 4-level timestamps (3–15s primitives) on 136s video; 28-segment progress on 3-cam video | [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/) — *"Argon produces a time-aligned 4-level hindsight annotation hierarchy"* |
| Hardware log + video self-diagnosis (SO-101 6-DoF arm) | Execution time cut from **58.9s (Iter 1) to 17.4s (Iter 3)** | [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/) — *"Review your own logs and wrist/overhead footage, find the root cause"* |
| Agentic coding: DeepSWE v1.1 / FrontierSWE v2 / Terminal-Bench 4.0 | DeepSWE: **77.9%** (1st); FrontierSWE: **55.0%** (3rd); Terminal-Bench: **57.4%** (3rd) | [Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon) — *"No changes are made to the official benchmark harness except increasing timeouts"* |
| Long-context reasoning (GraphWalks 256k–1M tokens) | **Argon 84.2%** vs. GPT-6 Astra (**71.8%**), Opus 5.5 (**66.8%**) | [Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon) — *"Evaluated on the full 60 sample problem set which includes both BFS and Parents."* |
| PX4, ArduPilot, STM32 FreeRTOS C, or `.ulg`/CSV UAV log benchmarks | **UNVERIFIED** (no published UAV firmware or flight-log evaluations) | — |

### 1. Robotics & 3D/Spatial Understanding
* **ERQA & Spatial Reasoning:** Google reports Gemini 4 Argon scores **87%** zero-shot on **ERQA** (*"Using pure reasoning, Argon scores 87%, outperforming every other frontier model"* — [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/)). Exact competitor ERQA numbers are **UNVERIFIED** because Google's blog embeds them only in a non-text chart image and omits ERQA from the [Evaluation Report](https://deepmind.google/models/evals-methodology/gemini-4-argon). On Andon Labs' [Blueprint-Bench 2](https://andonlabs.com/evals/blueprint-bench-2) (converting ~20 apartment photos into a metric 2D floorplan), Argon ranks **#1 (0.544)** over Claude Opus 5.5 (**0.512**) and GPT-6 Astra (**0.497**) (*"Blueprint-Bench 2 tests spatial reasoning by asking AI agents to convert apartment photographs"*).
* **Pointing, 3D Boxes & Calibration:** Argon natively reasons over calibrated 2D/3D camera coordinates (`robot.deproject(u, v, depth)`) and reconstructs 500 Hz MuJoCo contact physics from egocentric video (*"accurately matching the timing, magnitude, and direction of the logged real-world tactile forces"* — [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/)). Specialized **Gemini Robotics-ER 1.5** outputs normalized `0–1000` 2D points, 2D/3D bounding boxes, and trajectories across 15 benchmarks including Point-Bench, RefSpatial, and Where2Place (*"The model excels at generating semantically-precise 2D points, grounded in reasoning"* — [Google Devs Blog](https://developers.googleblog.com/en/building-the-next-generation-of-physical-agents-with-gemini-robotics-er-1-5/)), while **Gemini Robotics ER 2** adds multi-robot coordination on a Gemini 3.5 Flash backbone (*"understand the physical world, plan multi-step tasks, and can coordinate multiple robots"* — [DeepMind ER](https://deepmind.google/models/gemini-robotics/gemini-robotics-er/)).

### 2. Drone Benchmark ("Drone-Bench")
* **Argon Is Not Evaluated; Opus 5.5 Leads:** **Drone-Bench** exists and is published by **Andon Labs** ([website](https://andonlabs.com/evals/drone-bench), [paper](https://andonlabs.com/docs/Drone_Bench.pdf)). **Gemini 4 Argon is not on the leaderboard.** **Claude Opus 5.5** leads at **97%** average progress toward the human–AI baseline, followed by **GPT-6 Astra (95%)** (first to beat the baseline on all 5 tasks in best-of-10 runs), while Google's latest tested model (`gemini-3.1`) scores **69%** (*"Drone-Bench Can AI models code autonomous drones? Highest score by Claude Opus 5.5"* — [Andon Labs](https://andonlabs.com/)).
* **What It Measures & Credibility:** Models get 10 iterative submissions per run to write Python code for a **$129 DJI Tello EDU** across 5 tasks: **Reconstruct** (video → 3D/2D obstacle map; Opus 5.5 71.0%, Astra 67.5%, Gemini 3.1 10.7%), **Localize** (frame → 6-DoF pose within 10 cm / 0.1s; Opus 5.5 95.0%, Astra 86.7%, Gemini 3.1 40.7%), **Navigate** (MuJoCo waypoint loop under 100–300 ms latency and 80% camera fix rate; Astra 98.2%, Opus 5.5 96.7%), **Detect** (face-matched person IoU; Opus 5.5 88.7%), and **Follow** (60s closed-loop tracking of a 0.6 m/s human; Astra 88.6%, Opus 5.5 82.8%) ([Drone-Bench](https://andonlabs.com/evals/drone-bench)). It is credible for **high-level Python vision/navigation pipelines** (validated on real Tello hardware with anti-cheat checks: *"measuring how well AI models can write code to perform a simple surveillance task"* — [Drone-Bench PDF](https://andonlabs.com/docs/Drone_Bench.pdf)), but tests slow indoor `DJITelloPy` velocity commands, **not** STM32 C firmware or inner-loop attitude/MRAC control (*"The drone moves at slow speeds, we tested a single office floorplan"* — [Drone-Bench PDF](https://andonlabs.com/docs/Drone_Bench.pdf)).

### 3. Video Understanding of Fast-Moving Objects & Flight Footage
* **1 FPS Bottleneck vs. Offline Timing Analysis:** A dedicated fast-moving aerial/flight video benchmark **does not exist** for Argon (**UNVERIFIED**). On **LVBench** (long-form video), Argon leads at **91.7%** vs. GPT-6 Astra (**87.5%**) and Opus 5.5 (**83.7%**), but Google explicitly samples video at **1 FPS** (*"LVBench results are self computed without tools. We use 1FPS for Gemini"* — [Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon)). Thus, Argon cannot directly track sub-second fast quadcopter maneuvers via raw video tokens; however, it excels at **offline multi-camera trajectory scoring** (28 segments over 60s: *"decompose the trajectory into 28 segments with positive, neutral, and negative progress"* — [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/)) and **writing GPU/OpenCV ground-truth phone-camera tracking scripts**.

### 4. Practical Uses for Your Quadcopter Stack
* **Control Theory & Sim-to-Real:** Argon translates control papers (*Whole-Body MPC* iLQR, 50 Hz PPO *HUSKY*, *DeepLoco*) directly into closed-loop physics simulations (*"ports the physics model and closed-loop controller... into self-contained MuJoCo WebAssembly browser labs"* — [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/)). This directly helps derive and test MRAC/EKF math in Python/MuJoCo before C port.
* **Flight-Log + Camera Diagnosis:** While PX4/ArduPilot/STM32 C benchmarks are **UNVERIFIED**, Argon's 1M-token context (**84.2%** on GraphWalks 256k–1M — [Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon)), overnight repo optimization (*"reducing session preparation overhead by 40% and cutting cold import times by 21%"* — [Google Devs Blog](https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/)), and closed-loop hardware log + video debugging (**58.9s → 17.4s** on SO-101 — [Google Devs Blog](https://developers.googleblog.com/gemini-4-argon-for-robotics/)) make it well-suited for correlating WiFi CSV telemetry with phone-camera ground truth and refactoring C/Python ground-station code—Though Opus 5.5 (**66.4%**) and GPT-6 Astra (**65.5%**) still lead Argon on Terminal-Bench 4.0 (**57.4%**) and FrontierSWE v2 (**55.0%**) ([Argon Evals](https://deepmind.google/models/evals-methodology/gemini-4-argon)).

## Sources
* https://developers.googleblog.com/gemini-4-argon-for-robotics/
* https://deepmind.google/models/evals-methodology/gemini-4-argon
* https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-4-Argon-Model-Card.pdf
* https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/
* https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world
* https://developers.googleblog.com/en/building-the-next-generation-of-physical-agents-with-gemini-robotics-er-1-5/
* https://deepmind.google/models/gemini-robotics/gemini-robotics-er/
* https://deepmind.google/models/model-cards/gemini-robotics-er-2
* https://andonlabs.com/
* https://andonlabs.com/evals/drone-bench
* https://andonlabs.com/docs/Drone_Bench.pdf
* https://andonlabs.com/evals/blueprint-bench-2

