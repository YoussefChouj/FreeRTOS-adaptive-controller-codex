# Quota Probe Findings: Antigravity CLI (agy) and Volcengine Ark

## 1. Antigravity CLI (agy)

### CLI Flags & Subcommands
- Command: `agy --help` and `agy <subcmd> --help`.
- Result: No flags for `usage`, `quota`, or `status` exist. Subcommands are `agent`, `agents`, `changelog`, `help`, `install`, `mcp`, `mic-serve`, `models`, `plugin`, `plugins`, `remote-control` (has `status` for daemon only), and `update`.

### Local State & Config Files
- Searched: `~/.gemini`, `~/.antigravity` (absent), `~/.config`, and `~/.cache`.
- Files found under `~/.gemini`:
  - `antigravity-cli/cache/last_conversations.json` (keys: workspace directory paths)
  - `antigravity-cli/cache/onboarding.json` (keys: `consumerOnboardingComplete`, `enterpriseOnboardingComplete`, `onboardingComplete`)
  - `antigravity-cli/updater/update_status.json` (keys: `success`, `message`)
  - `config/projects/default-cli-project.json` (keys: `id`, `name`, `projectResources`)
- Result: No quota numbers or remaining tokens are stored in local files. Quota is fetched remotely and held in-process in memory (`Cache(retrieveUserQuotaSummary)`).

### Running `/usage` Headless
- Command: `agy --output-format json -p "/usage"` (or plain `agy -p "/usage"`).
- Exact output (`agy -p "/usage"`):
```
Quota:
Gemini Models          Weekly Limit Remaining     98%   2026-10-02T15:17:50Z
Gemini Models          Five Hour Limit Remaining  90%   2026-09-25T20:17:50Z
Claude and GPT models  Weekly Limit Remaining     100%  2026-10-02T16:09:17Z
Claude and GPT models  Five Hour Limit Remaining  100%  2026-09-25T21:09:17Z
```
- Behavior: Runs headlessly with exit code 0. Consumes 0 tokens (`num_turns: 0, total_tokens: 0`). With `--output-format json`, outputs structured JSON including `remaining_fraction` (float) and `reset_time` (ISO 8601 string) per bucket (`gemini-weekly`, `gemini-5h`, `3p-weekly`, `3p-5h`).

### HTTP Endpoint
- Source: CLI logs (`~/.gemini/antigravity-cli/log/cli-*.log`) and binary strings (`~/.local/bin/agy`).
- Quota endpoint: `POST https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary`
  (RPC `RetrieveUserQuotaSummary`, returns `QuotaSummaryBucket` items with `remaining_fraction` and reset timestamp).
- State initialization: `POST https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist`.

---

## 2. Volcengine Ark ("Agent Plan" / "ArkCoding")

### Documented API & Console Endpoints
- Control-Plane OpenAPI:
  - Gateway: `https://open.volcengineapi.com` (Region: `cn-beijing`, Service: `ark`).
  - Auth: Volcengine Signature V4 (HMAC-SHA256) requiring account-level `AccessKey` (AK) and `SecretKey` (SK). Data-plane inference keys (`ark-...` / `ARK_CODING_PLAN_API_KEY`) cannot call this endpoint (fail with `400 InvalidAuthorization`).
  - Action for Coding Plan: `Action=GetCodingPlanUsage&Version=2024-01-01`. Returns `QuotaUsage` array with `Level` (`session` [5h window], `weekly`, `monthly`), `Percent`, and `ResetTimestamp`.
  - Action for Agent Plan: `Action=GetAFPUsage&Version=2024-01-01` (or `GetSeatAFPUsage` for seats). Returns remaining AFP budget and reset timestamps across 5h, weekly, and monthly windows.
- CLI: `arkcli usage plan --product coding-plan` (uses AK/SK configuration).
- Console: Volcengine Ark Console -> Coding Plan / Agent Plan management dashboard.

---

## 3. Quota Exhaustion Errors & Reset Times

### Antigravity CLI (agy)
- HTTP Status: `HTTP 429 Too Many Requests` (gRPC status 8 `RESOURCE_EXHAUSTED`).
- Exact error text: `RESOURCE_EXHAUSTED: quota exceeded` or `Resource has been exhausted (e.g. check quota).`
- Reset time stated: No, the model generation call error does not state the reset timestamp. However, scripts can query `agy --output-format json -p "/usage"` at any time to obtain `reset_time`.

### Volcengine Ark
- HTTP Status: `HTTP 429 Too Many Requests`.
- Exact error text:
  - Burst/rate limits: `"System protection triggered by request burst"` or `TooManyRequests` / `InflightBatchsizeExceeded`. Returns `Retry-After` header (seconds).
  - Quota window exhausted: `QuotaExceeded` or `TooManyRequests` ("触发频率限制/滑动窗口耗尽").
- Reset time stated: Inference endpoints do not state the window reset timestamp in the error body. The reset timestamp is only retrievable via OpenAPI (`ResetTimestamp` in `GetCodingPlanUsage` / `GetAFPUsage`) or the web console.

---

## Recommendation

1. **agy**: Run `agy --output-format json -p "/usage"`. It executes headlessly in <2s, consumes 0 tokens, and returns machine-readable JSON containing exact remaining fractions and ISO 8601 `reset_time` values for both 5h and weekly pools.
2. **Ark**: For inference keys alone, there is no zero-cost query endpoint; detect exhaustion via HTTP 429 (`QuotaExceeded` / `RESOURCE_EXHAUSTED`). To inspect remaining quota and reset timestamps programmatically, configure Volcengine IAM AK/SK and call `https://open.volcengineapi.com` with `Action=GetCodingPlanUsage` or `Action=GetAFPUsage`.

SUBSTITUTIONS: none
