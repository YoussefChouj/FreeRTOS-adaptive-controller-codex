#!/bin/sh
# Keep the 8081 -> VPS:18081 reverse tunnel alive. Before each connect, kill
# orphaned tunnel sshd sessions (agent-owned, not the killer's own) that hold the port.
while true; do
  ssh -o ConnectTimeout=15 oc-agent 'me=$(ps -o ppid= -p $$ | tr -d " "); for p in $(ps -u agent -o pid=,cmd= | awk "/sshd: agent\$/{print \$1}"); do [ "$p" != "$me" ] && kill $p; done; true' 2>/dev/null
  sleep 2
  echo "$(date +%T) connect"
  ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -R 18081:127.0.0.1:8081 oc-agent
  echo "$(date +%T) exit $?"
  sleep 5
done
