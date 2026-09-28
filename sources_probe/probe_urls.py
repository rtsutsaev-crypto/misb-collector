#!/usr/bin/env python3
"""Check whether list pages of procurement sources can be read from the cloud.

For every URL: robots.txt of the host is read first (a disallowed path is not
fetched), then the page is fetched once with a plain user agent. Nothing is
bypassed: a redirect to a captcha or login page is reported as such.

Usage:
  python3 probe_urls.py URLS.json OUT.json [--pause 1.5] [--workers 6]

URLS.json is a list of URLs (strings). OUT.json gets one record per URL:
  status, final, size, robots ("allowed"/"disallowed"/"unknown"), gate ("captcha"/"login"/""),
  proc (hits of procurement words), dates (dd.mm.yyyy hits), train (training words), title.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (compatible; MISB-monitor)"
GATE = re.compile(r"showcaptcha|captcha|are you not a robot|checking your browser|подтвердите, что вы не робот|доступ ограничен", re.I)
LOGIN = re.compile(r"/(login|auth|signin|registration|user/login)(/|\?|$)", re.I)
PROC = re.compile(r"закупк|тендер|конкурс|запрос(?:а)? предложений|процедур|аукцион", re.I)
TRAIN = re.compile(r"обучен|семинар|тренинг|повышени[ея] квалификаци|образовательн|конференц", re.I)
DATE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_for(host: str, scheme: str):
    if host in _robots:
        return _robots[host]
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f"{scheme}://{host}/robots.txt", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(400_000).decode("utf-8", "replace")
        if not body.lstrip().lower().startswith(("user-agent", "#", "sitemap", "disallow", "allow", "crawl")):
            rp = None  # HTML instead of robots.txt: treat as "no rules"
        else:
            rp.parse(body.splitlines())
    except Exception:
        rp = None
    _robots[host] = rp
    return rp


def probe(url: str, pause: float) -> dict:
    u = urllib.parse.urlparse(url)
    rec = {"url": url, "status": 0, "final": "", "size": 0, "robots": "unknown", "gate": "", "proc": 0, "dates": 0, "train": 0, "title": ""}
    rp = robots_for(u.netloc, u.scheme)
    if rp is not None:
        rec["robots"] = "allowed" if rp.can_fetch("*", url) and rp.can_fetch("MISB-monitor", url) else "disallowed"
        if rec["robots"] == "disallowed":
            return rec
    time.sleep(pause)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read(1_500_000)
            rec["status"], rec["final"] = r.status, r.geturl()
    except urllib.error.HTTPError as e:
        rec["status"], rec["final"] = e.code, url
        raw = e.read(200_000) if hasattr(e, "read") else b""
    except Exception as e:  # noqa: BLE001
        rec["status"], rec["title"] = 0, f"{type(e).__name__}: {str(e)[:120]}"
        return rec
    text = raw.decode("utf-8", "replace")
    if text.count("�") > 20:
        text = raw.decode("cp1251", "replace")
    plain = html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", text, flags=re.S | re.I))
    rec["size"] = len(raw)
    rec["proc"], rec["dates"], rec["train"] = len(PROC.findall(plain)), len(DATE.findall(plain)), len(TRAIN.findall(plain))
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    rec["title"] = " ".join(html.unescape(m.group(1)).split())[:120] if m else ""
    if GATE.search(rec["final"]) or GATE.search(plain[:3000]) or GATE.search(rec["title"]):
        rec["gate"] = "captcha"
    elif LOGIN.search(urllib.parse.urlparse(rec["final"]).path) and not LOGIN.search(u.path):
        rec["gate"] = "login"
    return rec


def run(urls: list[str], pause: float, workers: int) -> list[dict]:
    by_host: dict[str, list[str]] = {}
    for x in urls:
        by_host.setdefault(urllib.parse.urlparse(x).netloc, []).append(x)

    def host_job(host: str):
        return [probe(x, pause) for x in by_host[host]]  # one host at a time: the pause is per host

    with ThreadPoolExecutor(workers) as ex:
        out = [r for rs in ex.map(host_job, by_host) for r in rs]
    order = {x: i for i, x in enumerate(urls)}
    return sorted(out, key=lambda r: order[r["url"]])


if __name__ == "__main__":
    args = sys.argv[1:]
    pause = float(args[args.index("--pause") + 1]) if "--pause" in args else 1.5
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 6
    res = run(json.load(open(args[0], encoding="utf-8")), pause, workers)
    json.dump(res, open(args[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in res:
        print(r["status"], r["robots"][:4], r["gate"] or "-", r["proc"], r["dates"], r["train"], r["url"][:90])
