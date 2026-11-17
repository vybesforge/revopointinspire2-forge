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

## Update — Nov 2026 (live re-verification + new tooling)

Ran the box end to end again against live hardware. Confirmations and corrections:

### The depth stream is a BAND, not a full frame
The 640x512 Y16 buffer is mostly padding: **all real depth data lives in a
640x128 band (rows 0-117)**; the rest is zero. Treat the band as the range image.
Inside it a frame-filling subject is ONE connected component (~26k px, 96% of
valid data) — that is the object, not noise.

### The extra Y16 modes are dead ends
The depth node also advertises `1920x512` and `640x1024`. Both are useless:
- `1920x512` = the 640x512 frame tiled 3x horizontally (corr 0.98 between
  thirds, row autocorr peak at 655 ~= 640).
- `640x1024` = a static pattern (frames near-identical, mean|d| 0.05).
`640x512` is the real mode.

### Register knobs: the vendor init sequence is exact
Setting depth options — AE mode `0x912` (incl. close-up), HDR `0x914`, laser
line `0xb07`, gain `0x903`, exposure `0x911` — *either* into a live stream *or*
baked into a fresh init before arming, **collapses the laser**:

| setting | fill% |
|---|---|
| vendor defaults | **8.57** |
| AE close-up 0x912 3 | 0.06 |
| gain high 0x903 0x30 | 0.06 |
| laser single 0xb07 8 | 0.05 |
| exposure 12k 0x911 12000 | 0.05 |

Columns drop 630 -> 57. The vendor's "depth options" ride the app's own
algorithm loop (RPALaserAutoExposure), not reachable by register writes.
`0x914` additionally forces a device re-enumeration.

### Working distance dominates everything
| setup | valid px | fill |
|---|---|---|
| ~3 ft | 371 | 0.11% |
| ~1 ft | 35,663 | 10.8% |
| close, subject fills frame | ~30k | 8.5% |

### RGB + operational notes
- RGB cam streams **only while the depth node is held open** (shared ISP).
- The depth buffer is **vertically flipped** vs RGB.
- The V4L2 `saturation` control is a **no-op** (colorfulness identical before and
  after); the muted RGB look is the sensor, not a fixable slider.
- **USB recovery:** `unbind`/`bind` the USB device (bus3 port 11) brings it back
  without a physical replug. Repeated `0x481` arming can wedge the engine
  (dropped to 0.06% fill) until the device is reset.

### New tooling
- `armcap.py`  — find nodes, arm depth, capture Y16 frames.
- `knobs.py`   — depth-option register sweep, applied to a live stream.
- `preknob.py` — same options baked into a fresh init, then arm (the correct way).
- `mesh.py` consumes the band cleanly: a close, frame-filling capture meshes to a
  coherent surface (29.8k verts / 57.7k tris); a far capture collapses (638 verts).
  Note: mesh input files must be named `depth_*.raw`.

## License
Apache 2.0. Clean-room reimplementation from protocol observations; no vendor
code is redistributed.
