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
    masked = np.where(st > 0, st, np.nan)
    out = np.nanmedian(masked, axis=0)
    return np.nan_to_num(out, nan=0.0).astype("f4")


def isolate_object(dep):
    """Keep the surface in front of the scanner, drop the far plane and speckle.

    The Y16 stream is real depth, but a lit back wall often owns most of the
    pixels. Meshing that plane plus the stripe hits is what produced spikes.
    """
    from scipy import ndimage
    valid = dep > 80
    if int(valid.sum()) < 50:
        return dep
    z = dep[valid]
    med = float(np.median(z))
    near = valid & (dep > 80) & (dep < med * 0.92)
    if int(near.sum()) < 400:
        near = valid
    lab, n = ndimage.label(near)
    if n == 0:
        return dep
    cy, cx = H // 2, W // 2
    window = lab[max(0, cy - 40):cy + 40, max(0, cx - 80):cx + 80]
    vals, counts = np.unique(window[window > 0], return_counts=True)
    if len(vals):
        keep = int(vals[np.argmax(counts)])
    else:
        sizes = ndimage.sum(near, lab, range(1, n + 1))
        keep = int(np.argmax(sizes)) + 1
    mask = lab == keep
    local = ndimage.median_filter(dep, size=7)
    # p50 error vs the local median is ~1 unit. The spikes are the p99, ~2000 off.
    agree = np.abs(dep - local) < max(20.0, med * 0.02)
    mask = mask & agree & (dep > 80)
    mask = ndimage.binary_closing(mask, iterations=1)
    closed = ndimage.binary_fill_holes(mask)
    known = mask & (dep > 80)
    out = np.zeros_like(dep)
    out[known] = local[known]
    holes = closed & ~known
    if holes.any() and known.any():
        _, (iy, ix) = ndimage.distance_transform_edt(~known, return_indices=True)
        out[holes] = local[iy[holes], ix[holes]]
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
    ap.add_argument("--scale", type=float, default=0.0,
                    help="mm per raw depth unit. 0 = derive from --distance-mm")
    ap.add_argument("--distance-mm", type=float, default=250.0,
                    help="nominal distance to the object, used when --scale is 0")
    ap.add_argument("--scene", action="store_true",
                    help="mesh the whole frame, including the back wall")
    ap.add_argument("--gap", type=float, default=0.02,
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
    if not args.scene:
        dep = isolate_object(dep)
    valid = dep > 0
    print(f"valid pixels {int(valid.sum())} ({100*valid.mean():.1f}%)")
    if valid.sum() < 50:
        print("too few valid pixels - is an object in range? (sparse map)",
              file=sys.stderr)
        return 1

    scale = args.scale
    if scale <= 0:
        scale = args.distance_mm / float(np.median(dep[valid]))
    print(f"scale {scale:.4f} mm/unit")

    ys, xs = np.nonzero(valid)
    vz = dep[ys, xs] * scale          # mm
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
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        mesh = max(parts, key=lambda m: len(m.faces))
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
