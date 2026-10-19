---
trigger: model_decision
description: Load when debugging complex state machines, non-linear systems, concurrency bugs, or multi-module architectural refactors
---
# Deep Debugging & Verification Protocol
1. First dispatch `codebase-scout` via `invoke_subagent` to build a call-graph and state-mutation map with exact file:line coordinates.
2. Read only the identified line slices in the main thread.
3. Formulate a falsifiable root-cause hypothesis and verify it with a single targeted reproduction test via `test-runner` before applying broad fixes.