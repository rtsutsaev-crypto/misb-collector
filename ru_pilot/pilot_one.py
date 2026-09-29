#!/usr/bin/env python3
"""Pilot: what opens from a Russian server that the Claude cloud could not read.

For every address in urls.json (93 addresses the cloud failed on: no answer, 403/503, captcha, redirects) the script
  1. reads robots.txt of the site and skips a disallowed path (no workaround),
  2. opens the page ONCE with an honest User-Agent, TLS verification on, one request at a time per host,
  3. classifies the answer: list (procurement list visible) | shell (page opens, no list in it) | gate (captcha or
     "checking your browser") | blocked (401/403/429/451/5xx) | robots | error (no answer),
  4. counts procurement words, dates and lines about training (seminar, course, qualification, conference ...) and keeps
     up to 8 sample lines,
  5. writes result.json and report.md, comparing with what the cloud saw.

No API keys, no logins, no forms. Only the standard library of Python 3.8+.

  python3 probe_ru.py                      # all addresses, about 8-12 minutes
  python3 probe_ru.py --limit 10           # quick trial
  python3 probe_ru.py --only tektorg       # addresses whose name or url contains the text
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import http.cookiejar
import subprocess
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SSL_CTX = None  # set by --cafile: default trust store plus one extra root; verification stays on
EMBEDDED_URLS = "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"  # zlib+base64 of urls.json, filled in by make_one.py for the single-file variant
UA = "misb-collector-pilot/1.0 (research of public procurement lists; single request per page; contact: MISB)"

GATE = re.compile(r"(captcha|капч|recaptcha|hcaptcha|just a moment|checking your browser|проверка браузера|"
                  r"подтвердите, что вы не робот|ddos-guard|qrator|доступ к сайту .{0,20}запрещ|access denied|"
                  r"unknown user-agent)", re.I)
PROC = re.compile(r"(закупк|тендер|конкурс|лот|запрос предложений|запрос котировок|аукцион|процедур|приём заявок|прием заявок)", re.I)
DATE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b")
TRAIN = re.compile(r"(обучен|повышени\w* квалификац|переподготовк|семинар|тренинг|вебинар|мастер-класс|конференц|форум|"
                   r"образовательн|стратегическ\w* сесси|коучинг|наставнич|оценк\w* персонал|ассессмент|деловая игра|"
                   r"консультационн|дополнительн\w* профессиональн|профессиональн\w* подготовк)", re.I)
NOISE = re.compile(r"(поставк|ремонт|аренд|уборк|питани|медицин|лекарств|строительств|монтаж|охран[аы] объект|канцеляр)", re.I)


def fetch(url: str, timeout: int) -> tuple[int, str, str, str]:
    """-> (status, final_url, text, error). status 0 = no answer. Cookies of one request only, TLS verification on."""
    handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    if SSL_CTX is not None:
        handlers.append(urllib.request.HTTPSHandler(context=SSL_CTX))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                                               "Accept-Language": "ru,en;q=0.5", "Accept-Encoding": "gzip"})
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(1_500_000)
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            enc = r.headers.get_content_charset() or "utf-8"
            return r.status, r.geturl(), raw.decode(enc, "replace"), ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read(200_000)
            if e.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            text = body.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return e.code, url, text, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as e:
        return 0, url, "", f"{type(e).__name__}: {str(e)[:120]}"


def robots_allows(url: str, timeout: int) -> tuple[bool, str]:
    u = urllib.parse.urlsplit(url)
    st, _, text, _ = fetch(f"{u.scheme}://{u.netloc}/robots.txt", timeout)
    if st == 0:
        return True, "robots.txt not reachable"
    if st != 200 or re.search(r"<html|<!doctype", text[:300], re.I):
        return True, "no robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    ok = rp.can_fetch("*", url)
    return ok, "robots.txt allows" if ok else "robots.txt disallows"


def strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|td|a|section|article)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"').replace("&#171;", "«").replace("&#187;", "»"))
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def analyse(status: int, text: str, err: str) -> dict:
    out = {"status": status, "error": err, "class": "", "textLength": 0, "procWords": 0, "dates": 0, "trainLines": 0, "samples": []}
    if status == 0:
        out["class"] = "error"
        return out
    if 300 <= status < 400:
        out["class"] = "redirect"
        return out
    if status in (401, 403, 429, 451) or status >= 500 or status == 404:
        out["class"] = "blocked"
        out["gate"] = bool(GATE.search(text[:5000]))
        return out
    body = strip_html(text)
    out["textLength"] = len(body)
    if GATE.search(body[:4000]) and len(body) < 4000:
        out["class"] = "gate"
        return out
    out["procWords"] = len(PROC.findall(body))
    out["dates"] = len(DATE.findall(body))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in body.split("\n")]
    hits = [ln for ln in lines if 25 <= len(ln) <= 400 and TRAIN.search(ln) and not NOISE.search(ln)]
    seen, uniq = set(), []
    for ln in hits:
        k = ln[:80].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(ln)
    out["trainLines"] = len(uniq)
    out["samples"] = uniq[:8]
    out["class"] = "list" if (out["procWords"] >= 5 and out["dates"] >= 3) else "shell"
    return out


def cloud_text(c: dict) -> str:
    st, note = c.get("status"), (c.get("note") or "")
    if c.get("robots") == "disallowed":
        return "robots.txt запрещает"
    if st in ("blocked", "failed"):
        return ("закрыт: " + note.split(": ", 1)[-1])[:46] if note else "закрыт"
    if st == "covered":
        return "закрыт (закупки берём другим путём)"
    if c.get("gate"):
        return f"защита (HTTP {st})"
    return "нет ответа" if not st else f"HTTP {st}"


def browser_step(r: dict, url: str, timeout: int) -> None:
    """Second stage for pages that open but show no list: read with headless Chromium (browser_lists/render_list.cjs)."""
    js = os.path.join(HERE, "..", "browser_lists", "render_list.cjs")
    if not (shutil.which("node") and os.path.exists(js)):
        r["browser"] = "node or render_list.cjs missing"
        return
    tmp = os.path.join(HERE, ".render_tmp.json")
    try:
        subprocess.run(["node", js, url, "--out", tmp], timeout=timeout + 60, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        d = json.load(open(tmp, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        r["browser"] = f"error: {type(e).__name__}"
        return
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    r["browser"] = d.get("status")
    if d.get("status") != "ok":
        if d.get("status") == "gate":
            r["class"] = "gate"
        return
    rows = [x.get("text", "") for x in d.get("rows", [])]
    hits, seen = [], set()
    for ln in rows:
        if TRAIN.search(ln) and not NOISE.search(ln) and ln[:80].lower() not in seen:
            seen.add(ln[:80].lower())
            hits.append(ln)
    r.update({"procWords": d.get("procWords", 0), "dates": d.get("dates", 0), "rows": len(rows),
              "jsonEndpoints": d.get("jsonEndpoints", [])[:5], "trainLines": len(hits), "samples": hits[:8]})
    if r["procWords"] >= 5 and r["dates"] >= 3:
        r["class"] = "list"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default="", help="urls.json (default: urls.json next to the script, else the embedded list)")
    ap.add_argument("--out", default=os.path.join(HERE, "result.json"))
    ap.add_argument("--report", default=os.path.join(HERE, "report.md"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--from-result", default="", help="take the addresses from an earlier result.json (with --classes)")
    ap.add_argument("--classes", default="shell", help="with --from-result: which classes to re-read, comma separated")
    ap.add_argument("--error-contains", default="", help="with --from-result: only rows whose error text contains this")
    ap.add_argument("--cafile", default="", help="extra root certificate (PEM) added to the trust store; verification stays on")
    ap.add_argument("--summary", nargs="+", default=[], help="print a compact summary of result files and exit")
    ap.add_argument("--peek", default="", help="print the first text lines of one page (robots.txt is checked) and exit")
    ap.add_argument("--browser", action="store_true", help="read pages that open without a list with headless Chromium (needs node + playwright)")
    a = ap.parse_args()
    if a.summary:
        return print_files(a.summary)
    if a.cafile:
        global SSL_CTX
        SSL_CTX = ssl.create_default_context()
        SSL_CTX.load_verify_locations(cafile=a.cafile)
    if a.peek:
        ok, why = robots_allows(a.peek, a.timeout)
        print(f"===== {a.peek} ({why}) =====")
        if ok:
            st, _, text, err = fetch(a.peek, a.timeout)
            print(f"HTTP {st} {err}")
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in strip_html(text).split("\n")]
            for ln in [x for x in lines if len(x) >= 30][:45]:
                print("-", ln[:220])
        return 0

    src = a.urls or os.path.join(HERE, "urls.json")
    if a.from_result:
        prev = json.load(open(a.from_result, encoding="utf-8"))["results"]
        want = set(a.classes.split(","))
        urls = [{"name": r["name"], "url": r["url"], "kinds": r["kinds"], "cloud": r["cloud"]} for r in prev if r["class"] in want and a.error_contains in r.get("error", "")]
    elif os.path.exists(src):
        urls = json.load(open(src, encoding="utf-8"))
    elif EMBEDDED_URLS:
        import base64
        import zlib
        urls = json.loads(zlib.decompress(base64.b64decode(EMBEDDED_URLS)).decode("utf-8"))
    else:
        sys.exit("urls.json not found")
    if a.only:
        urls = [u for u in urls if a.only.lower() in (u["name"] + u["url"]).lower()]
    if a.limit:
        urls = urls[: a.limit]
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    env = {"startedAt": started, "python": sys.version.split()[0], "host": socket.gethostname()}
    try:
        st, _, txt, _ = fetch("https://api.ipify.org?format=json", 15)
        env["publicIp"] = json.loads(txt).get("ip") if st == 200 else ""
    except Exception:  # noqa: BLE001
        env["publicIp"] = ""
    results = []
    last_t = 0.0
    for i, u in enumerate(urls, 1):
        gap = a.pause - (time.monotonic() - last_t)
        if gap > 0:
            time.sleep(gap)
        ok, why = robots_allows(u["url"], a.timeout)
        if not ok:
            r = {"status": 0, "error": "", "class": "robots", "textLength": 0, "procWords": 0, "dates": 0, "trainLines": 0, "samples": []}
        else:
            time.sleep(a.pause)
            t0 = time.monotonic()
            status, final, text, err = fetch(u["url"], a.timeout)
            r = analyse(status, text, err)
            r["finalUrl"] = final if final != u["url"] else ""
            r["seconds"] = round(time.monotonic() - t0, 1)
            if a.browser and r["class"] == "shell":
                browser_step(r, u["url"], a.timeout)
        last_t = time.monotonic()
        r.update({"name": u["name"], "url": u["url"], "kinds": u["kinds"], "cloud": u["cloud"], "robots": why})
        results.append(r)
        print(f"[{i}/{len(urls)}] {r['class']:8} {r['status']:>3} train={r['trainLines']:<3} {u['name'][:34]:34} {u['url'][:60]}", flush=True)
    finished = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    json.dump({"env": env, "finishedAt": finished, "results": results}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_report(a.report, env, finished, results)
    print_summary(results)
    print(f"\nDone: {a.out} and {a.report}. Send report.md and result.json back (or copy the text above).")
    return 0


def print_files(paths: list) -> int:
    """Compact text for pasting into the chat: one line per address, plus sample lines of pages with a list."""
    for pth in paths:
        if not os.path.exists(pth):
            continue
        data = json.load(open(pth, encoding="utf-8"))
        res = data["results"]
        print(f"\n===== {os.path.basename(pth)}: {len(res)} адресов =====")
        for r in res:
            err = (r.get("error") or "")[:60]
            extra = f" proc={r.get('procWords', 0)} dates={r.get('dates', 0)} rows={r.get('rows', '-')}" if r["class"] in ("list", "shell") else ""
            print(f"{r['class']:8} {r['status']:>3} train={r['trainLines']:<3}{extra} | {r['name'][:38]} | {r['url'][:70]} | {err}{' | browser=' + str(r['browser']) if r.get('browser') else ''}")
        for r in res:
            if r["class"] == "list" and r["samples"]:
                print(f"-- {r['name']}")
                for sline in r["samples"][:8]:
                    print(f"   {sline[:200]}")
    return 0


def print_summary(results: list) -> None:
    cnt: dict[str, int] = {}
    for r in results:
        cnt[r["class"]] = cnt.get(r["class"], 0) + 1
    print("\n===== ИТОГ =====")
    print("проверено {}: ".format(len(results)) + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])))
    shown = 0
    for r in results:
        for sline in r["samples"][:3]:
            if shown < 40:
                print(f"- {r['name'][:30]}: {sline[:170]}")
                shown += 1
    print("===== КОНЕЦ ИТОГА =====")


def write_report(path: str, env: dict, finished: str, results: list) -> None:
    n = len(results)
    cnt: dict[str, int] = {}
    for r in results:
        cnt[r["class"]] = cnt.get(r["class"], 0) + 1
    readable = [r for r in results if r["class"] in ("list", "shell")]
    lst = [r for r in results if r["class"] == "list"]
    L = ["# Пилот: что открывается с российского сервера", "",
         f"Сервер: {env.get('host')} · IP {env.get('publicIp') or 'не определён'} · начало {env['startedAt']} · конец {finished}", "",
         f"Проверено адресов: **{n}**. Из них: список закупок виден — **{cnt.get('list', 0)}**, страница открылась без списка (оболочка) — "
         f"{cnt.get('shell', 0)}, защита (капча и т. п.) — {cnt.get('gate', 0)}, отказ или ошибка сервера — {cnt.get('blocked', 0)}, "
         f"нет ответа — {cnt.get('error', 0)}, переадресация без результата — {cnt.get('redirect', 0)}, "
         f"robots.txt запрещает — {cnt.get('robots', 0)}.", "",
         f"Открывается (список или оболочка): **{len(readable)}** из {n}. Из облака Claude эти адреса не читались "
         f"(нет ответа, отказ или защита); на «оболочке» страница открывается, но списка закупок в ней нет.", "",
         f"Строк про обучение на первых страницах (список и оболочка): **{sum(r['trainLines'] for r in readable)}**, "
         f"из них там, где виден список: {sum(r['trainLines'] for r in lst)}.", "",
         "| Площадка | Облако | С российского сервера | Про обучение | Класс |", "| --- | --- | --- | --- | --- |"]
    for r in sorted(results, key=lambda x: (x["class"] != "list", x["class"] != "shell", -x["trainLines"], x["name"])):
        now = ("не открывался (robots.txt)" if r["class"] == "robots"
               else f"HTTP {r['status']}" if r["status"] else (r["error"][:40] or "нет ответа"))
        L.append(f"| {r['name'][:40]} | {cloud_text(r['cloud'])} | {now} | {r['trainLines']} | {r['class']} |")
    L += ["", "## Примеры строк про обучение (первая страница)", ""]
    for r in results:
        if r["samples"]:
            L.append(f"**{r['name']}** — {r['url']}")
            L += [f"- {s[:220]}" for s in r["samples"][:5]]
            L.append("")
    open(path, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    raise SystemExit(main())
