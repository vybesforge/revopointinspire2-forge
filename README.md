# Revopoint INSPIRE 2 — clean-room Linux driver (Apache 2.0)

Drive the Revopoint INSPIRE 2 3D scanner from Linux, no vendor software.
Reverse-engineered from the scanner's own Windows driver (Revo Metro 5.8.8)
and a USBPcap capture of the real control traffic.

## Status
- **Framing cracked:** control reports = `5a5a5a5a` magic + `01010010` + 32B
  context + 4B LE length + ASCII command.
- **Control protocol:** commands are `echo s 0xNNN <val> >/dev/rk_preisp`
  register writes to the Rockchip pre-ISP.
- **Depth engine armed** by `echo s 0x481 1`; depth stream flows as Y16
  640x512 @15fps on the depth UVC node.
- **Working:** arm depth → stream Y16 depth frames.
- **In progress:** full depth map (needs object in range), meshing to STL.

## Hardware
- VID:PID `2207:110c` (Chishine3d / Rockchip), SN `D2590165AB6H00AC5`.
- USB interfaces: IF0/1 RGB UVC, IF2/3 depth UVC (bulk), IF4 HID control.
- Depth cam: `REVO_PRODUCT: INSPIRE2-REVO-Dep` (Y16 640x512, MJPG 1280x1024).
- RGB cam: `REVO_PRODUCT: INSPIRE2-REVO-RGB` (MJPG 1280x800).

## Usage
```
./inspire2.py scan -s 10 -o ~/scans/mug    # arm depth engine + capture Y16 frames
./mesh.py ~/scans/mug -o mug.stl           # fuse -> point cloud -> STL
```
`scan` arms the depth engine (register writes to the Rockchip pre-ISP over the
HID node) and records Y16 depth frames. A udev rule (`99-inspire2.rules`)
grants the `video` group access to the HID node, so no root is needed.

## Depth -> mesh
`mesh.py` unprojects valid depth pixels to mm (pinhole model), depth-median-fuses
multiple frames, triangulates the 2.5D range image, and exports an STL. Runs from
the project venv: `infer` deps in `.venv` (numpy, trimesh, scipy, networkx).

Calibration is the accuracy step: the scanner's real intrinsics and the
raw-depth->mm scale ride the network/adb channel (not yet pulled), so
`--cx/--cy/--fx/--fy/--scale` default to nominal values. Correct them once
intrinsics are known:
```
./mesh.py ~/scans/mug -o mug.stl --fx 600 --fy 598 --scale 4.2
```

## Files
- `inspire2.py` — the driver (find nodes, arm depth, capture).
- `protocol.md` — the reverse-engineered control protocol.
- `capture/` — the USBPcap capture + analysis.

## License
Apache 2.0. Clean-room reimplementation from protocol observations; no vendor
code is redistributed.
