#!/usr/bin/env python3
"""Compile one turntable revolution into a mesh (clean-room, Apache 2.0).

The platter period is known (default 45s). Each Y16 frame is cleaned to the
near object, unprojected, and rotated about the vertical axis by the angle
implied by its timestamp. A Hot Wheels car is 76.2mm long; the widest
silhouette in the turn sets the mm scale.

    ./compile360.py turn.raw -o model.stl --period 45 --length-mm 76.2
"""
import argparse, os, sys
import numpy as np
from scipy import ndimage

W, H = 640, 512
FRAME = W * H
FX = FY = 580.0
CX, CY = W / 2.0, H / 2.0


def load_raw(path):
    raw = np.fromfile(path, dtype="<u2")
    n = raw.size // FRAME
    if n < 4:
        raise SystemExit(f"only {n} frames in {path}")
    return raw[: n * FRAME].reshape(n, H, W).astype("f4")


def object_mask(dep):
    valid = dep > 80
    if int(valid.sum()) < 80:
        return np.zeros_like(valid)
    med = float(np.median(dep[valid]))
    near = valid & (dep < med * 0.92)
    if int(near.sum()) < 200:
        near = valid
    lab, n = ndimage.label(near)
    if n == 0:
        return near
    cy, cx = H // 2, W // 2
    window = lab[max(0, cy - 50):cy + 70, max(0, cx - 160):cx + 160]
    vals, counts = np.unique(window[window > 0], return_counts=True)
    if len(vals) == 0:
        sizes = ndimage.sum(near, lab, range(1, n + 1))
        keep = int(np.argmax(sizes)) + 1
    else:
        keep = int(vals[np.argmax(counts)])
    mask = lab == keep
    local = ndimage.median_filter(dep, size=5)
    mask = mask & (np.abs(dep - local) < max(24.0, med * 0.025)) & (dep > 80)
    return ndimage.binary_opening(mask, iterations=1)


def spans(stack, step):
    best = (0, 0, 0.0)  # width, height, raw_med
    for i in range(0, len(stack), step):
        m = object_mask(stack[i])
        if m.sum() < 200:
            continue
        ys, xs = np.nonzero(m)
        bw, bh = int(xs.max() - xs.min()), int(ys.max() - ys.min())
        if bw > best[0]:
            best = (bw, bh, float(np.median(stack[i][m])))
    return best


def frame_points(dep, mask, scale):
    ys, xs = np.nonzero(mask)
    if len(ys) < 30:
        return None
    z = dep[ys, xs] * scale
    x = (xs - CX) * z / FX
    y = (ys - CY) * z / FY
    # subsample so a full turn stays tractable
    if len(x) > 2500:
        sel = np.linspace(0, len(x) - 1, 2500).astype(int)
        x, y, z = x[sel], y[sel], z[sel]
    return np.column_stack([x, -y, z])


def rotate_y(pts, ang, origin):
    c, s = np.cos(ang), np.sin(ang)
    x = pts[:, 0] - origin[0]
    z = pts[:, 2] - origin[2]
    out = pts.copy()
    out[:, 0] = c * x + s * z + origin[0]
    out[:, 2] = -s * x + c * z + origin[2]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("-o", default="turn.stl")
    ap.add_argument("--period", type=float, default=45.0)
    ap.add_argument("--length-mm", type=float, default=76.2, help="Hot Wheels length")
    ap.add_argument("--preview", default="")
    args = ap.parse_args()

    stack = load_raw(args.raw)
    n = len(stack)
    print(f"{n} frames over {args.period:.0f}s ({n / args.period:.2f} fps)", flush=True)
    # rotation check: silhouette of first vs quarter-turn
    q = max(1, n // 4)
    a = object_mask(stack[0])
    b = object_mask(stack[q])
    if a.sum() and b.sum():
        inter = np.logical_and(a, b).sum()
        union = np.logical_or(a, b).sum()
        iou = inter / max(union, 1)
        print(f"quarter-turn silhouette IoU {iou:.2f} (1.00 = not rotating)", flush=True)
        if iou > 0.92:
            print("platter does not appear to be turning in the depth stream", file=sys.stderr)
            return 2

    bw, bh, raw_med = spans(stack, max(1, n // 24))
    if bw < 20:
        print("object not found", file=sys.stderr)
        return 1
    scale = args.length_mm * FX / (bw * raw_med)
    print(f"widest silhouette {bw}x{bh}px  raw {raw_med:.0f}  scale {scale:.4f} mm/unit", flush=True)

    # axis = median object center across the turn
    centers = []
    cleaned = []
    for i in range(0, n, max(1, n // 80)):
        m = object_mask(stack[i])
        pts = frame_points(stack[i], m, scale)
        if pts is None:
            continue
        centers.append(pts.mean(axis=0))
        cleaned.append((i, pts))
    if len(cleaned) < 6:
        print("not enough clean views", file=sys.stderr)
        return 1
    origin = np.median(np.stack(centers), axis=0)
    print(f"axis at x={origin[0]:.1f} z={origin[2]:.1f} mm", flush=True)

    clouds = []
    for i, pts in cleaned:
        ang = 2 * np.pi * (i / n)
        clouds.append(rotate_y(pts, -ang, origin))
    fused = np.vstack(clouds)
    print(f"fused {len(fused)} points from {len(cleaned)} views", flush=True)

    import open3d as o3d
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(fused.astype("f8"))
    pcd = pcd.voxel_down_sample(1.2)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=4.0, max_nn=30))
    pcd.orient_normals_towards_camera_location(camera_location=np.array([0.0, 0.0, 0.0]))
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=8)
    dens = np.asarray(densities)
    keep = dens > np.quantile(dens, 0.08)
    mesh.remove_vertices_by_mask(~keep)
    mesh.compute_vertex_normals()
    # keep the biggest piece
    parts = mesh.cluster_connected_triangles()
    labels = np.asarray(parts[0])
    counts = np.bincount(labels)
    mesh.remove_triangles_by_mask(labels != counts.argmax())
    mesh.remove_unreferenced_vertices()
    tri = o3d.geometry.TriangleMesh()
    # export via trimesh for a plain STL
    import trimesh
    tm = trimesh.Trimesh(vertices=np.asarray(mesh.vertices),
                         faces=np.asarray(mesh.triangles), process=True)
    os.makedirs(os.path.dirname(args.o) or ".", exist_ok=True)
    tm.export(args.o)
    ext = tm.bounds[1] - tm.bounds[0]
    print(f"wrote {args.o}: {len(tm.vertices)} verts, {len(tm.faces)} tris", flush=True)
    print(f"size mm  x={ext[0]:.1f} y={ext[1]:.1f} z={ext[2]:.1f}", flush=True)
    if args.preview:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        m = tm
        if len(m.faces) > 20000:
            m = m.simplify_quadric_decimation(min(0.85, 1 - 20000 / len(m.faces)))
        v, f = m.vertices, m.faces
        fig = plt.figure(figsize=(8, 8))
        ax = fig.add_subplot(111, projection="3d")
        ax.plot_trisurf(v[:, 0], v[:, 1], v[:, 2], triangles=f, cmap="viridis", shade=True)
        ax.set_box_aspect(tuple(np.maximum(np.ptp(v, axis=0), 1)))
        ax.view_init(elev=20, azim=-55)
        plt.tight_layout()
        plt.savefig(args.preview, dpi=110, facecolor="white")
        print("preview", args.preview, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
