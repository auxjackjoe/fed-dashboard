#!/usr/bin/env python
"""Tiny server for the dashboard.   python -I server.py   ->  http://<this-PC-name>:8769/fed_cheatsheet.html
POST /api/refresh  runs build_data.py (live) in the background;  GET /api/status shows progress."""
import http.server, socketserver, subprocess, sys, os, json, threading, time, re

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8769"))
PUBLIC = re.compile(r"^/(?:fed_cheatsheet\.html|index\.html|data\.json|itc_2nov\.json|favicon\.ico|photos/[\w-]+\.(?:jpe?g|png|webp))$")
STATE = {"running": False, "started": None, "finished": None, "ok": None, "tail": ""}

def run_build(demo=False):
    STATE.update(running=True, started=time.time(), ok=None, tail="")
    cmd = [sys.executable, "-I", os.path.join(HERE, "build_data.py")] + (["--demo"] if demo else [])
    try:
        p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        for line in p.stdout:
            lines.append(line.rstrip()); STATE["tail"] = "\n".join(lines[-12:])
        p.wait(); STATE["ok"] = p.returncode == 0
    except Exception as e:
        STATE["tail"] = str(e); STATE["ok"] = False
    STATE.update(running=False, finished=time.time())

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=HERE, **k)
    def _json(self, o, code=200):
        b = json.dumps(o).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/api/status"): return self._json(STATE)
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/": self.send_response(302); self.send_header("Location", "/fed_cheatsheet.html"); self.end_headers(); return
        if not PUBLIC.match(path): return self.send_error(404)   # scripts, cache, logs, word list etc. are never served
        return super().do_GET()
    def do_POST(self):
        if self.path.startswith("/api/refresh"):
            if not STATE["running"]:
                threading.Thread(target=run_build, args=("demo" in self.path,), daemon=True).start()
            return self._json(STATE)
        self.send_error(404)
    def end_headers(self):
        self.send_header("Cache-Control", "no-store"); super().end_headers()
    def log_message(self, *a): pass

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True

def auto_refresh_loop():
    """Rebuild live data when it is more than 20 hours old (checked hourly). A failed build keeps the old data."""
    time.sleep(20)
    while True:
        try:
            p = os.path.join(HERE, "data.json")
            old = (not os.path.exists(p)) or (time.time() - os.path.getmtime(p) > 20 * 3600)
            try: demo = json.load(open(p)).get("meta", {}).get("demo", True)
            except Exception: demo = True
            if (old or demo) and not STATE["running"] and os.environ.get("AUTO_REFRESH", "1") == "1":
                run_build(False)
        except Exception: pass
        time.sleep(3600)

if __name__ == "__main__":
    threading.Thread(target=auto_refresh_loop, daemon=True).start()
    print(f"Serving on http://0.0.0.0:{PORT}/fed_cheatsheet.html  (Ctrl+C to stop)")
    S(("0.0.0.0", PORT), H).serve_forever()
