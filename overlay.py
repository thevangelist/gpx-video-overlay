#!/usr/bin/env python3
"""Minimalist / gestalt GPX dashboard overlay with a heatmap route.

Library + CLI. Everything is parameterised: gpx file, video start time, sync
offset, metric, resolution, fps, font and colour palette. The design is authored
at 1080p; a scale factor S = height/1080 makes it resolution-independent, so the
same drawing code feeds both the still/frame export here and the full 4K render
in render_full.py.

  python overlay.py --still 113 --gpx ride.gpx --video-start 2026-06-18T16:08:26Z
"""
import os, math, argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

GN = "{http://www.topografix.com/GPX/1/1}"
XN = "{http://www.garmin.com/xmlschemas/TrackPointExtension/v1}"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FONT = os.path.join(HERE, "fonts", "RedditMono.ttf")
DESIGN_H = 1080  # reference height the layout is authored at

WHITE = (255, 255, 255, 255)
GREY  = (175, 181, 188, 255)
DIM   = (120, 126, 134, 255)

# ---------- palettes (low -> high), each reads as "heat" over video ----------
PALETTES = {
    "turbo": [(48,18,59),(50,100,200),(28,160,230),(20,205,150),
              (120,222,60),(205,218,40),(250,168,30),(240,90,30),(190,28,28)],
    "fire":  [(20,12,28),(90,20,40),(170,40,30),(225,95,30),(248,170,40),(255,236,170)],
    "ice":   [(10,14,30),(20,60,120),(30,120,200),(60,180,230),(150,220,245),(240,250,255)],
    "mono":  [(70,74,80),(255,255,255)],
}

def parse_start(s):
    """ISO8601 UTC (e.g. 2026-06-18T16:08:26Z) -> epoch seconds.
    Wall-clock time the recording began; used to align gpx timestamps to video time."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

# ---------- data ----------
def load_gpx(path):
    root = ET.parse(path).getroot()
    t, lat, lon, ele, hr = [], [], [], [], []
    for p in root.iter(GN + "trkpt"):
        lat.append(float(p.get("lat"))); lon.append(float(p.get("lon")))
        e = p.find(GN + "ele"); tm = p.find(GN + "time"); h = p.find(".//" + XN + "hr")
        t.append(datetime.strptime(tm.text, "%Y-%m-%dT%H:%M:%SZ")
                 .replace(tzinfo=timezone.utc).timestamp())
        ele.append(float(e.text) if e is not None else 0.0)
        hr.append(float(h.text) if h is not None else 0.0)
    return (np.array(t), np.array(lat), np.array(lon), np.array(ele), np.array(hr))

def haversine(la1, lo1, la2, lo2):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(np.radians, [la1, lo1, la2, lo2])
    dla, dlo = la2 - la1, lo2 - lo1
    h = np.sin(dla/2)**2 + np.cos(la1)*np.cos(la2)*np.sin(dlo/2)**2
    return 2 * R * np.arcsin(np.sqrt(h))

def prep(metric, gpx):
    """Load gpx and derive smoothed speed + the chosen metric's 5/95 range."""
    t, lat, lon, ele, hr = load_gpx(gpx)
    seg_d = haversine(lat[:-1], lon[:-1], lat[1:], lon[1:]); seg_dt = np.diff(t)
    seg_v = np.where(seg_dt > 0, seg_d/seg_dt, 0) * 3.6
    spd = np.concatenate([[seg_v[0]], (seg_v[:-1]+seg_v[1:])/2, [seg_v[-1]]])
    spd = np.convolve(spd, np.ones(3)/3, mode="same")
    mvals = spd if metric == "speed" else hr
    pos = mvals[mvals > 0] if metric == "hr" else mvals
    vmin, vmax = np.percentile(pos, [5, 95])
    return t, lat, lon, ele, hr, spd, mvals, vmin, vmax

# ---------- look ----------
def F(path, sz, wght=400):
    f = ImageFont.truetype(path, int(round(sz)))
    try:
        f.set_variation_by_axes([wght])  # variable fonts (e.g. Reddit Mono)
    except Exception:
        pass                             # static font: weight baked in
    return f

def make_fonts(path, S):
    return dict(
        big=F(path, 150*S, 300), lab=F(path, 20*S, 500),
        sec=F(path, 44*S, 400), seclab=F(path, 18*S, 500),
        clk=F(path, 26*S, 300), off=F(path, 13*S, 500),
        maplab=F(path, 16*S, 500), maprng=F(path, 16*S, 400),
    )

