#!/usr/bin/env bash
# Camera-truth session (R3 recipe): dashboard service + WiFi stream log (preset arg 3, default drift_rc) + phone camera video.
#   bash ground_station/lab/camera_truth.sh start <name>   # drone DISARMED; operator confirmed preset + name first
#   bash ground_station/lab/camera_truth.sh stop  <name>   # after landing + disarm
#   bash ground_station/lab/camera_truth.sh check <name>   # rows per slot, video size
#   bash ground_station/lab/camera_truth.sh fusion <name> <1|2|3> [tracker args]   # camera fusion, 1 shadow, 2 offset, 3 in-EKF:
#     stream_log --relay-port 47800 (or, with 8081 up for `fly`, its relay + --tel-url) + scrcpy r5cam (record, off screen) + live_tracker --show (topmost,
#     resizable, path/anchor map, --traj preset, demo clip tracker.mp4; keys r a c f);
#     `stop <name>` ends the recording, tracker and video (stop file); PlotJuggler keeps streaming live until
#     `end` or the next fusion. Floor pose: CT_FLOOR=<scan dir> (cam.mkv + aruco.npz of this placement), default r5_marks_1.
#   bash ground_station/lab/camera_truth.sh end            # end the live PlotJuggler stream (before starting 8081)
#   bash ground_station/lab/camera_truth.sh video <name>   # live window + recording only (workflows A/B/C keep their own log)
# WiFi only, never the SWD probe. Video: phone camera id 4, zoom 0.6, 1080p30 (the R3 calibration pose).
set -euo pipefail
cd "$(dirname "$0")/../.."
cmd=${1:?start|stop|check|fusion|video|end}; name=${2:-}; [ "$cmd" = end ] || : "${name:?session name}"; preset=${3:-drift_rc}; api=http://127.0.0.1:8081; vid=logs/video_truth/$name
post() { curl -s --noproxy "*" -m 10 -X POST -H 'Content-Type: application/json' -d "$2" "$api$1"; echo; }
up() { curl -s --noproxy "*" -m 2 -o /dev/null -w '%{http_code}' "$api/health" | grep -q 200; }
# Lab processes start through ground_station.lab.detach: their own hidden console, no parent left, so they outlive
# this shell (2026-10-14: 8081 and the scrcpy loop, `nohup ... &` from Claude Code's bash, died together at 18:51).
# Their .pid files hold Windows PIDs: alive/kill them with tasklist/taskkill, never bash kill.
bg() { python -m ground_station.lab.detach "$@" > /dev/null; }
alive() { local p; p=$(cat "$1" 2>/dev/null) || return 1; tasklist //FI "PID eq $p" //NH 2>/dev/null | grep -qi python; }
# start/video show the live window r5cam on top; fusion puts it off screen (the tracker window is the one on top).
cam() {                                           # CT_WIN: scrcpy window flags (default on top), CT_SIZE: WxH; loop: cam_loop.sh
  bg --log "$vid/cam_loop.out" --pid "$vid/cam_loop.pid" -- bash ground_station/lab/cam_loop.sh "$vid" "${CT_WIN---always-on-top}" "${CT_SIZE-1194x672}"
}
pj() {
  local layout=$1
  taskkill //F //IM plotjuggler.exe > /dev/null 2>&1 || pkill -f plotjuggler 2>/dev/null || true
  for cand in "${PJ_BIN:-}" "D:/PlotJuggler/plotjuggler.exe" "/mnt/d/PlotJuggler/plotjuggler.exe" "plotjuggler.exe" "plotjuggler"; do
    if [ -n "$cand" ] && { command -v "$cand" > /dev/null 2>&1 || [ -x "$cand" ] || [ -f "$cand" ]; }; then
      bg --log "$vid/plotjuggler.out" --pid "$vid/plotjuggler.pid" -- "$cand" -l "$layout" --start_streamer "UDP Server" --buffer_size 400 --nosplash
      break
    fi
  done
}
endlive() {                                         # a previous fusion's stream_log still streams to PlotJuggler: end it
  [ -f logs/plot_live.pid ] || return 0
  touch logs/plot_live.end
  for i in $(seq 10); do alive logs/plot_live.pid || break; sleep 1; done
  alive logs/plot_live.pid && taskkill //F //PID "$(cat logs/plot_live.pid)" > /dev/null 2>&1; rm -f logs/plot_live.pid logs/plot_live.end
}
case $cmd in
start)
  mkdir -p "$vid"
  if ! up; then
    bg --log "$vid/service.log" -- python -m ground_station.service --port 8081
    for i in $(seq 30); do up && break; sleep 1; done; up || { echo "service did not come up, see $vid/service.log"; exit 1; }
  fi
  body=$(python -c "import json;p=json.load(open('ground_station/vofa_studio/presets/$preset.json'));print(json.dumps({'slots':[{'slot':i+1,'vars':s['vars'],'rate':s['rate'],'name':'$preset'} for i,s in enumerate(p['slots'])]}))")
  out=$(post /api/streams/apply "$body"); echo "${out:0:300}"; case "$out" in *'"ok": false'*|*'"error"'*) echo "ABORT: preset $preset refused by apply (slots unchanged)"; exit 1;; esac
  post /api/streams/log/start "{\"name\":\"$name\",\"mode\":\"unlimited\"}"
  post /api/recording/start "{\"requested_by\":\"operator\",\"label\":\"$name\",\"reason\":\"camera-truth session\"}"
  rm -f "$vid/stop" "$vid/fusion" "$vid/video_only"
  cam
  sleep 5; "$0" check "$name" ;;
