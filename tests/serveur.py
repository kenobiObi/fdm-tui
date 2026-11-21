import asyncio, hashlib, os, re, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

DATA = os.urandom(12 * 1024 * 1024 + 123)   # ~12 Mio
SMALL = os.urandom(5000)
COUNT = {"flaky": 0}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _send(self, data, name=None, ranges=True):
        rng = self.headers.get("Range")
        start, end, code = 0, len(data) - 1, 200
        if rng and ranges:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else len(data) - 1
            end = min(end, len(data) - 1); code = 206
        body = data[start:end + 1]
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        if code == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        if ranges: self.send_header("Accept-Ranges", "bytes")
        if name: self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        try:
            for i in range(0, len(body), 16384):
                self.wfile.write(body[i:i+16384])
        except Exception: pass
    def do_GET(self):
        p = self.path
        if p.startswith("/big.bin"): self._send(DATA)
        elif p.startswith("/norange.bin"): self._send(DATA, ranges=False)
        elif p.startswith("/cd"): self._send(SMALL, name="../../evil name.pdf")
        elif p.startswith("/small.txt"): self._send(SMALL)
        elif p.startswith("/flaky.bin"):
            COUNT["flaky"] += 1
            if COUNT["flaky"] <= 2:
                self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
            self._send(DATA)
        elif p.startswith("/404"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
        elif p.startswith("/page"):
            html = b'<a href="big.bin">x</a><a href="/cd.pdf">p</a><img src="img/a.png"><a href="mailto:x">m</a><a href="/page2">no</a><a href="big.bin#frag">dup</a>'
            self.send_response(200); self.send_header("Content-Type","text/html"); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html)
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()

def demarrer(port=8765):
    s = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s
SHA = hashlib.sha256(DATA).hexdigest()
