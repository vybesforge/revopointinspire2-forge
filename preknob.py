#!/usr/bin/env python3
"""INSPIRE 2: set depth options BEFORE arming, then measure.

The earlier sweep poked registers into a *live* stream and everything collapsed.
The vendor app configures, then starts. So here each trial re-runs the full init
with the knob values baked in, arms, and captures.
"""
import glob, os, struct, subprocess, sys, tempfile, time
import numpy as np

HID_NAME = "Chishine3d REVO_PRODUCT"
DEP_CARD = "REVO_PRODUCT: INSPIRE2-REVO-Dep"

BASE = [
    "echo s 0x483 >/dev/rk_preisp",
    "echo s 0x707 200 600 >/dev/rk_preisp",
    "echo s 0x904 0 >/dev/rk_preisp",
    "echo s 0x702 640 512 > /dev/rk_preisp",
    "echo s 0x903 0x10 > /dev/rk_preisp",
    "echo s 0x910 7000 > /dev/rk_preisp",
    "echo s 0x911 5000 > /dev/rk_preisp",
    "echo s 0x488 0 >/dev/rk_preisp",
    "echo s 0x48b 10 >/dev/rk_preisp",
    "echo s 0xb03 0 >/dev/rk_preisp",
]
ARM = "echo s 0x481 1 >/dev/rk_preisp"


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


def arm(seq):
    hid = find_hid()
    if not hid:
        return None
    for c in seq:
        send(hid, c)
        time.sleep(0.15)
    time.sleep(3)
    for _ in range(20):
        if find_depth():
            break
        time.sleep(0.5)
    return find_depth()


def measure(node, secs=4.0):
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
        return dict(frames=len(fs), fill=0.0, px=0, p50=0, spread=0, cols=0)
    z = m[v]
    return dict(frames=len(fs), fill=100 * v.mean(), px=int(v.sum()),
                p50=float(np.median(z)),
                spread=float(np.percentile(z, 95) - np.percentile(z, 5)),
                cols=int(v.any(axis=0).sum()))


def main():
    trials = [
        ("baseline (defaults)",        BASE),
        ("AE close-up     0x912 3",    BASE + ["echo s 0x912 3 >/dev/rk_preisp"]),
        ("gain high       0x903 0x30", BASE[:4] + ["echo s 0x903 0x30 >/dev/rk_preisp"] + BASE[5:]),
        ("laser single    0xb07 8",    BASE + ["echo s 0xb07 8 >/dev/rk_preisp"]),
        ("exposure 12k    0x911 12000", BASE[:6] + ["echo s 0x911 12000 >/dev/rk_preisp"] + BASE[7:]),
    ]
    print(f"{'trial':26s} {'frames':>6} {'fill%':>7} {'px':>7} {'cols':>5} {'p50':>6} {'spread':>7}")
    print("-" * 70)
    for label, seq in trials:
        node = arm(seq + [ARM])
        if not node:
            print(f"{label:26s}  NO NODE"); continue
        r = measure(node)
        if not r:
            print(f"{label:26s}  no frames"); continue
        print(f"{label:26s} {r['frames']:>6} {r['fill']:>7.2f} {r['px']:>7} "
              f"{r['cols']:>5} {r['p50']:>6.0f} {r['spread']:>7.0f}")
    # leave armed with defaults
    arm(BASE + [ARM])
    print("\nre-armed with defaults")
    return 0


if __name__ == "__main__":
    sys.exit(main())
