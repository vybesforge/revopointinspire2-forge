#!/usr/bin/env python3
"""INSPIRE2 web front end (clean-room, Apache 2.0).

HTTP backend driving the clean-room scanner tools over the browser:
  GET  /              -> single-file SPA (index.html)
  GET  /status        -> scanner nodes + armed state
  POST /view          -> capture a fresh depth view -> /view.png
  POST /depth {exp,gain} -> tune depth exposure(0x910)/gain(0x911)
  POST /laser {on}    -> IR/laser projector engine (0x912)
  POST /scan {sec}    -> arm + capture N seconds -> report frame count
  POST /mesh {dir}    -> depth dir -> STL -> /latest.stl
  GET  /view.png      -> the latest centering view
  GET  /latest.stl    -> the latest mesh as an STL download

LAN-facing (binds 0.0.0.0). Driver + venv paths below.
"""
import json, os, subprocess, sys, time, struct

import http.server

FORGE = "/opt/inspire2/forge"
VENV = "/opt/inspire2/.venv/bin/python3"
SCANS = "/home/billie/scans/web"
RUNS = "/opt/inspire2/web/runs"
os.makedirs(SCANS, exist_ok=True)
os.makedirs(RUNS, exist_ok=True)

VIEW_PNG = os.path.join(RUNS, "view.png")
COLOR_PNG = os.path.join(RUNS, "color.png")
LATEST_STL = os.path.join(RUNS, "latest.stl")
CURRENT_DIR = ""


def _hid():
    for h in os.listdir("/dev"):
        if h.startswith("hidraw"):
            try:
                u = open(f"/sys/class/hidraw/{h}/device/uevent").read()
                if "REVO_PRODUCT" in u:
                    return f"/dev/{h}"
            except Exception:
                pass
    return None


def _hid_write(cmd: bytes):
    hid = _hid()
    if not hid:
        return False
    hdr = b"ZZZZ" + b"\x01\x01\x00\x10" + b"\x00" * 32 + struct.pack("<I", len(cmd))
    with open(hid, "wb") as f:
        f.write((hdr + cmd + b"\x00" * 1024)[:1024])
        f.flush()
    return True


def set_depth(on: bool):
    """Arm (1) or de-arm (0) the depth engine via the HID control node."""
    ok = _hid_write(b"echo s 0x481 %d >/dev/null rk_preisp" % (1 if on else 0))
    time.sleep(2.5)      # device re-enumerates: give it time to settle
    return ok


def set_depth_reg(reg, val):
    """Write a depth pre-ISP register (e.g. 0x910 exposure, 0x911 gain)."""
    ok = _hid_write(f"echo s {reg} {val} >/dev/null rk_preisp".encode())
    time.sleep(0.3)
    return ok


def find_rgb():
    for v in sorted(os.listdir("/sys/class/video4linux")):
        try:
            nm = open(f"/sys/class/video4linux/{v}/name").read().strip()
            if "REVO" in nm and "RGB" in nm:
                return f"/dev/{v}"
        except Exception:
            pass
    return None


def capture_color():
    """de-arm depth, grab one RGB MJPG frame, light-de-purple, save PNG."""
    set_depth(False)
    time.sleep(1.0)
    jpg = os.path.join(RUNS, "color_raw.jpg")
    last = ""
    for attempt in range(3):
        node = find_rgb()
        if not node:
            last = "no RGB node yet (re-enum)"
            time.sleep(2.0)
            continue
        if os.path.exists(jpg):
            os.remove(jpg)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "v4l2", "-input_format", "mjpeg",
                            "-video_size", "1280x800", "-i", node,
                            "-frames:v", "1", jpg],
                           capture_output=True, text=True, timeout=30)
        if os.path.exists(jpg):
            break
        last = (r.stderr or "rgb capture failed")[-300:]
        time.sleep(2.0)
    if not os.path.exists(jpg):
        return False, last
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(jpg).convert("RGB")
        a = np.asarray(im, "f4")
        r_, g_, b_ = a[..., 0].mean(), a[..., 1].mean(), a[..., 2].mean()
        gnorm = max(g_, 1e-6)
        a[..., 0] *= min(1.0, gnorm / max(r_, 1e-6) * 1.05)
        a[..., 2] *= min(1.0, gnorm / max(b_, 1e-6) * 1.05)
        Image.fromarray(a.astype("u1")).save(COLOR_PNG)
    except Exception:
        Image.open(jpg).convert("RGB").save(COLOR_PNG)
    return True, "ok"


