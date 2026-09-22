# Task: Implement Agent Map Step 3 (dashboard MCP tool)

The step 1 agent map script (`ground_station.agent_map`) is ready. Now you need to expose it to the agents via the MCP server.

## Instructions
1. Edit `ground_station/service/agent_mcp.py` to add a new MCP tool called `explain_symbol`.
2. The tool should wrap the step 1 `ground_station.agent_map.explain.explain(name)` logic.
3. Crucially, the tool should ALSO fetch the *live* value of the symbol by talking to the existing dashboard service read path via `_http("GET", f"/api/read/{name}")` (or however the service reads live values).
4. Combine the explanation and the live value into the tool's text response.
5. Exit cleanly when done.
