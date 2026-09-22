#!/bin/bash
# Regression test for run-worker.sh's NET_DOWN detector. Extracts the live
# regex from the script (so the test cannot drift from the code) and runs it
# over strings that must alarm and strings that must not.
# Resolve relative to this script, so a worktree tests its own run-worker.sh
# rather than the main checkout's.
RW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/run-worker.sh"
# Pick the NET_DOWN grep specifically; run-worker.sh has an earlier
# `grep -m1 -iE` for NEEDS_INPUT that would otherwise match first.
RE="$(grep -oE "grep -m1 -iE '[^']+'" "$RW" | grep ECONNRESET | sed "s/^grep -m1 -iE '//; s/'$//")"
[ -n "$RE" ] || { echo "FAIL: could not extract regex"; exit 1; }

fail=0
must_hit() { printf '%s\n' "$1" | grep -qiE "$RE" || { echo "MISS (should alarm): $1"; fail=1; }; }
must_miss(){ printf '%s\n' "$1" | grep -qiE "$RE" && { echo "FALSE ALARM: $1"; fail=1; }; }

# Real network failures - must still alarm
must_hit 'Error: connect ECONNRESET 104.18.0.1:443'
must_hit 'fetch failed'
must_hit 'socket hang up'
must_hit 'AI_APICallError: status 429 Too Many Requests'
must_hit 'HTTP 503 Service Unavailable'
must_hit 'upstream error: code 502'
must_hit 'rate limit exceeded, retry in 6s'
must_hit '429 Too Many Requests'

# The false positives this fix is for - must stay quiet
must_miss '- Rate limiting: 1 req per 6s (Hetzner 10 req/min), "busy, try again" on cooldown'
must_miss 'ground_station/service/api.py:502:    return jsonify(payload)'
must_miss '  502  def _validate_request_headers(req):'
must_miss 'Added 429 lines to test_api.py'
must_miss 'tests passed in 503 seconds'
must_miss 'slot 504 configured'

[ "$fail" -eq 0 ] && echo "NET_DOWN regex: all cases OK"
exit "$fail"
