#!/usr/bin/env python3
"""INSPIRE 2 depth arm + capture (fresh, 2026-11).

Framing/protocol facts (from the Oct-2026 clean-room RE):
  report = b"ZZZZ" + b"\x01\x01\x00\x10" + 32B zeros + LE32 len + ASCII cmd
  cmd    = "echo s 0xNNN <val> >/dev/rk_preisp"  (Rockchip pre-ISP register write)
  depth ON = 0x481 1  -> device re-enumerates, nodes change.
"""
import glob, os, struct, subprocess, sys, time

HID_NAME = "Chishine3d REVO_PRODUCT"
DEP_CARD = "REVO_PRODUCT: INSPIRE2-REVO-Dep"

INIT_SEQ = [
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
    "echo s 0x481 1 >/dev/rk_preisp",     # depth engine ON
]


def find_hid():
    for h in sorted(glob.glob("/dev/hidraw*")):
        try:
            u = open(f"/sys/class/hidraw/{os.path.basename(h)}/device/uevent").read()
            if HID_NAME in u:
                return h
        except Exception:
            pass
    return None


def find_depth():
    for v in sorted(glob.glob("/dev/video*"),
                    key=lambda p: int(p.rsplit("video", 1)[-1])):
        try:
            n = open(f"/sys/class/video4linux/{os.path.basename(v)}/name").read().strip()
            if n == DEP_CARD:
                return v
        except Exception:
            pass
    return None


def send_cmd(hid, cmd):
    payload = cmd.encode()
    hdr = b"ZZZZ" + b"\x01\x01\x00\x10" + b"\x00" * 32 + struct.pack("<I", len(payload))
    with open(hid, "wb", buffering=0) as f:
        f.write((hdr + payload + b"\x00" * 1024)[:1024])


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/scans/depth_run")
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

    hid = find_hid()
    if not hid:
        print("FAIL: scanner HID not found", file=sys.stderr); return 1
    print(f"HID control node : {hid}")
    print(f"depth before arm : {find_depth()}")

    print("arming depth engine (device will re-enumerate)...")
    for c in INIT_SEQ:
        send_cmd(hid, c)
        time.sleep(0.15)
        print(f"  -> {c}")
        # stop early if the device drops (expected on 0x481 1)
        time.sleep(0.05)

    # wait for re-enumeration
    dep = None
    for i in range(30):
        time.sleep(1)
        d = find_depth()
        if d:
            dep = d
            print(f"re-enumerated after {i+1}s: depth node = {d}")
            break
    if not dep:
        print("FAIL: depth node never came back", file=sys.stderr); return 1

    time.sleep(2)  # settle
    os.makedirs(out, exist_ok=True)
    print(f"capturing {secs}s Y16 -> {out}/")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "v4l2", "-input_format", "y16", "-video_size", "640x512",
        "-framerate", "15", "-t", str(secs), "-i", dep,
        "-c", "copy", f"{out}/depth_%06d.raw",
    ])
    n = len(glob.glob(f"{out}/depth_*.raw"))
    print(f"done: {n} depth frames in {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
