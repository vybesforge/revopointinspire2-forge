#!/usr/bin/env python3
"""INSPIRE2 depth -> STL mesh stage (clean-room, Apache 2.0).

Reads Y16 depth frames (u16 640x512) produced by inspire2.py `scan`, unprojects
valid pixels to 3D using camera intrinsics, and triangulates the resulting 2.5D
range image into an exported STL.

A single-view depth frame is a 2.5D surface: every valid pixel in a structured
light capture is a confident range sample. We unproject those to mm using the
pinhole model and connect 4-neighbours into quads (split into two triangles each),
dropping edges where the depth gap is too large (occlusion/discontinuity). When
several frames are supplied they are depth-median fused first, which averages out
temporal noise so handheld / turntable captures clean up.

Intrinsics and the raw-depth->mm scale are parameters: the scanner's own
`getIntrinsics` rides the network/adb control channel we haven't pulled yet.
Defaults are nominal; pass --cx/--cy/--fx/--fy/--scale to correct. This is the
calibration step that turns a geometrically*plausible* mesh into an accurate one.

Usage:
    inspire2 scan -s 8 -o ~/scan/mug        # capture depth frames
    ./mesh.py ~/scan/mug -o mug.stl         # depth -> STL
    ./mesh.py ~/scan/mug -o mug.stl --fx 600 --scale 4.2
"""
import argparse, glob, os, sys

import numpy as np
import trimesh

W, H = 640, 512
DEFAULTS = dict(cx=320.0, cy=256.0, fx=570.0, fy=570.0, scale=5.0, gap=0.12)


def load_depth(path):
    raw = np.fromfile(path, dtype="<u2")
    if raw.size != W * H:
        raise ValueError(f"{path}: {raw.size} bytes, expected {W*H} u16 samples")
    return raw.reshape(H, W).astype("f4")


def fuse(frames):
    """Median-merge a stack of frames into one float32 depth map (0=invalid)."""
    if len(frames) == 1:
        return frames[0]
    st = np.stack(frames)
    out = np.zeros((H, W), "f4")
    for y in range(H):
        for x in range(W):
            col = st[:, y, x]
            col = col[col > 0]
            if col.size:
                out[y, x] = np.median(col)
    return out


def reach(mesh, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    mesh.export(out)


def build(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("indir", help="directory containing depth_*.raw frames")
    ap.add_argument("-o", "--out", default="scan_mesh.stl")
    ap.add_argument("--cx", type=float, default=DEFAULTS["cx"])
    ap.add_argument("--cy", type=float, default=DEFAULTS["cy"])
    ap.add_argument("--fx", type=float, default=DEFAULTS["fx"])
    ap.add_argument("--fy", type=float, default=DEFAULTS["fy"])
    ap.add_argument("--scale", type=float, default=DEFAULTS["scale"],
                    help="mm per raw depth unit (calibrate for accuracy)")
    ap.add_argument("--gap", type=float, default=DEFAULTS["gap"],
                    help="max relative depth jump across a quad edge")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.indir, "depth_*.raw")))
    files += sorted(glob.glob(os.path.join(args.indir, "frame_*.raw")))
    files = sorted(set(files))
    if not files:
        print(f"no depth_*.raw in {args.indir}", file=sys.stderr)
        return 2
    frames = [load_depth(f) for f in files]
    print(f"{len(frames)} frame(s)")
    dep = fuse(frames)
    valid = dep > 0
    print(f"valid pixels {valid.sum()} ({100*valid.mean():.1f}%)")
    if valid.sum() < 50:
        print("too few valid pixels - is an object in range? (sparse map)",
              file=sys.stderr)
        return 1

    ys, xs = np.nonzero(valid)
    vz = dep[ys, xs] * args.scale          # mm
    zmed = np.median(vz)
    keep = vz < zmed * 3.0                 # drop absurd outliers
    ys, xs, vz = ys[keep], xs[keep], vz[keep]
    vx = (xs - args.cx) * vz / args.fx
    vy = (ys - args.cy) * vz / args.fy

    idx = {(int(y), int(x)): i for i, (y, x) in enumerate(zip(ys, xs))}

    # triangulate the 2.5D range image: for each valid pixel, form the quad
    # with its right, down, and diagonal neighbours; skip edges across depth
    # discontinuities. Each quad splits into triangles (i,r,d) and (r,diag,d).
    tri_pairs = []
    for i, (y, x) in enumerate(zip(ys, xs)):
        r = idx.get((y, x + 1))
        d = idx.get((y + 1, x))
        if r is None or d is None:
            continue
        zi, zr, zd = vz[i], vz[r], vz[d]
        if (abs(zi - zr) > args.gap * max(zi, zr) or
                abs(zi - zd) > args.gap * max(zi, zd)):
            continue
        tri_pairs.append((i, r, d))
        diag = idx.get((y + 1, x + 1))
        if diag is None:
            continue
        zdiag = vz[diag]
        if (abs(zr - zdiag) > args.gap * max(zr, zdiag) or
                abs(zd - zdiag) > args.gap * max(zd, zdiag)):
            continue
        tri_pairs.append((r, diag, d))

    if not tri_pairs:
        print("no valid topology (map too sparse/disjoint)", file=sys.stderr)
        return 1

    mesh = trimesh.Trimesh(
        vertices=np.column_stack([vx, -vy, vz]),
        faces=np.array(tri_pairs, dtype="i4"),
        process=True)
    trimesh.repair.fix_normals(mesh)
    if mesh.triangles.shape[0] < 10:
        print("mesh too small to be useful", file=sys.stderr)
        return 1
    reach(mesh, args.out)
    print(f"wrote {args.out}: {len(mesh.vertices)} verts, "
          f"{mesh.triangles.shape[0]} tris")
    return 0


if __name__ == "__main__":
    sys.exit(build())
