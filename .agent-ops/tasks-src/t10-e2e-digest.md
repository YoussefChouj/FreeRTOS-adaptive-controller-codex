# T10: read-only digest for E2E validation

This task is read-only. Do not edit source files, do not contact 127.0.0.1:8081, and do not touch the probe or run livewatch. Write the digest to `.agent-ops/out/t10-digest.md` and commit only that file.

Answer each question with file:line references and short quoted code. Keep the whole digest under 150 lines.

1. **mrac_simplex writers.** List every place in `API/` and `TASK/` that writes to `mrac_simplex` or any field of it, including memset/memcpy, init functions and struct assignments. Show the struct definition, with field order and types. Could any path write `hold_ticks` = 0 at runtime (for example a reset of counters on CMD 0x19 idx 7, a re-init, or a union overlap)? The CMD 0x19 handler is at `TASK/send_data.c` ~1758.
2. **Co-pilot.** Where does the dashboard "co-pilot" or chat live: a service route, a plugin JS under `docs/dashboard-platform/`, a separate process, or `/api/agent/message`? How does a client send a message and get a reply (route, method, body, and where the reply appears)? Which LLM backend or config does it need (env var, key file)?
3. **Terminal WS token.** Where does the service create or validate the `token` for `GET /api/terminal/ws`? How does the browser plugin (`terminal-panel.js`) obtain it? What is the WS message protocol (input, output, resize)?
4. **Sim.** Is there any simulator or dry-run mode for workflows or plans (grep for sim, dry_run, dryrun, simulate, mock backend, replay-as-source)? How is it invoked?
5. **Memory growth in the service.** Look at `ground_station/service/` for unbounded containers: lists, dicts or deques without maxlen that grow per telemetry frame, per request, per recording or per websocket client. Also look for repeated DWARF/ELF parsing (pyelftools DIE/CompileUnit objects) per request or per plan. List candidates with file:line and why each one grows.