fusion)
  mode=${3:?mode 1 shadow, 2 offset, 3 in-EKF}; shift 3
  # 8081 up (`fly` flights): it owns the link, relays the fixes (--relay-port 47800) and logs; the tracker reads
  # height from it. 8081 down: stream_log owns the link, relays and logs, as before.
  if up; then link=8081; tel=(--tel-url "$api"); else link=sl; tel=(--tel-csv "logs/$name.slot1.csv"); endlive; fi
  mkdir -p "$vid" logs; rm -f "$vid/stop" "$vid/video_only"; echo "$mode" > "$vid/fusion"
  if [ "$link" = sl ]; then                         # `fly` sets up VOFA+/PJ itself (key p)
  VOFA=${VOFA-127.0.0.1:1347}                       # flight view to VOFA+ (UDP FireWater, named tabs); VOFA= turns it off
  PLOT=${PLOT-}                                     # PlotJuggler opt-in (every slot): PLOT=127.0.0.1:9870
  if [ -n "$VOFA" ]; then                           # writes C:/tmp/vofa import files only; VOFA+ is never closed or patched
    python -m ground_station.vofa_studio.vofa_config --frames ground_station/livewatch/r5_extpos_frames.md \
      || echo "VOFA+ view setup failed: flight goes on, VOFA+ shows raw channels"
  fi
  if [ -n "$PLOT" ]; then
    python -m ground_station.livewatch.pj_layout ground_station/livewatch/r5_extpos_frames.md --out "logs/$name.pj.xml"       && pj "logs/$name.pj.xml" || echo "PlotJuggler layout failed: flight goes on, open PJ by hand"
  fi
  ends=(--stop-file "$vid/stop")                   # with a live viewer: the stop file ends only the recording
  [ -n "$PLOT$VOFA" ] && ends=(--rec-stop-file "$vid/stop" --stop-file logs/plot_live.end)
  bg --log "$vid/stream_log.out" --pid "$vid/stream_log.pid" -- python -m ground_station.livewatch.stream_log --frames ground_station/livewatch/r5_extpos_frames.md     --relay-port 47800 ${PLOT:+--plot "$PLOT"} ${VOFA:+--vofa "$VOFA" --vofa-view} --seconds 86400 "${ends[@]}" --out "logs/$name.csv"
  [ -n "$PLOT$VOFA" ] && cp "$vid/stream_log.pid" logs/plot_live.pid
  fi
  # mirror off screen at the full 1920x1080 camera size, borderless (client area = video), still grabbed by
  # PrintWindow, so the tracker reads every camera pixel; CT_WIN= CT_SIZE=1194x672 shows it as before. UNVERIFIED:
  # Windows keeps a 1920x1080 window on a 1920x1080 screen unclamped (check the tracker line "window 'r5cam' ready 1920x1080").
  CT_SIZE="${CT_SIZE-1920x1080}" CT_WIN="${CT_WIN---window-x=-3000 --window-y=0 --window-borderless}" cam
  sleep 4                                           # scrcpy window opens 256x256, final size after ~1.1 s (measured)
  dly=${DELAY_MS:-200}                              # camera delay: video_truth delay4 lag 0.21 s on r6_f4 + f4c (2026-10-13)
  al=()                                             # r6 flew modes 2/3 at --yaw-deg 0 vs ~90 deg measured: auto-fit it
  case " $* " in *" --yaw-deg"*|*" --align"*) ;; *) [ "$mode" -ge 2 ] && al=(--align auto) ;; esac
  bg --log "$vid/tracker.out" --pid "$vid/tracker.pid" -- python -m ground_station.analysis.live_tracker --source window:r5cam     --cam docs/video-truth/cam_r4_lenswave_2026-10-11.json --floor "${CT_FLOOR:-logs/video_truth/r5_marks_1}"     "${tel[@]}" --detector coral --gate --send udp:47800 --mode "$mode" --delay-ms "$dly"     --out "$vid/fixes.csv" --show --show-rec "$vid/tracker.mp4" --stop-file "$vid/stop" "${al[@]}" "$@"
  sleep 12; "$0" check "$name" ;;
