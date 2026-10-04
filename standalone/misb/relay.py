"""Локальный релей для сервера в России: тот же протокол, что у внешнего релея (GET /fetch?url=…, заголовок X-Relay-Token,
ответ — тело страницы и заголовок X-Upstream-Status), поэтому collector/relay_get.py и pages.py --relay работают без изменений.
Агент поднимает его на 127.0.0.1 на время сбора, если в окружении не задан внешний RELAY_URL. Капчи и входы не обходит —
это делает и проверяет relay_get.py, как с внешним релеем."""
import hmac, secrets, subprocess, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def fetch(url):
    r = subprocess.run(["curl", "-sS", "-L", "-m", "60", "--max-filesize", "8000000", "-A", UA, "-H", "Accept-Language: ru,en;q=0.8",
                        "-w", "\n__CODE__%{http_code}", url], capture_output=True, timeout=90)
    raw = r.stdout
    i = raw.rfind(b"\n__CODE__")
    return (raw[i + 9:].decode(errors="ignore").strip() or "000", raw[:i]) if i >= 0 else ("000", raw)


def start():
    token = secrets.token_hex(16)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/fetch" or not hmac.compare_digest(self.headers.get("X-Relay-Token", ""), token):
                self.send_response(403); self.end_headers(); self.wfile.write(b"forbidden"); return
            url = (urllib.parse.parse_qs(u.query).get("url") or [""])[0]
            if not url.startswith(("http://", "https://")):
                self.send_response(400); self.end_headers(); return
            code, body = fetch(url)
            self.send_response(200)
            self.send_header("X-Upstream-Status", code if code != "000" else "")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", token, srv
