#!/usr/bin/env python3
"""INSPIRE2 360 reconstruction (clean-room, Apache 2.0).

Turn a spinning-object depth capture into a full watertight STL. The scanner is
fixed; the turntable spins the object about its (vertical) axis. Distance loop:
for each depth frame we unproject a 3D point cloud of the visible near-surface,
then ICP-register each frame onto the next (the object has rotated a little
between frames — an asymmetric target like the toy car gives ICP a signal), chain
those transforms into one reference frame, fuse every frame's cloud, then
Poisson-reconstruct into a closed, printable mesh.

Objects that are rotationally symmetric (a plain mug) give ICP nothing to lock
onto at several angles — use an asymmetric target (toy car) for best results.

Depth scale + intrinsics stay in raw units for registration (geometry is
scale-invariant under the motion model); pass --scale to emit mm afterward.

Usage:
    ./inspire2.py scan -s 12 -o ~/scan/car     # capture while turntable spins
    ./reconstruct360.py ~/scan/car -o car.stl  # -> watertight 360 mesh
"""
import argparse, glob, os, sys

import numpy as np
import open3d as o3d

W, H = 640, 512
DEP_CARD = "REVO_PRODUCT: INSPIRE2-REVO-Dep"


def unproject(depth, cx, cy, fx, fy):
    depth = depth.astype("f4")
    v = depth > 0
    if not v.any():
        return None, None
    ys, xs = np.nonzero(v)
    z = depth[ys, xs]
    x = (xs - cx) * z / fx
    y = (ys - cy) * z / fy
    pts = np.column_stack([x, y, z])
    return pts, v


def to_pcd(pts, norm):
    pts = np.asarray(pts, "f8") / norm   # unit-ish scale, fast ICP
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    pcd = pcd.voxel_down_sample(0.008)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.02, max_nn=24))
    return pcd


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("indir", help="dir of depth_*.raw frames (spinning capture)")
    ap.add_argument("-o", "--out", default="car.stl")
    ap.add_argument("--cx", type=float, default=320.0)
    ap.add_argument("--cy", type=float, default=256.0)
    ap.add_argument("--fx", type=float, default=570.0)
    ap.add_argument("--fy", type=float, default=570.0)
    ap.add_argument("--scale", type=float, default=1.0, help="mm per raw depth unit")
    ap.add_argument("--skip", type=int, default=1,
                    help="use every Nth frame (tune if rotation per frame too big)")
    ap.add_argument("--poisson-depth", type=int, default=9)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.indir, "depth_*.raw")))[::args.skip]
    if not files:
        print("no depth_*.raw", file=sys.stderr)
        return 2
    print(f"{len(files)} frames (skip={args.skip})")

    # global normalization factor: median raw depth -> unit scale for fast ICP
    meds = []
    for f in files[:200]:
        dd = np.fromfile(f, "<u2").reshape(H, W)
        z = dd[dd > 0]
        if z.size:
            meds.append(np.median(z))
    if not meds:
        print("no valid depth in any frame", file=sys.stderr)
        return 1
    NORM = float(np.median(meds))
    print(f"normalizing by median depth {NORM:.0f}")

    clouds = []
    for f in files:
        d = np.fromfile(f, "<u2").reshape(H, W)
        pts, _ = unproject(d, args.cx, args.cy, args.fx, args.fy)
        if pts is None or len(pts) < 50:
            continue
        clouds.append(to_pcd(pts, NORM))
    if len(clouds) < 3:
        print("too few usable clouds", file=sys.stderr)
        return 1

    # consecutive-frame ICP; chain transforms into frame-0 reference
    acc = np.eye(4)
    frames = [np.asarray(clouds[0].points).copy()]
    traj = [np.eye(4)]
    crit = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=300)
    for i in range(1, len(clouds)):
        src, tgt = clouds[i], clouds[i - 1]
        r = o3d.pipelines.registration.registration_icp(
            src, tgt, max_correspondence_distance=0.03,
            init=np.eye(4),
            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(),
            criteria=crit)
        T = r.transformation
        acc = acc @ T
        traj.append(acc.copy())
        frames.append(np.asarray(src.points) @ acc[:3, :3].T + acc[:3, 3])
        print(f"  frame {i}: fitness {r.fitness:.3f} inlier_rmse {r.inlier_rmse:.3f}")

    merged = o3d.geometry.PointCloud()
    for p in frames:
        pc = o3d.geometry.PointCloud()
        pc.points = o3d.utility.Vector3dVector(p)
        merged += pc
    merged = merged.voxel_down_sample(0.008)
    merged.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.02, max_nn=30))
    merged.orient_normals_consistent_tangent_plane(30)
    print(f"registered cloud: {len(merged.points)} pts (watertight Poisson step)")
    mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        merged, depth=args.poisson_depth)
    bbox = mesh.get_axis_aligned_bounding_box()
    mesh = mesh.crop(o3d.geometry.AxisAlignedBoundingBox(
        bbox.get_min_bound() - 0.02, bbox.get_max_bound() + 0.02))
    mesh.compute_vertex_normals()
    n_before = np.asarray(mesh.triangles).shape[0]
    mesh = mesh.simplify_quadric_decimation(int(n_before * 0.25))
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_unreferenced_vertices()

    v = np.asarray(mesh.vertices) * (args.scale * NORM)
    mesh.vertices = o3d.utility.Vector3dVector(v)
    o3d.io.write_triangle_mesh(args.out, mesh)
    print(f"wrote {args.out}: {len(mesh.vertices)} verts, "
          f"{np.asarray(mesh.triangles).shape[0]} tris (factor {args.scale*NORM:.1f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