video)                                              # video only (workflows A/B/C): the log stays theirs
  mkdir -p "$vid"; rm -f "$vid/stop" "$vid/fusion"; touch "$vid/video_only"; cam
  sleep 5; "$0" check "$name" ;;
stop)
  touch "$vid/stop"
  if [ -f "$vid/fusion" ]; then                     # tracker exits; stream_log closes its CSVs (and streams on to PJ)
    for i in $(seq 20); do
      busy=0; alive "$vid/tracker.pid" && busy=1
      alive "$vid/stream_log.pid" && ! grep -q "recording stopped" "$vid/stream_log.out" && busy=1
      [ $busy = 0 ] && break; sleep 1
    done
  elif [ ! -f "$vid/video_only" ]; then
    post /api/recording/stop '{}'; post /api/streams/log/stop '{}'
  fi
  taskkill //F //IM scrcpy.exe > /dev/null 2>&1 || true; sleep 1; "$0" check "$name"   # PlotJuggler stays open for review
  [ -f logs/plot_live.pid ] && echo "VOFA+/PlotJuggler still streaming live: 'camera_truth.sh end' stops it" || true ;;
end) endlive; echo "live stream ended" ;;
check)
  for f in logs/vofa/"$name".slot*.csv logs/"$name".slot*.csv; do [ -f "$f" ] && echo "$f rows $(($(wc -l < "$f") - 1))"; done
  ls -la "$vid"/cam*.mkv 2>/dev/null || echo "no video yet"; { grep -c restart "$vid/scrcpy.log" 2>/dev/null || true; } | sed "s/^/restarts /"; tail -2 "$vid/scrcpy.log" 2>/dev/null
  { [ -f "$vid/fusion" ] && grep -m1 " ready " "$vid/tracker.out"; } || true   # grab size: 1920x1080 = full camera px
  [ -f "$vid/fusion" ] && { echo "fixes $(($(wc -l < "$vid/fixes.csv" 2>/dev/null || echo 1) - 1))"; tail -3 "$vid/tracker.out"; tail -2 "$vid/stream_log.out"; } || true ;;
esac
