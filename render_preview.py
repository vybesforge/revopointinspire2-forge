#!/usr/bin/env python3
"""Render a 3D preview PNG of a mesh (decimated) — headless matplotlib."""
import argparse, sys
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stl")
    ap.add_argument("-o", default="/tmp/mesh_preview.png")
    ap.add_argument("--tris", type=int, default=25000)
    args = ap.parse_args()

    mesh = trimesh.load(args.stl)
    if len(mesh.faces) > args.tris:
        ratio = min(0.9, 1.0 - (args.tris / len(mesh.faces)))
        mesh = mesh.simplify_quadric_decimation(ratio)
    v = mesh.vertices
    f = mesh.faces

    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_trisurf(v[:, 0], v[:, 1], v[:, 2], triangles=f,
                    cmap="viridis", antialiased=True, shade=True)
    ax.set_box_aspect((np.ptp(v[:, 0]), np.ptp(v[:, 1]), np.ptp(v[:, 2])))
    ax.view_init(elev=35, azim=-60)
    plt.tight_layout()
    plt.savefig(args.o, dpi=110, facecolor="w")
    print(f"preview -> {args.o}  ({len(f)} tris)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
