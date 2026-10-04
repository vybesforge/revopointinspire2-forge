#!/usr/bin/env python3
"""INSPIRE2 web front end (clean-room, Apache 2.0).

Minimal HTTP backend driving the clean-room scanner tools over the browser:
  GET  /              -> single-file SPA (index.html)
  GET  /status        -> scanner nodes + armed state
  POST /view          -> capture a fresh depth view -> /view.png
  POST /scan {sec}    -> arm + capture N seconds -> report frame count
  POST /mesh {dir}    -> depth dir -> STL -> /latest.stl
  GET  /view.png      -> the latest centering view
  GET  /latest.stl    -> the latest mesh as an STL download
  GET  /latest.ply    -> the latest registered phi cloud/PLY if present

LAN-facing (binds 0.0.0.0). Driver + venv paths below.
"""
import json, os, subprocess, sys, time

import http.server

FORGE = "/opt/inspire2/forge"
VENV = "/opt/inspire2/.venv/bin/python3"
SCANS = "/home/billie/scans/web"
RUNS = "/opt/inspire2/web/runs"
os.makedirs(SCANS, exist_ok=True)
os.makedirs(RUNS, exist_ok=True)

VIEW_PNG = os.path.join(RUNS, "view.png")
LATEST_STL = os.path.join(RUNS, "latest.stl")
CURRENT_DIR = ""


def run(*args, cwd=FORGE, timeout=280):
    cmd = [VENV] + list(args) if args and args[0].endswith(".py") else list(args)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=cwd, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def status():
    out = {"scanner": False, "depth": None, "hid": None}
    rc, txt = run("status") if False else (None, None)
    return out


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
        elif p == "/latest.stl":
            self._send_file(LATEST_STL, "model/stl", dl="scan.stl")
        else:
            self._json({"error": "unknown"}, 404)

    def _status(self):
        s = {"hid": None, "depth": None}
        for n in sorted(os.listdir("/sys/class/video4linux")):
            try:
                nm = open(f"/sys/class/video4linux/{n}/name").read().strip()
                if "REVO_PRODUCT" in nm:
                    if "Dep" in nm and s["depth"] is None:
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
        if p == "/view":
            rc, out = run("/opt/inspire2/forge/view.py", "-o", VIEW_PNG)
            self._json({"ok": rc == 0, "relative_url": "/view.png"})
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
