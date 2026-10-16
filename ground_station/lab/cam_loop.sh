#!/usr/bin/env bash
# scrcpy record loop for camera_truth.sh (started through ground_station.lab.detach so it outlives the caller):
#   bash ground_station/lab/cam_loop.sh <video dir> "<scrcpy window flags>" <WxH>
# scrcpy exits when Android drops the camera (R4: "Camera disconnected" at frame 2313); restart into the next part.
# Part 1 keeps the R3 names (cam.mkv, scrcpy_start_host_time.txt); part k: cam_k.mkv, scrcpy_start_host_time_k.txt.
# The loop ends when <video dir>/stop exists.
vid=$1; win=$2; size=$3; k=1
while [ ! -f "$vid/stop" ]; do
  sfx=$([ $k = 1 ] || echo _$k)
  python -c "import time,datetime;t=time.time();print(f\"{t:.3f}\",datetime.datetime.fromtimestamp(t).isoformat())" > "$vid/scrcpy_start_host_time$sfx.txt"
  scrcpy --video-source=camera --camera-id=4 --camera-zoom=0.6 --camera-size=1920x1080 --camera-fps=30 \
    --no-audio --window-title=r5cam --window-width=${size%x*} --window-height=${size#*x} $win --record="$vid/cam$sfx.mkv" >> "$vid/scrcpy.log" 2>&1
  [ -f "$vid/stop" ] || echo "restart part $((k+1)) at $(date +%T)" >> "$vid/scrcpy.log"; k=$((k+1)); sleep 2
done

