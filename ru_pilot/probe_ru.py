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
EMBEDDED_URLS = ""  # zlib+base64 of urls.json, filled in by make_one.py for the single-file variant
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
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
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
    ap.add_argument("--browser", action="store_true", help="read pages that open without a list with headless Chromium (needs node + playwright)")
    a = ap.parse_args()

    src = a.urls or os.path.join(HERE, "urls.json")
    if a.from_result:
        prev = json.load(open(a.from_result, encoding="utf-8"))["results"]
        want = set(a.classes.split(","))
        urls = [{"name": r["name"], "url": r["url"], "kinds": r["kinds"], "cloud": r["cloud"]} for r in prev if r["class"] in want]
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
