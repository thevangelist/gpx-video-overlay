# gpx-video-overlay

minimalist telemetry overlay for gopro footage, driven by a strava `.gpx`.
speed · heart rate · elevation · live heatmap route. transparent png frames
composited over native video with ffmpeg. resolution-independent — authored at
1080p, scales cleanly to 4k.

![example](example.png)

## how

gpx is sampled by utc timestamp and aligned to the video start time
(`--video-start` + `--offset`). overlay redrawn once per data point (~1 hz), held
across the output framerate. static route + scrim pre-baked once, only the moving
dot and numbers redrawn per frame.

## use

```sh
pip install -r requirements.txt   # numpy + pillow ; also needs ffmpeg on PATH

# runs out of the box on the bundled synthetic sample -> still_overlay.png
python overlay.py --gpx sample.gpx --video-start 2026-01-01T12:00:00Z --still 120

# single still preview (overlay at video time = 113s) -> still_overlay.png
python overlay.py --gpx ride.gpx --video-start 2026-06-18T16:08:26Z \
                  --still 113 --offset -215

# 30s of frames -> frames/  (mux yourself, or use render_full.py)
python overlay.py --gpx ride.gpx --video-start 2026-06-18T16:08:26Z \
                  --start 0 --dur 30 --offset -215

# full ride: concat all chapters, composite, encode
python render_full.py --gpx ride.gpx --video-start 2026-06-18T16:08:26Z \
                      --chapters 'GX*.MP4' --offset -215
```

`--video-start` is the wall-clock UTC the recording began. `--offset` shifts gpx
vs video (seconds) to nail sync. chapter durations are probed with `ffprobe`, so
just point `--chapters` at a glob in order.

## style — everything is a flag

| flag | default | options |
|------|---------|---------|
| `--palette` | `turbo` | `turbo` `fire` `ice` `mono` |
| `--metric` | `speed` | `speed` `hr` (drives the heatmap + label) |
| `--font` | bundled Reddit Mono | any `.ttf` (variable or static) |
| `--width` `--height` | `3840 × 2160` | any; layout scales to height |
| `--fps` | `60` | output framerate |
| `--ofps` | `12` | overlay redraw rate (must divide `--fps`) |
| `--bitrate` `--vcodec` | `60M` `hevc_videotoolbox` | any ffmpeg codec/bitrate |

(`overlay.py` uses `--w`/`--h`; defaults to 1920×1080 for fast previews.)

quick partial render for sync/style checks:

```sh
python render_full.py --gpx ride.gpx --video-start 2026-06-18T16:08:26Z \
                      --quick-start 600 --quick-dur 20   # -> test.mp4
```

## layout

- bottom-left — heatmap route + moving position dot
- bottom-right — speed (big), elevation · hr (secondary)
- top-right — utc clock + offset readout

## notes

source footage, renders, frames and personal `.gpx` are gitignored — bring your
own (`sample.gpx` is synthetic demo data and kept in the repo).

## license

code: MIT — see [`LICENSE`](LICENSE).
font: reddit mono (variable), SIL Open Font License 1.1 — see
[`fonts/OFL.txt`](fonts/OFL.txt).

