"""relay_get.py — чтение страниц через российский релей для источников type pages с полем relay (сайты, которые из облака
отвечают 403/503, сбрасывают соединение или не открываются). Отдаёт текст страницы без разметки и ссылки: по нему строки
лотов выбираются тем же промптом, что у pages.

Запуск: RELAY_URL=<адрес релея из задания> RELAY_TOKEN=<токен релея из задания> python3 relay_get.py --urls URL [URL …]
        --pause 3 --out relay_pages.json
Выход: {"pages": [{url, status: ok|failed|blocked, code, note, text, links: [[текст, адрес], …]}]}. blocked — проверка на бота
или страница входа (не обходится); failed с note «узел не разрешён в релее» — адреса нет в RELAY_ALLOW на сервере.
Токен — только из окружения; в выход, лог и базу не пишется.
"""
import argparse, html, json, os, re, subprocess, time, urllib.parse

BOT = re.compile(r"(?i)smartcaptcha\.yandexcloud|showcaptcha|hg-security|ddos-guard|checking your browser|cf-chl|<title>[^<]*(verification|access denied|just a moment|доступ ограничен|проверка браузера|вы робот)")   # слово «captcha» в коде формы — не признак
LOGIN = re.compile(r"(?i)<input[^>]+type=[\"']password")


def fetch(url):
    relay, token = os.environ.get("RELAY_URL", "").rstrip("/"), os.environ.get("RELAY_TOKEN", "")
    u = "".join(c if ord(c) < 128 else urllib.parse.quote(c) for c in url)
    r = subprocess.run(["curl", "-s", "-m", "90", "-H", "X-Relay-Token: " + token, "-G", "--data-urlencode", "url=" + u,
                        "-D", "-", relay + "/fetch"], capture_output=True, text=True, errors="ignore")
    m = re.search(r"\r?\n\r?\n", r.stdout); head, body = (r.stdout[:m.start()], r.stdout[m.end():]) if m else (r.stdout, "")
    while body.startswith("HTTP/"):
        m = re.search(r"\r?\n\r?\n", body); head, body = (head + body[:m.start()], body[m.end():]) if m else (head + body, "")
    codes = re.findall(r"(?m)^HTTP/\S+\s+(\d+)", head)          # перед ответом релея может стоять «200 Connection Established» прокси
    relay_code = codes[-1] if codes else "0"
    up = re.search(r"(?im)^x-upstream-status:\s*(\d+)", head)
    return relay_code, (up.group(1) if up else ""), body


def text_links(page, base):
    links = []
    for h, t in re.findall(r'(?is)<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', page):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()
        if len(t) > 8 and not h.startswith(("mailto:", "tel:", "javascript:")): links.append([t[:200], urllib.parse.urljoin(base, h)])
    s = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page)
    lines = [re.sub(r"\s+", " ", x).strip() for x in html.unescape(re.sub(r"<[^>]+>", "\n", s)).split("\n")]
    return "\n".join(x for x in lines if len(x) > 2)[:60000], links[:400]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--urls", nargs="+", required=True); a.add_argument("--pause", type=float, default=3); a.add_argument("--out", required=True)
    x = a.parse_args()
    if not (os.environ.get("RELAY_URL") and os.environ.get("RELAY_TOKEN")): raise SystemExit("нет RELAY_URL/RELAY_TOKEN в окружении")
    out = []
    for i, url in enumerate(x.urls):
        if i: time.sleep(x.pause)
        rc, code, body = fetch(url)
        p = {"url": url, "code": code or rc}
        if not code and "host not allowed" in body[:200]: p.update(status="failed", note="узел не разрешён в релее (RELAY_ALLOW)")
        elif rc == "403" and not code: p.update(status="failed", note="релей отказал: неверный токен")
        elif rc != "200": p.update(status="failed", note=f"релей ответил {rc}")
        elif code != "200": p.update(status="failed", note=f"сайт ответил {code or 'без ответа'}")
        elif BOT.search(body[:20000]) and len(body) < 30000: p.update(status="blocked", note="проверка на бота — не обходится")
        elif LOGIN.search(body) and len(re.sub(r"<[^>]+>", "", body)) < 4000: p.update(status="blocked", note="страница входа — не обходится")
        else:
            p["text"], p["links"] = text_links(body, url)
            p.update(status="ok" if len(p["text"]) > 200 else "failed", note="" if len(p["text"]) > 200 else "пустая страница (скрипт или SPA)")
        out.append(p)
        print(url, p["status"], p["code"], p.get("note", ""), len(p.get("text", "")))
    json.dump({"pages": out}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
