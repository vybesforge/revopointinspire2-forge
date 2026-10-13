#!/usr/bin/env python3
"""Fill sparse INSPIRE2 depth (laser-stripe hits) into a solid shell and export STL.

The Y16 stream is real depth, but only the projector hits are nonzero. A shiny
car at ~6 in reads as a speckled shell plus vertical stripe noise on the backdrop.
This fuses several frames, keeps the object cluster around the optical center,
inpaints short gaps, and unprojects with a scale that maps median depth to a
known distance (default 152 mm = 6 in).
"""
import argparse, glob, os, sys
import numpy as np
from scipy import ndimage
import trimesh

W, H = 640, 512


def load_stack(indir):
    files = sorted(glob.glob(os.path.join(indir, "depth_*.raw")))
    files += sorted(glob.glob(os.path.join(indir, "f_*.raw")))
    files += sorted(glob.glob(os.path.join(indir, "frame_*.raw")))
    if not files:
        raise SystemExit(f"no frames in {indir}")
    return np.stack([np.fromfile(f, "<u2").reshape(H, W).astype("f4") for f in files])


def fuse(stack, min_hits):
    hits = np.count_nonzero(stack > 0, axis=0)
    depth = np.zeros((H, W), "f4")
    # median of nonzero samples
    for y in range(H):
        cols = stack[:, y, :]
        for x in range(W):
            v = cols[:, x]
            v = v[v > 0]
            if v.size >= min_hits:
                depth[y, x] = np.median(v)
    return depth, hits


def object_mask(depth, hits, min_hits):
    stable = hits >= min_hits
    z = depth[stable]
    if z.size < 50:
        return stable
    med = np.median(z)
    # drop far speckle and the 65k sentinel
    near = stable & (depth > 80) & (depth < med * 1.6)
    lab, n = ndimage.label(near)
    if n == 0:
        return near
    # prefer the component that covers the optical center; else the largest
    # in the lower two-thirds (the car sits on the crosshair, stripes rain above)
    cy, cx = H // 2, W // 2
    center_lab = lab[cy - 40:cy + 80, cx - 80:cx + 80]
    vals, counts = np.unique(center_lab[center_lab > 0], return_counts=True)
    if len(vals):
        keep = vals[np.argmax(counts)]
    else:
        sizes = ndimage.sum(near, lab, range(1, n + 1))
        keep = int(np.argmax(sizes)) + 1
    return lab == keep


def inpaint(depth, mask):
    """Fill holes inside the object mask by nearest valid depth."""
    closed = ndimage.binary_closing(mask, iterations=3)
    closed = ndimage.binary_fill_holes(closed)
    # don't grow into the stripe rain: limit to the mask's bbox, padded a little
    ys, xs = np.nonzero(mask)
    if len(ys) < 20:
        return depth, mask
    y0, y1 = max(0, ys.min() - 4), min(H, ys.max() + 5)
    x0, x1 = max(0, xs.min() - 4), min(W, xs.max() + 5)
    region = np.zeros_like(closed)
    region[y0:y1, x0:x1] = closed[y0:y1, x0:x1]
    known = (depth > 0) & region
    if known.sum() < 20:
        return depth, region
    # nearest-neighbor fill of unknown pixels inside region
    inv = ~known
    inv &= region
    if not inv.any():
        return depth, region
    _, (iy, ix) = ndimage.distance_transform_edt(inv, return_indices=True)
    filled = depth.copy()
    filled[inv] = depth[iy[inv], ix[inv]]
    return filled, region


def mesh_from(depth, mask, cx, cy, fx, fy, scale, gap):
    ys, xs = np.nonzero(mask & (depth > 0))
    if len(ys) < 30:
        raise SystemExit("object too small to mesh")
    vz = depth[ys, xs] * scale
    vx = (xs - cx) * vz / fx
    vy = (ys - cy) * vz / fy
    idx = {(int(y), int(x)): i for i, (y, x) in enumerate(zip(ys, xs))}
    tris = []
    for i, (y, x) in enumerate(zip(ys, xs)):
        r = idx.get((int(y), int(x) + 1))
        d = idx.get((int(y) + 1, int(x)))
        if r is None or d is None:
            continue
        zi, zr, zd = vz[i], vz[r], vz[d]
        if abs(zi - zr) > gap * max(zi, zr) or abs(zi - zd) > gap * max(zi, zd):
            continue
        tris.append((i, r, d))
        diag = idx.get((int(y) + 1, int(x) + 1))
        if diag is None:
            continue
        zdiag = vz[diag]
        if abs(zr - zdiag) > gap * max(zr, zdiag) or abs(zd - zdiag) > gap * max(zd, zdiag):
            continue
        tris.append((r, diag, d))
    if len(tris) < 10:
        raise SystemExit("no surface (gaps too big)")
    mesh = trimesh.Trimesh(vertices=np.column_stack([vx, -vy, vz]),
                           faces=np.array(tris, dtype="i4"), process=True)
    mesh.remove_unreferenced_vertices()
    return mesh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir")
    ap.add_argument("-o", default="car.stl")
    ap.add_argument("--preview", default="")
    ap.add_argument("--distance-mm", type=float, default=152.0,
                    help="real distance to the object, used to set mm/unit")
    ap.add_argument("--fx", type=float, default=580.0)
    ap.add_argument("--fy", type=float, default=580.0)
    ap.add_argument("--gap", type=float, default=0.18)
    ap.add_argument("--min-hits", type=int, default=3)
    args = ap.parse_args()

    stack = load_stack(args.indir)
    print(f"{len(stack)} frames")
    depth, hits = fuse(stack, args.min_hits)
    mask = object_mask(depth, hits, args.min_hits)
    filled, region = inpaint(depth, mask)
    use = region & (filled > 0)
    if use.any():
        med0 = float(np.median(filled[use]))
        use = use & (filled > med0 * 0.65) & (filled < med0 * 1.35)
    med = float(np.median(filled[use])) if use.any() else 1.0
    scale = args.distance_mm / med
    print(f"object px {int(use.sum())}  median raw {med:.0f}  scale {scale:.4f} mm/unit")
    mesh = mesh_from(filled, use, W / 2, H / 2, args.fx, args.fy, scale, args.gap)
    os.makedirs(os.path.dirname(args.o) or ".", exist_ok=True)
    mesh.export(args.o)
    ext = mesh.bounds[1] - mesh.bounds[0]
    print(f"wrote {args.o}: {len(mesh.vertices)} verts, {len(mesh.faces)} tris")
    print(f"size mm  x={ext[0]:.1f} y={ext[1]:.1f} z={ext[2]:.1f}")
    if args.preview:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        m = mesh
        if len(m.faces) > 20000:
            m = m.simplify_quadric_decimation(min(0.85, 1 - 20000 / len(m.faces)))
        v, f = m.vertices, m.faces
        fig = plt.figure(figsize=(8, 8))
        ax = fig.add_subplot(111, projection="3d")
        ax.plot_trisurf(v[:, 0], v[:, 1], v[:, 2], triangles=f, cmap="viridis", shade=True)
        ax.set_box_aspect((np.ptp(v[:, 0]), np.ptp(v[:, 1]), np.ptp(v[:, 2])))
        ax.set_xlabel("x mm"); ax.set_ylabel("y mm"); ax.set_zlabel("z mm")
        ax.view_init(elev=25, azim=-70)
        plt.tight_layout()
        plt.savefig(args.preview, dpi=110, facecolor="w")
        print("preview", args.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
