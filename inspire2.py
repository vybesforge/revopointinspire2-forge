#!/usr/bin/env python3
"""Revopoint INSPIRE 2 clean-room Linux driver.

Arms the depth engine and captures Y16 depth frames. Reverse-engineered from
the vendor driver + a USBPcap capture. Apache 2.0.

Needs root to write the HID node (or a udev rule granting access).
"""
import argparse, glob, os, struct, subprocess, sys, time
from datetime import datetime

DEP_CARD = "REVO_PRODUCT: INSPIRE2-REVO-Dep"
RGB_CARD = "REVO_PRODUCT: INSPIRE2-REVO-RGB"
HID_NAME = "Chishine3d REVO_PRODUCT"

# Init + depth-arm sequence (from the vendor capture, in order).
INIT_SEQ = [
    "echo s 0x483 >/dev/rk_preisp",
    "echo s 0x707 200 600 >/dev/rk_preisp",
    "echo s 0x904 0 >/dev/rk_preisp",
    "echo s 0x702 640 512 > /dev/rk_preisp",
    "echo s 0x903 0x10 > /dev/rk_preisp",
    "echo s 0x910 7000 >/dev/rk_preisp",
    "echo s 0x911 5000 > /dev/rk_preisp",
    "echo s 0x488 0 > /dev/rk_preisp",
    "echo s 0x48b 10 > /dev/rk_preisp",
    "echo s 0xb03 0 > /dev/rk_preisp",
    "echo s 0x481 1 >/dev/rk_preisp",   # depth engine ON
]

def find_hid():
    for h in glob.glob("/dev/hidraw*"):
        try:
            u = open(f"/sys/class/hidraw/{os.path.basename(h)}/device/uevent").read()
            if HID_NAME in u:
                return h
        except Exception:
            pass
    return None

def find_depth():
    for v in glob.glob("/dev/video*"):
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
    with open(hid, "wb") as f:
        f.write((hdr + payload + b"\x00" * 1024)[:1024])

def arm_depth(hid):
    for c in INIT_SEQ:
        send_cmd(hid, c)
        time.sleep(0.15)

def scan(args):
    hid = find_hid()
    if not hid:
        print("scanner HID node not found (is it plugged in?)", file=sys.stderr)
        sys.exit(1)
    print(f"HID: {hid}")
    arm_depth(hid)
    print("depth engine armed (device may re-enumerate)")
    time.sleep(4)  # let re-enumeration settle
    dep = find_depth()
    if not dep:
        print("depth node not found after arming", file=sys.stderr)
        sys.exit(1)
    print(f"depth: {dep}")
    out = args.out or datetime.now().strftime("inspire2-scan-%Y%m%d-%H%M%S")
    os.makedirs(out, exist_ok=True)
    print(f"recording {args.seconds}s depth -> {out}/")
    cmd = ["ffmpeg", "-y", "-f", "v4l2", "-input_format", "y16",
           "-video_size", "640x512", "-framerate", "15", "-i", dep,
           "-c", "copy", f"{out}/depth_%06d.raw"]
    subprocess.run(cmd)
    n = len(glob.glob(f"{out}/depth_*.raw"))
    print(f"done: {n} depth frames in {out}/")

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="arm depth + record frames")
    s.add_argument("-o", "--out", default=None)
    s.add_argument("-s", "--seconds", type=float, default=10.0)
    sub.add_parser("status", help="show scanner nodes")
    args = ap.parse_args()
    if args.cmd == "status":
        print("HID:", find_hid())
        print("depth:", find_depth())
    else:
        scan(args)

if __name__ == "__main__":
    main()
