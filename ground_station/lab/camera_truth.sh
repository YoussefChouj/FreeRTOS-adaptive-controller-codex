#!/usr/bin/env bash
# Camera-truth session (R3 recipe): dashboard service + WiFi stream log (preset arg 3, default drift_rc) + phone camera video.
#   bash ground_station/lab/camera_truth.sh start <name>   # drone DISARMED; operator confirmed preset + name first
#   bash ground_station/lab/camera_truth.sh stop  <name>   # after landing + disarm
#   bash ground_station/lab/camera_truth.sh check <name>   # rows per slot, video size
#   bash ground_station/lab/camera_truth.sh fusion <name> <1|2> [tracker args]   # R5 camera fusion, 1 shadow 2 live:
#     stream_log --relay-port 47800 (no 8081) + scrcpy window r5cam (record, always on top) + live_tracker --show;
#     `stop <name>` ends all three cleanly (stop file). Same phone placement as r5_marks_1 (its floor pose is reused).
# WiFi only, never the SWD probe. Video: phone camera id 4, zoom 0.6, 1080p30 (the R3 calibration pose).
set -euo pipefail
cd "$(dirname "$0")/../.."
cmd=${1:?start|stop|check|fusion}; name=${2:?session name}; preset=${3:-drift_rc}; api=http://127.0.0.1:8081; vid=logs/video_truth/$name
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
  out=$(post /api/streams/apply "$body"); echo "${out:0:300}"; case "$out" in *'"ok": false'*|*'"error"'*) echo "ABORT: preset $preset refused by apply (slots unchanged)"; exit 1;; esac
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
fusion)
  mode=${3:?mode 1 shadow or 2 live}; shift 3
  up && { echo "8081 service is up: stop it first (stream_log needs the WiFi downlink)"; exit 1; }
  mkdir -p "$vid"; rm -f "$vid/stop"; echo "$mode" > "$vid/fusion"
  nohup python -m ground_station.livewatch.stream_log --frames ground_station/livewatch/r5_extpos_frames.md     --relay-port 47800 --seconds 86400 --stop-file "$vid/stop" --out "logs/$name.csv" > "$vid/stream_log.out" 2>&1 &
  echo $! > "$vid/stream_log.pid"
  nohup bash -c '
    vid=$1; k=1
    while [ ! -f "$vid/stop" ]; do
      sfx=$([ $k = 1 ] || echo _$k)
      python -c "import time,datetime;t=time.time();print(f\"{t:.3f}\",datetime.datetime.fromtimestamp(t).isoformat())" > "$vid/scrcpy_start_host_time$sfx.txt"
      scrcpy --video-source=camera --camera-id=4 --camera-zoom=0.6 --camera-size=1920x1080 --camera-fps=30         --no-audio --window-title=r5cam --always-on-top --record="$vid/cam$sfx.mkv" >> "$vid/scrcpy.log" 2>&1
      [ -f "$vid/stop" ] || echo "restart part $((k+1)) at $(date +%T)" >> "$vid/scrcpy.log"; k=$((k+1)); sleep 1
    done' _ "$vid" > /dev/null 2>&1 &
  sleep 4                                           # scrcpy window opens 256x256, final size after ~1.1 s (measured)
  nohup python -m ground_station.analysis.live_tracker --source window:r5cam     --cam docs/video-truth/cam_r4_lenswave_2026-10-11.json --floor logs/video_truth/r5_marks_1     --tel-csv "logs/$name.slot1.csv" --detector coral --gate --send udp:47800 --mode "$mode" --delay-ms 0     --out "$vid/fixes.csv" --show --stop-file "$vid/stop" "$@" > "$vid/tracker.out" 2>&1 &
  echo $! > "$vid/tracker.pid"
  sleep 12; "$0" check "$name" ;;
stop)
  touch "$vid/stop"
  if [ -f "$vid/fusion" ]; then                     # tracker + stream_log poll the stop file and exit cleanly
    for i in $(seq 20); do
      alive=0; for f in "$vid"/tracker.pid "$vid"/stream_log.pid; do kill -0 "$(cat "$f")" 2>/dev/null && alive=1; done
      [ $alive = 0 ] && break; sleep 1
    done
  else
    post /api/recording/stop '{}'; post /api/streams/log/stop '{}'
  fi
  taskkill //F //IM scrcpy.exe > /dev/null 2>&1 || true; sleep 1; "$0" check "$name" ;;
check)
  for f in logs/vofa/"$name".slot*.csv logs/"$name".slot*.csv; do [ -f "$f" ] && echo "$f rows $(($(wc -l < "$f") - 1))"; done
  ls -la "$vid"/cam*.mkv 2>/dev/null || echo "no video yet"; grep -c restart "$vid/scrcpy.log" 2>/dev/null | sed "s/^/restarts /"; tail -2 "$vid/scrcpy.log" 2>/dev/null
  [ -f "$vid/fusion" ] && { echo "fixes $(($(wc -l < "$vid/fixes.csv" 2>/dev/null || echo 1) - 1))"; tail -3 "$vid/tracker.out"; tail -2 "$vid/stream_log.out"; } || true ;;
esac
