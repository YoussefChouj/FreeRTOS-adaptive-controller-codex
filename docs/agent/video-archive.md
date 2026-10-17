# Where the camera videos are (read before opening any `logs/video_truth` video)

Since 2026-10-17 most old camera videos (`.mkv`, `.mp4`) are on Google Drive, not on D:. Everything else stays on D:.

| What | Where |
|---|---|
| Old flight videos (`cam.mkv`, `cam_k.mkv`, `tracker.mp4`, `camera_*.mkv`) | `G:/My Drive/UAV_lab/<YYYY-MM-DD>/<folder>/<file>` (day = the folder's newest file) |
| Their CSV, npz, json, txt, png (fixes.csv, truth4.csv, coral caches, summaries) | still `logs/video_truth/<folder>/` on D: |
| Floor scans (folders with `aruco.npz`, e.g. `r5_marks_1` = the tracker's `CT_FLOOR`) | still on D:, never moved |
| Which file went where | `docs/lab/video-archive.csv` (tracked: day,folder,file,bytes,drive_path) and `logs/video_truth/<folder>/ARCHIVED.txt` |
| Videos moved by `lab end` at the end of a lab day | same Drive layout; listed in `logs/lab/<day>/videos_moved.txt` |

`G:` is Google Drive for desktop (streaming mode, cache on `D:\GoogleDriveCache`, so reads never fill C:). A video
on G: opens like a local file; Drive downloads it on first read (~0.5 GB per flight, takes a while over WiFi).

## Rules for code and agents

- Open a flight video with `video_truth.video_file(<folder>)` (or `vt.video_file`), never `str(folder / "cam.mkv")`:
  it returns the local file, else the Drive copy. `video_truth.py` and `live_tracker.py` already do.
- A missing `cam.mkv` on D: is NOT lost data: look in `docs/lab/video-archive.csv` or `ARCHIVED.txt` first.
  Never re-record or delete a folder because its video is gone.
- Heavy re-processing of many videos (coral re-track, NN training on frames): copy them back first
  (`robocopy "G:\My Drive\UAV_lab\<day>\<folder>" logs\video_truth\<folder>`), or run the job in Colab on the Drive
  copy (docs/agent/ultra-setup.md step 3).
- Not on G: (WSL, VPS, cloud, Jules): the videos are not reachable there; the CSV index still says what exists.
- Never delete anything under `G:/My Drive/UAV_lab`: it is the only copy.

## Moving more later

`python -m ground_station.lab archive [--days 7] [--yes]` (ground_station/lab/archive.py). Dry run by default; each
video is copied, size-checked, then removed from D:; skips floor scans and a folder whose tracker still runs.
Frees D: only (C: is full of app data, not videos). Background: docs/agent/ultra-setup.md.
