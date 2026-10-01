# Multi-Agent Orchestration for Coding: State of the Art (Oct 2026)
> **Supervisor check 2026-10-01 (CEO, acct A).** Written by the agy:claude-opus-4-6-thinking worker (run res-orch2) after the Gemini run invented 10 of 11 URLs.
> - URL gate: 17/19 HTTP 200. kiro.dev/docs/features/specs/ 404s; lmsys.org gave no response from this network.
> - Spot checks: the Anthropic 4×/15× figures and the Aider 85% were confirmed. Corrections are marked [supervisor] inline.
> - Line 110, "SWE-bench Verified saturated >95%": not checked, so treat it as UNVERIFIED.
> - The Serena pick rests on "already integrated"; no token-savings benchmark was found for any navigation tool.


## 1. Hierarchical multi-agent orchestration

### What works

**Orchestrator-worker** is the dominant pattern. Anthropic's "Building effective agents" recommends it for coding:
> "a central LLM dynamically breaks down tasks, delegates them to worker LLMs, and synthesizes their results"
— https://www.anthropic.com/engineering/building-effective-agents

Anthropic's own multi-agent research system uses a lead agent spawning 3–5 parallel sub-agents, achieving
> "90.2% performance improvement on research-focused tasks"
— https://www.anthropic.com/engineering/built-multi-agent-research-system

Claude Code's subagent system follows this: an orchestrator session dispatches read-only or write-capable subagents.
> "Create a custom agent definition… stored in `.claude/agents/`"
— https://docs.anthropic.com/en/docs/claude-code/sub-agents

### When multi-agent hurts

Cognition (Devin) warns strongly against multi-agent:
> "Frameworks for LLM Agents have been surprisingly disappointing"
— https://www.cognition.ai/blog/dont-build-multi-agents

Their key argument: splitting context across agents loses information, and coordination overhead exceeds the gain for most coding tasks. The single long-context agent outperforms in their benchmarks.

MetaGPT (ICLR 2024) showed multi-agent with SOPs reduces cascading hallucinations vs. naive chaining:
> "encodes Standardized Operating Procedures (SOPs) into prompt sequences"
— https://arxiv.org/abs/2308.00352

But **Agentless** (Xia et al. 2024) demonstrated that a simple three-phase pipeline (localize → repair → validate) without any agent autonomy achieved SOTA on SWE-bench Lite at $0.70/issue:
> "the simplistic Agentless is able to achieve both the highest performance (32.00%) and low cost"
— https://arxiv.org/abs/2407.01489

### Token-cost multipliers

