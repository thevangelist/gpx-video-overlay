#!/usr/bin/env python3
"""Full-ride render: concat GoPro chapters at native resolution, composite the
minimalist heatmap overlay, stream overlay frames into ffmpeg.

The overlay is recomputed only at --ofps (the gpx data is ~1 Hz, so there is no
point redrawing it 60x/s); each frame's bytes are repeated to fill the output
framerate. The static route is pre-baked once. Chapter durations are probed with
ffprobe, so you only list the files.

  python render_full.py --gpx ride.gpx --video-start 2026-06-18T16:08:26Z --offset -215
"""
import os, glob, time, argparse, subprocess
from datetime import datetime
from overlay import (parse_start, prep, make_fonts, projector, bake_static,
                     draw_dynamic, PALETTES, DESIGN_H, DEFAULT_FONT)

def probe_dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path])
    return float(out.strip())

def main():
    ap = argparse.ArgumentParser(description="Composite GPX overlay over GoPro footage.")
    ap.add_argument("--gpx", default="ride.gpx")
    ap.add_argument("--video-start", required=True,
                    help="ISO8601 UTC time the video started, e.g. 2026-06-18T16:08:26Z")
    ap.add_argument("--chapters", default="GX*.MP4",
                    help="glob (or space-separated list) of video chapters in order")
    ap.add_argument("--offset", type=float, default=0.0, help="gpx-vs-video sync shift (s)")
    ap.add_argument("--metric", choices=["speed", "hr"], default="speed")
    ap.add_argument("--width", type=int, default=3840); ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--fps", type=int, default=60, help="output framerate")
    ap.add_argument("--ofps", type=int, default=12, help="overlay redraw rate (held across output fps)")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--palette", choices=list(PALETTES), default="turbo")
    ap.add_argument("--bitrate", default="60M")
    ap.add_argument("--vcodec", default="hevc_videotoolbox")
    ap.add_argument("--out", default="overlay_render.mp4")
    ap.add_argument("--quick-start", type=float, default=0.0, help="preview: seek to (s)")
    ap.add_argument("--quick-dur", type=float, default=None, help="preview: render only (s) -> test.mp4")
    args = ap.parse_args()

    chapters = sorted(glob.glob(args.chapters)) if any(c in args.chapters for c in "*?[") \
        else args.chapters.split()
    if not chapters:
        raise SystemExit(f"no chapters matched: {args.chapters!r}")
    durs = [probe_dur(c) for c in chapters]
    total = sum(durs)
    print(f"chapters: {len(chapters)}  total {total/60:.1f} min")

    start_utc = parse_start(args.video_start)
    W, H = args.width, args.height
    S = H / DESIGN_H
    if args.fps % args.ofps:
        raise SystemExit(f"--fps ({args.fps}) must be a multiple of --ofps ({args.ofps})")

    t, lat, lon, ele, hr, spd, mvals, vmin, vmax = prep(args.metric, args.gpx)
    fonts = make_fonts(args.font, S)
    proj, route, ox, oy, MAPSZ = projector(lat, lon, W, H, S)
    base = bake_static(W, H, S, route, mvals, vmin, vmax, fonts,
                       PALETTES[args.palette], args.metric, ox, oy, MAPSZ)

    outfile = "test.mp4" if args.quick_dur else args.out
    with open("concat.txt", "w") as f:
        for c in chapters: f.write(f"file '{os.path.abspath(c)}'\n")
    seek = ["-ss", str(args.quick_start), "-t", str(args.quick_dur)] if args.quick_dur else []
    EMIT = args.fps; STEP = EMIT // args.ofps
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
           *seek, "-f", "concat", "-safe", "0", "-i", "concat.txt",
           "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{W}x{H}",
           "-framerate", str(EMIT), "-i", "pipe:0",
           "-filter_complex", "[0:v]setsar=1[v];[v][1:v]overlay=0:0:format=auto:eof_action=repeat[o]",
           "-map", "[o]", "-map", "0:a", "-c:v", args.vcodec, "-b:v", args.bitrate,
           "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", outfile]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    total_dur = float(args.quick_dur) if args.quick_dur else total
    n = int(round(total_dur*EMIT)); t0 = time.time()
    last_slot = -1; cached = None
    for i in range(n):
        slot = i // STEP
        if slot != last_slot:
            vid_t = args.quick_start + slot/args.ofps
            q = start_utc + vid_t + args.offset
            frame = draw_dynamic(base.copy(), S, fonts, q, t, lat, lon, ele, hr, spd,
                                 proj, W, H, offset=args.offset, show_off=False)
            cached = frame.tobytes(); last_slot = slot
        try:
            proc.stdin.write(cached)
        except BrokenPipeError:
            break
        if i % 1800 == 0:
            elp = time.time()-t0; ef = i/elp if elp > 0 else 0
            eta = (n-i)/ef/60 if ef > 0 else 0
            print(f"[render] {i}/{n} ({100*i/n:.0f}%)  {ef:.0f} fps  "
                  f"{elp/60:.1f} min elapsed  ETA {eta:.0f} min", flush=True)
    proc.stdin.close(); proc.wait()
    print(f"DONE -> {outfile}  ({n} frames, {(time.time()-t0)/60:.1f} min)")

if __name__ == "__main__":
    main()
