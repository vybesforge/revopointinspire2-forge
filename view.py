#!/usr/bin/env python3
"""INSPIRE2 live view -> PNG (clean-room, Apache 2.0).

Captures a few Y16 depth frames from the depth capture node and renders them
into a black-background PNG, colored by depth, with a center crosshair. Valid
(in-range, confident) depth pixels are exactly where the scanner sees a surface,
so this is the centering view: move the object until the blob is on the crosshair.

Usage:
    ./view.py -o /tmp/scanner_view.png [-n 5] [--node /dev/videoN]

Does not re-arm: assumes the depth engine is already armed (a prior scan).
"""
import argparse, glob, os, subprocess, sys, tempfile

import numpy as np

W, H = 640, 512
DEP_CARD = "REVO_PRODUCT: INSPIRE2-REVO-Dep"


def find_depth():
    for v in sorted(glob.glob("/dev/video*"),
                    key=lambda p: int(p.rsplit("video", 1)[-1])):
        try:
            n = open(f"/sys/class/video4linux/{os.path.basename(v)}/name").read().strip()
            if n == DEP_CARD:
                return v
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="/tmp/scanner_view.png")
    ap.add_argument("-n", "--frames", type=int, default=6)
    ap.add_argument("--node", default=None)
    args = ap.parse_args()

    node = args.node or find_depth()
    if not node:
        print("depth node not found - scanner armed?", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as d:
        cmd = ["ffmpeg", "-y", "-f", "v4l2", "-input_format", "y16",
               "-video_size", f"{W}x{H}", "-framerate", "15",
               "-t", "1", "-i", node, "-c", "copy",
               os.path.join(d, "f_%03d.raw")]
        subprocess.run(cmd, check=True, capture_output=True)
        imgs = sorted(glob.glob(os.path.join(d, "f_*.raw")))
        if not imgs:
            print("no frames captured", file=sys.stderr)
            return 1
        st = np.stack([np.fromfile(f, "<u2").reshape(H, W) for f in imgs[:args.frames]])

    # depth-median fusion
    depth = np.median([st[k] for k in range(st.shape[0])], axis=0).astype("f4")
    valid = depth > 0
    print(f"valid pixels: {valid.sum()} ({100*valid.mean():.1f}%)  "
          f"range {depth[valid].min():.0f}..{depth[valid].max():.0f}" if valid.any()
          else "no objects in range (sparse map)")

    img = np.zeros((H, W, 3), "u1")
    if valid.any():
        # window OUT the far background so only the object band shows bright;
        # sensor dumps high speckle values (up to ~65k) that lit the background.
        zc = depth[valid]
        med = np.median(zc)
        mad = np.median(np.abs(zc - med))
        hi = med + 4.5 * mad
        obj = valid & (depth <= hi)
        dv = depth[obj]
        if not dv.any():
            dv = zc
            obj = valid
        lo = np.percentile(dv, 2)
        hi2 = np.percentile(dv, 98)
        norm = np.clip((dv - lo) / max(hi2 - lo, 1e-6), 0, 1)
        # heat-style: nearest = bright orange, mid = yellow, far = blue.
        # never black on a valid pixel - that's how the mug "went black".
        r = np.ones_like(norm) * 255.0
        g = np.clip(2 * norm, 0, 1) * 255.0
        b = np.clip(1 - norm, 0, 1) * 255.0
        col = np.stack([r, g, b], axis=-1).astype("u1")
        img[obj] = col

    # center crosshair (drawn over everything)
    cx, cy = W // 2, H // 2
    img[:, cx - 1:cx + 2, :] = 0                 # vertical line (black)
    img[cy - 1:cy + 2, :, :] = 0                 # horizontal line (black)
    img[:, cx - 1:cx + 2, 0] = 255               # red vertical
    img[cy - 1:cy + 2, 1] = 255                  # green horizontal
    # small white center dot
    img[cy - 3:cy + 4, cx - 3:cx + 4, :] = 255

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    from PIL import Image
    Image.fromarray(img).save(args.out)
    print(f"view -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