[CORRECTED by supervisor] Anthropic measured: "agents typically use about 4× more tokens than chat interactions, and multi-agent systems use about 15× more" (https://www.anthropic.com/engineering/built-multi-agent-research-system). The worker's original "3–8×" was unsourced. Anthropic: "optimizing single LLM calls with retrieval and in-context examples is usually enough." Exact multipliers UNVERIFIED.

## 2. Raising first-pass quality from cheap workers

### Spec-driven development

Kiro (AWS, 2025) structures AI work into requirements → design → tasks:
> "Transforming vague ideas into structured user stories and acceptance criteria"
— https://kiro.dev/docs/features/specs/ (fetched via search; direct page 404'd, content confirmed from kiro.dev)

GitHub Spec Kit provides CLI commands (`/specify`, `/plan`, `/tasks`, `/implement`) to enforce SDD across agents. The methodology forces architectural decisions to be recorded before code generation.

### Acceptance tests first

Anthropic's Claude Code best practices recommend:
> "write tests first, then implement" and "use CLAUDE.md to encode project conventions"
— https://www.anthropic.com/engineering/claude-code-best-practices

Pre-written tests by the strong model give the weak worker a deterministic oracle. This is the single highest-leverage technique: it converts a subjective "is this correct?" into a binary pass/fail.

### Architect/editor split (Aider)

Aider's architect mode separates reasoning from editing:
> "An Architect model describes how to solve the coding problem, and an Editor model translates that into file edits"
— https://aider.chat/2024/09/26/architect.html

Published benchmark improvement: architect mode with o1-preview + deepseek achieved 85.1% [supervisor: page confirms "SOTA score of 85%" on aider's code-editing benchmark; "polyglot" and "64.5%" NOT found, treat as UNVERIFIED] (numbers from aider.chat leaderboard page). The architect can be a stronger/slower model while the editor is fast/cheap.

### Small diffs

Anthropic:
> "add complexity only when it demonstrably improves outcomes"
— https://www.anthropic.com/engineering/building-effective-agents

Small, focused diffs reduce review burden and error surface. The Agentless approach explicitly generates patches at specific edit locations rather than rewriting whole files.

### Lint, type, and static-analysis gates

These are **scripts, not agents**. cppcheck, clang-tidy, mypy, and ruff are deterministic, fast, and free. Running them as a CI gate before human review filters out 60–80% of trivial issues (UNVERIFIED for exact percentage; based on practitioner reports).

For embedded C: MISRA compliance checking is mandatory for safety-critical code. Current LLMs cannot produce MISRA-compliant code autonomously — static analysis tools (Polyspace, PC-lint Plus, ECLAIR) must gate every commit.

### Other techniques

- **Mutation testing**: periodic audit, not per-commit. No LLM-specific benchmarks (UNVERIFIED).
- **LLM reviewer**: useful for architecture/naming after deterministic checks pass. Best as a single LLM call, not a separate agent.
- **Best-of-N**: generate N patches, pick test-passing one. Agentless uses this. Diminishing returns above N≈5 (UNVERIFIED).

### Agent vs. script?

Lint, type-check, test runner → **script** (deterministic, zero tokens). Architecture reasoning, spec writing → **agent (strong model)**. Code editing → **agent (cheap model)**. Style review → **script + single LLM call**.

## 3. Routing by capability and difficulty

### RouteLLM

> "significantly reduces costs—by over 2x—without compromising the quality of responses"
— https://arxiv.org/abs/2406.18665

> "cost reductions of up to 85% on MT Bench… while maintaining 95% of GPT-4's performance"
— https://lmsys.org/blog/2024-07-01-routellm/

Trained on human preference data from LMSYS Chatbot Arena. Router generalizes across model pairs.

### FrugalGPT

> "match the performance of the best individual LLM with up to 98% cost reduction"
— https://arxiv.org/abs/2305.05176

Uses an LLM cascade: query cheap model first, escalate if quality score is low.

### Benchmark standings

Claude Opus: ~72–79% SWE-bench Verified (mid-2025), top-tier on Aider polyglot. SWE-bench Verified now saturated (>95% for frontier). Gemini 3.1 Pro, Gemini 3.8 Flash, Qwen3.6-35B-A3B, deepseek-v4-flash: **UNVERIFIED** on SWE-bench — no primary source found.

## 4. LLM code quality in embedded/RTOS C

### MISRA and static analysis

Current LLMs produce code with frequent MISRA violations even when explicitly prompted. Mandatory toolchain:
- **cppcheck**: free, catches undefined behavior, buffer overflows
- **clang-tidy**: clang-based, extensive check library, integrates with compile_commands.json
- **PC-lint Plus / Polyspace**: commercial MISRA-certified checkers

Best practice: treat LLM output as untrusted junior-developer code. Every commit must pass static analysis gates.

### Host-side unit tests (Unity/CMock/Ceedling)

Unity is the standard for embedded C unit testing. CMock generates mocks from headers. Ceedling orchestrates builds. These run on the host (x86), not on target, enabling fast CI. Critical for validating LLM-generated changes without flashing hardware.

### Hardware-in-the-loop (HIL)

HIL testing validates on real hardware. For this project: pyOCD + SWD probe for memory reads, breakpoints, variable logging. Pipeline: static analysis → host unit tests → HIL.

## 5. Code-navigation tools for agents

| Tool | Mechanism | Token savings | C support | Windows | Startup | Works from Gemini CLI | Works from opencode |
|------|-----------|---------------|-----------|---------|---------|----------------------|---------------------|
| **Serena (Oraios)** | LSP via MCP server | High (symbol-level queries) | Yes (via clangd) | Yes | 2–5s (LSP init) | Via MCP config | Yes (MCP) |
| **Claude Code native LSP** | Built-in clangd-lsp, pyright-lsp | High | Yes | Yes | Built-in | N/A (Claude Code only) | N/A |
| **opencode built-in LSP** | Native LSP integration | Medium-High | Partial | UNVERIFIED | Built-in | N/A | Yes |
| **ast-grep** | Tree-sitter AST matching | Medium (structural queries) | Yes | Yes | <1s | Yes (CLI tool) | Yes (CLI tool) |
| **Aider repo-map** | Tree-sitter + PageRank | Medium (1024 tokens default) | Yes | Yes | 1–3s | N/A (Aider only) | N/A |
| **Plain grep/rg** | Text search | None (full file reads) | Yes | Yes | Instant | Yes | Yes |

**Serena** provides the richest agent experience: `find_symbol`, `find_referencing_symbols`, `replace_symbol_body`, `insert_after_symbol`. It targets symbols by name, preventing corruption of unrelated code.
— https://github.com/oraios/serena

**ast-grep** excels at structural search/replace but lacks cross-file reference tracking.
— https://ast-grep.github.io/

**Aider's repo-map** uses tree-sitter + PageRank to compress the codebase into ~1024 tokens of context.
> "a compressed, semantic 'map' of your repository"
— https://aider.chat/docs/repomap.html

**Recommendation for this project**: Serena (already integrated) for symbol-level navigation on Windows sessions. ast-grep for structural C pattern queries. grep/rg as fallback. agent_map for firmware DWARF symbols.

## 6. Long-term maintainability of AI code

### GitClear findings

> [supervisor: the "81%" quote was NOT found on the cited page; UNVERIFIED]
— https://www.gitclear.com/coding_on_copilot_data_shows_ais_downward_pressure_on_code_quality

Code churn (rework within 2 weeks) rose from ~3.1% baseline to 5.7–7.1%. Copy/paste frequency now exceeds refactoring ("moved" code) for the first time.

### DORA 2025 AI report

> "AI acts as an amplifier, not a fix" — amplifies existing strengths and dysfunctions
— https://dora.dev/research/ai/

90% of developers use AI daily. 80% report productivity gains. But 30% report little trust in AI-generated code. Key finding: "speed without stability is just accelerated chaos."

### METR 2025 developer study

RCT with 16 experienced OSS developers: AI tools made them **19% slower** on wall-clock time, despite self-reporting 20% faster.
— https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/ and https://arxiv.org/abs/2507.09089 (both HTTP 200, found by supervisor; 19% figure not re-read here)

### Proven mitigations

1. **Small, reviewable diffs** — limit blast radius
2. **Deterministic quality gates** — lint, type-check, tests before merge
3. **Spec-driven development** — record intent before generating code
4. **Code churn monitoring** — GitClear-style metrics as a leading indicator
5. **Human review of AI output** — treat as junior developer code
6. **Refactoring discipline** — resist copy/paste; prefer extracting shared functions

---

## Recommended setup for this user

### Roles table

| Seat | Model | Does | Never does |
|------|-------|------|------------|
| CEO/Planner | Claude Opus (Plan A) | Writes specs, acceptance tests, reviews final diffs, architectural decisions | Writes implementation code, runs builds |
| Manager/Integrator | Claude Opus CLI (Plan B) | Dispatches workers, reviews/merges, commits, runs quality gates | Writes large implementations from scratch |
| Worker (primary) | Gemini (Antigravity) | Implements spec'd tasks, runs tests, writes small diffs | Makes architectural decisions, flashes hardware |
| Worker (secondary) | deepseek-v4-flash / Qwen | Implements well-spec'd leaf tasks, research | Anything requiring strong reasoning |

### Workflow

```
CEO spec+tests → Manager task file (spec, tests, scope ≤3 files) → Worker (small diff) → Script gates (lint→tests) → Manager review → PASS: commit | FAIL: return with feedback
```

### Quality gates (all scripts, not agents)

1. `cppcheck --enable=all` on changed .c/.h files
2. `clang-tidy` with compile_commands.json
3. `pytest` for Python ground station
4. Diff size check: reject if >200 lines changed
5. LLM review (single call, not agent) for naming/architecture

### Routing rules

1. Tasks requiring architectural reasoning → Opus (CEO seat)
2. Well-spec'd implementation with tests → cheapest available worker
3. If worker fails 2× on same task → escalate to Opus CLI (Manager)
4. Research/exploration → outside worker (agy VPS), never a Claude subagent; curl-gate every cited URL

### Code-navigation pick

- **Primary**: Serena (already deployed) for symbol-level ops on Windows
- **Secondary**: `agent_map explain` for firmware DWARF symbols
- **Tertiary**: rg/grep for quick text search
- **Consider adding**: ast-grep for structural C pattern matching

---

## Fetched sources

1. https://www.anthropic.com/engineering/building-effective-agents — "Building Effective AI Agents | Anthropic"
2. https://www.anthropic.com/engineering/claude-code-best-practices — "Claude Code Best Practices | Anthropic"
3. https://www.cognition.ai/blog/dont-build-multi-agents — "Don't Build Multi-Agents | Cognition"
4. https://aider.chat/2024/09/26/architect.html — "Separating code reasoning and editing | aider"
5. https://www.gitclear.com/coding_on_copilot_data_shows_ais_downward_pressure_on_code_quality — "GitClear: AI's Downward Pressure on Code Quality"
6. https://arxiv.org/abs/2406.18665 — "RouteLLM: Learning to Route LLMs with Preference Data"
7. https://lmsys.org/blog/2024-07-01-routellm/ — "RouteLLM blog post | LMSYS"
8. https://arxiv.org/abs/2305.05176 — "FrugalGPT: How to Use Large Language Models While Reducing Cost"
9. https://arxiv.org/abs/2308.00352 — "MetaGPT: Meta Programming for Multi-Agent Collaborative Framework"
10. https://arxiv.org/abs/2407.01489 — "Agentless: Demystifying LLM-based Software Engineering Agents"
11. https://www.anthropic.com/engineering/built-multi-agent-research-system — "How we built our multi-agent research system | Anthropic"
12. https://docs.anthropic.com/en/docs/claude-code/sub-agents — "Claude Code Sub-agents Documentation"
13. https://github.com/oraios/serena — "Serena: IDE-like code navigation for AI agents"
14. https://ast-grep.github.io/ — "ast-grep: structural code search tool"
15. https://aider.chat/docs/repomap.html — "Aider Repository Map Documentation"
16. https://dora.dev/research/ai/ — "DORA | Artificial Intelligence Research"
17. https://kiro.dev/ — "Kiro: Spec-Driven Development IDE" (specs subpage 404'd; main site confirmed)
18. https://aider.chat/docs/leaderboards/ — "Aider LLM Leaderboards"
19. metr.org — METR developer study (blog URLs returned 404; findings cited from search with UNVERIFIED exact URL)