def run(*args, cwd=FORGE, timeout=280):
    cmd = [VENV] + list(args) if args and args[0].endswith(".py") else list(args)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=cwd, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


class H(http.server.BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_file(self, path, ctype, dl=None):
        if not os.path.exists(path):
            self._json({"error": "not found"}, 404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if dl:
            self.send_header("Content-Disposition", f'attachment; filename="{dl}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p in ("/", "/index.html"):
            idx = os.path.join(os.path.dirname(__file__), "index.html")
            self._send_file(idx, "text/html; charset=utf-8")
        elif p == "/status":
            self._status()
        elif p == "/view.png":
            self._send_file(VIEW_PNG, "image/png")
        elif p == "/color.png":
            self._send_file(COLOR_PNG, "image/png")
        elif p == "/latest.stl":
            self._send_file(LATEST_STL, "model/stl", dl="scan.stl")
        else:
            self._json({"error": "unknown"}, 404)

    def _status(self):
        s = {"hid": None, "depth": None}
        for n in sorted(os.listdir("/sys/class/video4linux")):
            try:
                nm = open(f"/sys/class/video4linux/{n}/name").read().strip()
                if "REVO_PRODUCT" in nm and "Dep" in nm and s["depth"] is None:
                    s["depth"] = f"/dev/{n}"
            except Exception:
                pass
        for h in os.listdir("/dev"):
            if h.startswith("hidraw"):
                try:
                    u = open(f"/sys/class/hidraw/{h}/device/uevent").read()
                    if "REVO_PRODUCT" in u:
                        s["hid"] = f"/dev/{h}"
                except Exception:
                    pass
        s["scanner"] = bool(s["depth"] and s["hid"])
        self._json(s)

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        ln = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(ln).decode() if ln else "{}"
        try:
            d = json.loads(body) if body else {}
        except Exception:
            d = {}
        global CURRENT_DIR
        if p in ("/view", "/scan"):
            set_depth(True)          # ensure depth engine armed first
            time.sleep(0.4)
        if p == "/view":
            rc, out = run("/opt/inspire2/forge/view.py", "-o", VIEW_PNG)
            self._json({"ok": rc == 0, "relative_url": "/view.png"})
        elif p == "/color":
            ok, msg = capture_color()
            self._json({"ok": ok, "msg": msg, "relative_url": "/color.png"})
        elif p == "/depth":
            exp = d.get("exposure")
            gain = d.get("gain")
            if exp is not None:
                set_depth_reg("0x910", int(exp))
            if gain is not None:
                set_depth_reg("0x911", int(gain))
            self._json({"ok": True, "exposure": exp, "gain": gain})
        elif p == "/laser":
            on = bool(d.get("on", True))
            set_depth_reg("0x912", 1 if on else 0)
            self._json({"ok": True, "laser": on})
        elif p == "/scan":
            sec = float(d.get("sec", 8))
            tag = f"scan_{int(time.time())}"
            dr = os.path.join(SCANS, tag)
            rc, out = run("inspire2.py", "scan", "-s", str(sec), "-o", dr)
            n = len([f for f in os.listdir(dr) if f.startswith("depth_")]) if os.path.isdir(dr) else 0
            self._json({"ok": rc == 0, "dir": dr, "frames": n, "log": out[-1200:]})
        elif p == "/mesh":
            dr = d.get("dir") or CURRENT_DIR or ""
            if not os.path.isdir(dr):
                self._json({"error": "need a scan dir first"}, 400)
            rc, out = run("mesh.py", dr, "-o", LATEST_STL)
            self._json({"ok": rc == 0, "stl": "/latest.stl", "log": out[-1200:]})
        else:
            self._json({"error": "unknown"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"INSPIRE2 web front end on http://0.0.0.0:{port}")
    server.serve_forever()