def heat(palette, v):  # v in [0,1] -> (r,g,b)
    pal = palette
    x = np.clip(v, 0, 1) * (len(pal)-1)
    i = int(np.floor(x)); f = x - i
    if i >= len(pal)-1: return tuple(int(c) for c in pal[-1])
    c = [pal[i][k]*(1-f) + pal[i+1][k]*f for k in range(3)]
    return tuple(int(round(x)) for x in c)

def tracked(d, xy, text, font, fill, track=0, anchor_right=False):
    # poor-man letter-spacing; returns total width
    widths = [d.textlength(ch, font=font) for ch in text]
    total = sum(widths) + track*(len(text)-1 if text else 0)
    x, y = xy
    if anchor_right: x -= total
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=font, fill=fill); x += w + track
    return total

def bottom_scrim(W, H, h=320, strength=150):
    g = np.zeros((H, W, 4), np.uint8)
    col = np.linspace(0, strength, h).astype(np.uint8)
    g[H-h:H, :, 3] = col[:, None]
    return Image.fromarray(g)

# ---------- projection ----------
def projector(lat, lon, W, H, S):
    """Return (proj_fn, route_px, ox, oy, MAPSZ) for the bottom-left mini-map."""
    MAPSZ = int(300*S); PAD = int(14*S); M = int(64*S)
    ox, oy = M, H - M - MAPSZ - int(34*S)
    lat0 = lat.mean()
    mxa = (lon-lon.mean())*math.cos(math.radians(lat0)); mya = (lat-lat.mean())
    mnx, mxx = mxa.min(), mxa.max(); mny, mxy = mya.min(), mya.max()
    def proj(la, lo):
        x = (lo-lon.mean())*math.cos(math.radians(lat0))
        sx = (x-mnx)/(mxx-mnx+1e-9); sy = (la-lat.mean()-mny)/(mxy-mny+1e-9)
        return ox+PAD+sx*(MAPSZ-2*PAD), oy+PAD+(1-sy)*(MAPSZ-2*PAD)
    route = [proj(a, b) for a, b in zip(lat, lon)]
    return proj, route, ox, oy, MAPSZ

# ---------- drawing ----------
def bake_static(W, H, S, route, mvals, vmin, vmax, fonts, palette, metric, ox, oy, MAPSZ):
    """Everything that never moves: scrim + glowing heatmap route + labels.
    Baked once, then copied per frame (the dynamic bits redrawn on top)."""
    base = Image.new("RGBA", (W, H), (0,0,0,0))
    base.alpha_composite(bottom_scrim(W, H, h=int(320*S)))
    line = Image.new("RGBA", (W, H), (0,0,0,0)); ld = ImageDraw.Draw(line)
    for i in range(1, len(route)):
        c = heat(palette, (mvals[i]-vmin)/(vmax-vmin+1e-9))
        ld.line([route[i-1], route[i]], fill=c+(255,), width=max(1, int(6*S)))
    glow = line.filter(ImageFilter.GaussianBlur(6*S))
    glow.putalpha(glow.getchannel("A").point(lambda a: int(a*0.5)))
    base.alpha_composite(glow); base.alpha_composite(line)
    bd = ImageDraw.Draw(base)
    lbl = "SPEED MAP" if metric == "speed" else "EFFORT MAP"
    unit = "KM/H" if metric == "speed" else "BPM"
    ly = oy + MAPSZ + int(6*S)
    tracked(bd, (ox, ly), lbl, fonts["maplab"], GREY, track=5*S)
    tracked(bd, (ox+MAPSZ, ly), f"{vmin:.0f}-{vmax:.0f} {unit}",
            fonts["maprng"], DIM, track=2*S, anchor_right=True)
    return base

