"""Веб-сервер отдельной версии: страница монитора, API хранилища для неё и запуск сбора по кнопке «Обновить».

  python3 -m misb.server --db data/misb.sqlite3 --site ../site/monitor.html --port 8080

Вход — HTTP Basic: логин и пароль из MISB_USER / MISB_PASSWORD (без них сервер не стартует, кроме --no-auth для локальной проверки).
Сервер слушает 127.0.0.1; наружу его отдаёт Caddy с HTTPS (deploy/Caddyfile).
"""
import argparse, base64, fcntl, gzip, hmac, json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .store import Store, VersionConflict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# каркас страницы: то же, что площадка артефактов добавляет при публикации (кодировка, viewport, небольшой сброс стилей)
HEAD = ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
        'body{margin:0;font:14px system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#fafaf9}img{max-width:100%}[hidden]{display:none!important}</style>'
        '<script src="/claude-shim.js"></script></head><body>')
TAIL = "</body></html>"


def busy(path):
    """Занят ли замок сбора (fcntl.flock держит процесс misb.agent)."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


class Run:
    """Один сбор за раз: повторное нажатие кнопки, пока идёт сбор, ничего не запускает."""
    def __init__(self, cmd):
        self.cmd, self.proc, self.lock = cmd, None, threading.Lock()
        self.lockfile = cmd[cmd.index("--db") + 1] + ".run.lock"

    def running(self):
        if self.proc is not None and self.proc.poll() is None:
            return True
        return busy(self.lockfile)          # сбор по расписанию (systemd-таймер) держит тот же замок

    def start(self, mode):
        with self.lock:
            if self.running():
                return False
            log = open(os.path.join(os.environ.get("MISB_LOG_DIR", "."), "run-last.log"), "ab")
            self.proc = subprocess.Popen(self.cmd + ["--mode", mode, "--manual"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            return True


def make_handler(store, site, run, auth):
    class H(BaseHTTPRequestHandler):
        server_version = "misb/1.0"

        def log_message(self, fmt, *a):
            if os.environ.get("MISB_ACCESS_LOG"):
                super().log_message(fmt, *a)

        def _auth(self):
            if not auth:
                return True
            h = self.headers.get("Authorization", "")
            ok = h.startswith("Basic ") and hmac.compare_digest(h[6:].strip(), auth)
            if not ok:
                self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="MISB", charset="UTF-8"'); self.send_header("Content-Length", "0"); self.end_headers()
            return ok

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body, ensure_ascii=False).encode()
            if len(body) > 2048 and "gzip" in self.headers.get("Accept-Encoding", ""):
                body = gzip.compress(body, 5); enc = True
            else:
                enc = False
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
            if enc:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            if not self._auth():
                return
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(200, (HEAD + open(site, encoding="utf-8").read() + TAIL).encode(), "text/html; charset=utf-8")
            if u.path == "/claude-shim.js":
                return self._send(200, open(os.path.join(ROOT, "web", "claude-shim.js"), "rb").read(), "text/javascript; charset=utf-8")
            if u.path == "/api/doc":
                data, ver = store.get(q["path"][0])
                return self._send(200, {"exists": data is not None, "data": data, "version": ver})
            if u.path == "/api/col":
                c = q["path"][0]
                return self._send(200, {"rev": store.rev(c), "docs": [{"id": i, "data": d, "version": v} for i, d, v in store.list(c)]})
            if u.path == "/api/revs":
                return self._send(200, {c: store.rev(c) for c in q.get("c", [])})
            if u.path == "/api/run":
                return self._send(200, {"running": run.running()})
            self._send(404, {"error": "not found"})

        def do_PUT(self):
            if not self._auth():
                return
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/api/doc":
                try:
                    b = self._body()
                    ver = store.write(b.get("op", "set"), q["path"][0], data=b.get("data"), if_version=b.get("if_version"))
                    return self._send(200, {"version": ver})
                except VersionConflict as e:
                    return self._send(409, {"error": str(e), "current": e.current})
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            if not self._auth():
                return
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/api/doc":
                store.write("delete", q["path"][0]); return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            if urlparse(self.path).path == "/api/refresh":
                mode = (self._body().get("mode") or "full")
                started = run.start("light" if mode == "light" else "full")
                return self._send(202 if started else 409, {"started": started, "running": True})
            self._send(404, {"error": "not found"})
    return H


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--db", default=os.environ.get("MISB_DB", "data/misb.sqlite3"))
    a.add_argument("--site", default=os.environ.get("MISB_SITE", os.path.join(ROOT, "..", "site", "monitor.html")))
    a.add_argument("--host", default="127.0.0.1"); a.add_argument("--port", type=int, default=int(os.environ.get("MISB_PORT", "8080")))
    a.add_argument("--no-auth", action="store_true")
    x = a.parse_args()
    user, pw = os.environ.get("MISB_USER", ""), os.environ.get("MISB_PASSWORD", "")
    if not x.no_auth and not (user and pw):
        sys.exit("задайте MISB_USER и MISB_PASSWORD (или --no-auth для локальной проверки)")
    auth = "" if x.no_auth else base64.b64encode(f"{user}:{pw}".encode()).decode()
    os.makedirs(os.path.dirname(os.path.abspath(x.db)), exist_ok=True)
    store = Store(x.db)
    run = Run([sys.executable, "-m", "misb.agent", "--db", os.path.abspath(x.db)])
    srv = ThreadingHTTPServer((x.host, x.port), make_handler(store, os.path.abspath(x.site), run, auth))
    print(f"misb: http://{x.host}:{x.port} (база {x.db})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
