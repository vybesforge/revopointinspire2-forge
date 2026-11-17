#!/usr/bin/env python3
"""Sweep INSPIRE 2 depth-engine register knobs and measure depth fill.

Every knob is a Rockchip pre-ISP register write over the scanner's HID control
channel. We capture a few seconds of Y16 depth for each setting and report the
valid-pixel fraction + depth spread, so we can pick the best 'depth option'.

Register labels (from the vendor DLL):
  0x912 AE mode: 0 off, 1 framerate, 2 quality, 3 close-up
  0x914 HDR:     0 off, 1 high-reflection, 2 dark, 3 composite
  0xb07 laser line mode: 7 multi, 8 single
  0x903 depth gain (0x10 = 16 default)
  0x911 depth exposure time
  0x913 depth ROI 0-100
  0x48d laser brightness 0-255
"""
import glob, os, struct, subprocess, sys, tempfile, time
import numpy as np

HID_NAME = "Chishine3d REVO_PRODUCT"
DEP_CARD = "REVO_PRODUCT: INSPIRE2-REVO-Dep"


def find_hid():
    for h in sorted(glob.glob("/dev/hidraw*")):
        try:
            if HID_NAME in open(f"/sys/class/hidraw/{os.path.basename(h)}/device/uevent").read():
                return h
        except Exception:
            pass
    return None


def find_depth():
    for v in sorted(glob.glob("/dev/video*"), key=lambda p: int(p.rsplit("video", 1)[-1])):
        try:
            if open(f"/sys/class/video4linux/{os.path.basename(v)}/name").read().strip() == DEP_CARD:
                return v
        except Exception:
            pass
    return None


def send(hid, cmd):
    p = cmd.encode()
    hdr = b"ZZZZ" + b"\x01\x01\x00\x10" + b"\x00" * 32 + struct.pack("<I", len(p))
    with open(hid, "wb", buffering=0) as f:
        f.write((hdr + p + b"\x00" * 1024)[:1024])


def wr(hid, reg_val):
    send(hid, f"echo s {reg_val} >/dev/rk_preisp")
    time.sleep(0.12)


def capture(node, secs=3.0):
    d = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "v4l2", "-input_format", "y16", "-video_size", "640x512",
                    "-framerate", "15", "-t", str(secs), "-i", node,
                    "-c", "copy", f"{d}/f_%04d.raw"], capture_output=True)
    fs = sorted(glob.glob(f"{d}/f_*.raw"))
    if not fs:
        return None
    st = np.stack([np.fromfile(f, "<u2").reshape(512, 640) for f in fs])
    m = np.median(st, axis=0).astype("f4")
    v = (m > 0) & (m < 60000)
    if not v.any():
        return dict(frames=len(fs), fill=0.0, px=0, spread=0)
    z = m[v]
    return dict(frames=len(fs), fill=100 * v.mean(), px=int(v.sum()),
                spread=float(np.percentile(z, 95) - np.percentile(z, 5)),
                p50=float(np.median(z)))


def main():
    hid = find_hid()
    if not hid:
        print("no HID node"); return 1
    node = find_depth()
    print(f"HID={hid}  depth={node}\n")

    trials = [
        ("baseline (as armed)",                []),
        ("AE close-up        0x912 3",         ["0x912 3"]),
        ("AE off            0x912 0",          ["0x912 0"]),
        ("laser: single line 0xb07 8",         ["0xb07 8"]),
        ("laser: multi line  0xb07 7",         ["0xb07 7"]),
        ("gain up           0x903 0x30",       ["0x903 0x30"]),
        ("HDR high-refl      0x914 1",         ["0x914 1"]),
        ("HDR dark           0x914 2",         ["0x914 2"]),
        ("ROI full           0x913 100",       ["0x913 100"]),
        ("laser bright 0x48d 255",             ["0x48d 255"]),
    ]
    print(f"{'setting':32s} {'frames':>6} {'fill%':>7} {'px':>7} {'p50':>6} {'spread':>7}")
    print("-" * 74)
    for label, cmds in trials:
        for c in cmds:
            wr(hid, c)
        time.sleep(0.4)
        r = capture(find_depth() or node)
        if not r:
            print(f"{label:32s}  no frames"); continue
        print(f"{label:32s} {r['frames']:>6} {r['fill']:>7.2f} {r.get('px',0):>7} "
              f"{r.get('p50',0):>6.0f} {r.get('spread',0):>7.0f}")

    # restore sane defaults
    for c in ["0x912 0", "0x914 0", "0xb07 7", "0x903 0x10", "0x913 0", "0x48d 128"]:
        wr(hid, c)
    print("\nrestored defaults: 0x912 0 / 0x914 0 / 0xb07 7 / 0x903 0x10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
