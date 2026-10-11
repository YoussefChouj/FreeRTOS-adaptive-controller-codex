#!/usr/bin/env bash
# Camera-truth session (R3 recipe): dashboard service + WiFi stream log (preset arg 3, default drift_rc) + phone camera video.
#   bash ground_station/lab/camera_truth.sh start <name>   # drone DISARMED; operator confirmed preset + name first
#   bash ground_station/lab/camera_truth.sh stop  <name>   # after landing + disarm
#   bash ground_station/lab/camera_truth.sh check <name>   # rows per slot, video size
# WiFi only, never the SWD probe. Video: phone camera id 4, zoom 0.6, 1080p30 (the R3 calibration pose).
set -euo pipefail
cd "$(dirname "$0")/../.."
cmd=${1:?start|stop|check}; name=${2:?session name}; preset=${3:-drift_rc}; api=http://127.0.0.1:8081; vid=logs/video_truth/$name
post() { curl -s -m 10 -X POST -H 'Content-Type: application/json' -d "$2" "$api$1"; echo; }
up() { curl -s -m 2 -o /dev/null -w '%{http_code}' "$api/health" | grep -q 200; }
case $cmd in
start)
  mkdir -p "$vid"
  if ! up; then
    nohup python -m ground_station.service --port 8081 > "$vid/service.log" 2>&1 &
    for i in $(seq 30); do up && break; sleep 1; done; up || { echo "service did not come up, see $vid/service.log"; exit 1; }
  fi
  body=$(python -c "import json;p=json.load(open('ground_station/vofa_studio/presets/$preset.json'));print(json.dumps({'slots':[{'slot':i+1,'vars':s['vars'],'rate':s['rate'],'name':'$preset'} for i,s in enumerate(p['slots'])]}))")
  post /api/streams/apply "$body"
  post /api/streams/log/start "{\"name\":\"$name\",\"mode\":\"unlimited\"}"
  post /api/recording/start "{\"requested_by\":\"operator\",\"label\":\"$name\",\"reason\":\"camera-truth session\"}"
  rm -f "$vid/stop"
  # scrcpy exits when Android drops the camera (R4: "Camera disconnected" at frame 2313); restart into the next part.
  # Part 1 keeps the R3 names (cam.mkv, scrcpy_start_host_time.txt); part k: cam_k.mkv, scrcpy_start_host_time_k.txt.
  nohup bash -c '
    vid=$1; k=1
    while [ ! -f "$vid/stop" ]; do
      sfx=$([ $k = 1 ] || echo _$k)
      python -c "import time,datetime;t=time.time();print(f\"{t:.3f}\",datetime.datetime.fromtimestamp(t).isoformat())" > "$vid/scrcpy_start_host_time$sfx.txt"
      scrcpy --video-source=camera --camera-id=4 --camera-zoom=0.6 --camera-size=1920x1080 --camera-fps=30         --no-audio --no-playback --no-window --record="$vid/cam$sfx.mkv" >> "$vid/scrcpy.log" 2>&1
      [ -f "$vid/stop" ] || echo "restart part $((k+1)) at $(date +%T)" >> "$vid/scrcpy.log"; k=$((k+1)); sleep 1
    done' _ "$vid" > /dev/null 2>&1 &
  sleep 5; "$0" check "$name" ;;
stop)
  post /api/recording/stop '{}'; post /api/streams/log/stop '{}'
  touch "$vid/stop"; taskkill //F //IM scrcpy.exe > /dev/null 2>&1 || true; sleep 1; "$0" check "$name" ;;
check)
  for f in logs/vofa/"$name".slot*.csv; do [ -f "$f" ] && echo "$f rows $(($(wc -l < "$f") - 1))"; done
  ls -la "$vid"/cam*.mkv 2>/dev/null || echo "no video yet"; grep -c restart "$vid/scrcpy.log" 2>/dev/null | sed "s/^/restarts /"; tail -2 "$vid/scrcpy.log" 2>/dev/null ;;
esac
