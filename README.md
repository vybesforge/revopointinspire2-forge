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
./inspire2.py scan -s 10 -o ~/scans/mug
```
Arms the depth engine and records depth frames. Requires root to write the
HID node (or a udev rule).

## Files
- `inspire2.py` — the driver (find nodes, arm depth, capture).
- `protocol.md` — the reverse-engineered control protocol.
- `capture/` — the USBPcap capture + analysis.

## License
Apache 2.0. Clean-room reimplementation from protocol observations; no vendor
code is redistributed.