def draw_dynamic(frame, S, fonts, q, t, lat, lon, ele, hr, spd, proj, W, H,
                 offset=0.0, show_off=True):
    """Moving position dot + telemetry numbers + clock, drawn onto a base copy."""
    if not (t[0] <= q <= t[-1]):
        return frame
    d = ImageDraw.Draw(frame)
    M = int(64*S)
    la = np.interp(q, t, lat); lo = np.interp(q, t, lon)
    v  = np.interp(q, t, spd); h_ = np.interp(q, t, hr); el = np.interp(q, t, ele)
    cx, cy = proj(la, lo)
    for r, a in [(17*S,45),(12*S,80),(7*S,160)]:
        d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=(255,255,255,a))
    d.ellipse([cx-5*S,cy-5*S,cx+5*S,cy+5*S], fill=WHITE)

    rx = W - M
    big, lab, sec, seclab = fonts["big"], fonts["lab"], fonts["sec"], fonts["seclab"]
    sv = f"{v:0.0f}"
    bbox = d.textbbox((0,0), sv, font=big); bh = bbox[3]-bbox[1]
    y_speed = H - M - bh - bbox[1]; wv = d.textlength(sv, font=big)
    klw = tracked(d, (rx, y_speed+bh-26*S), "KM/H", lab, GREY, track=4*S, anchor_right=True)
    d.text((rx-klw-16*S-wv, y_speed), sv, font=big, fill=WHITE)

    y_sec = y_speed - 14*S; s_el = f"{el:0.0f}"; s_hr = f"{h_:0.0f}"; x = rx
    x -= tracked(d, (x, y_sec+18*S), "BPM", seclab, GREY, track=3*S, anchor_right=True); x -= 10*S
    whr = d.textlength(s_hr, font=sec); d.text((x-whr, y_sec), s_hr, font=sec, fill=WHITE); x -= whr+26*S
    tracked(d, (x, y_sec+18*S), "·", seclab, DIM, anchor_right=True); x -= 26*S
    x -= tracked(d, (x, y_sec+18*S), "M", seclab, GREY, track=3*S, anchor_right=True); x -= 10*S
    wel = d.textlength(s_el, font=sec); d.text((x-wel, y_sec), s_el, font=sec, fill=WHITE)

    clk = datetime.fromtimestamp(q, timezone.utc).strftime("%H:%M:%S")
    tracked(d, (W-M, 52*S), clk, fonts["clk"], GREY, track=3*S, anchor_right=True)
    if show_off and offset:
        tracked(d, (W-M, 86*S), f"OFFSET {offset:+.0f}S", fonts["off"], DIM,
                track=3*S, anchor_right=True)
    return frame

def build_overlay(W, H, q, t, lat, lon, ele, hr, spd, mvals, vmin, vmax,
                  offset, font=DEFAULT_FONT, palette="turbo", metric="speed", show_off=True):
    """One full transparent overlay frame (still / per-frame export)."""
    S = H / DESIGN_H
    fonts = make_fonts(font, S)
    pal = PALETTES[palette]
    proj, route, ox, oy, MAPSZ = projector(lat, lon, W, H, S)
    base = bake_static(W, H, S, route, mvals, vmin, vmax, fonts, pal, metric, ox, oy, MAPSZ)
    return draw_dynamic(base, S, fonts, q, t, lat, lon, ele, hr, spd, proj, W, H,
                        offset=offset, show_off=show_off)

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Render GPX overlay frames / a still preview.")
    ap.add_argument("--gpx", default="ride.gpx")
    ap.add_argument("--video-start", required=True,
                    help="ISO8601 UTC time the video started, e.g. 2026-06-18T16:08:26Z")
    ap.add_argument("--start", type=float, default=0, help="video time (s) of first frame")
    ap.add_argument("--dur", type=float, default=30.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--offset", type=float, default=0.0, help="gpx-vs-video sync shift (s)")
    ap.add_argument("--metric", choices=["speed", "hr"], default="speed")
    ap.add_argument("--w", type=int, default=1920); ap.add_argument("--h", type=int, default=1080)
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--palette", choices=list(PALETTES), default="turbo")
    ap.add_argument("--out", default="frames")
    ap.add_argument("--still", type=float, default=None, help="video time (s): write one PNG and exit")
    args = ap.parse_args()

    start_utc = parse_start(args.video_start)
    t, lat, lon, ele, hr, spd, mvals, vmin, vmax = prep(args.metric, args.gpx)

    def frame_at(vid_t):
        q = start_utc + vid_t + args.offset
        return build_overlay(args.w, args.h, q, t, lat, lon, ele, hr, spd, mvals,
                             vmin, vmax, args.offset, font=args.font,
                             palette=args.palette, metric=args.metric)

    if args.still is not None:
        frame_at(args.still).save("still_overlay.png")
        print("wrote still_overlay.png"); return

    os.makedirs(args.out, exist_ok=True)
    for f in os.listdir(args.out):
        if f.endswith(".png"): os.remove(os.path.join(args.out, f))
    n = int(args.dur * args.fps)
    for i in range(n):
        frame_at(args.start + i/args.fps).save(os.path.join(args.out, f"o_{i:05d}.png"))
    print(f"wrote {n} frames -> {args.out}/ metric={args.metric} offset={args.offset}")

if __name__ == "__main__":
    main()
