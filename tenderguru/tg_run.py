#!/usr/bin/env python3
"""TenderGuru API v2.3 queries from tenderguru/queries.json -> new leads.

Usage:
  TENDERGURU_KEY=... python3 tg_run.py W [--part extra|etp|all] [--start N] [--count N] [--pause 3]

W is a work dir holding the site's database dump (ArtifactData out_dir):
  W/db/config/dictionary.json, W/db/leadsets/*.json.
Writes W/out/tenderguru-<part>.json (one leadset doc) and W/out/tenderguru-<part>-stat.json.

The key is read from the environment only. The API tariff returns the 10 newest
cards per query (pages beyond the first are «access denied»), hides the customer,
the platform and the official number — so leads carry only what the answer has;
the platform is taken from the filter that was sent (codes checked 28.09.2026).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "b2b_center"))
sys.path.insert(0, os.path.join(HERE, "..", "group_profiles"))
from b2b_core import existing_leads, norm_title  # noqa: E402
from profile_groups import GroupMatcher, load  # noqa: E402

API = "https://www.tenderguru.ru/api2.3/export"
QUERIES = os.path.join(HERE, "queries.json")


def ddmmyyyy(v: str) -> str:
    p = str(v or "").split("-")
    return f"{p[2][:4]}-{p[1]}-{p[0]}" if len(p) == 3 and len(p[0]) == 2 else ""


def call(params: dict, key: str) -> tuple[str, list[dict]]:
    q = {"dtype": "json", "tenpage": "10", "sort_by": "by_date", "sort_dest": "desc", **params, "api_code": key}
    for attempt in range(3):
        try:
            with urllib.request.urlopen(API + "?" + urllib.parse.urlencode(q), timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == 2:
                return f"ошибка: {type(e).__name__}", []
            time.sleep(3 * (attempt + 1))
    if data and isinstance(data[0], dict) and data[0].get("access") == "denied":
        return "denied", []
    return "ok", [x for x in data if isinstance(x, dict) and "ID" in x]


def requests_of(part: str) -> list[dict]:
    doc = json.load(open(QUERIES, encoding="utf-8"))
    out = []
    if part in ("extra", "all"):
        out += [{"kwords": q} for q in doc["extra"]]
    if part in ("etp", "all"):
        out += doc["etp"]
    return out


def run(W: str, part: str, start: int, count: int | None, pause: float, key: str) -> dict:
    today = dt.date.today()
    M = GroupMatcher(load(os.path.join(W, "db", "config", "dictionary.json"), {}))
    ids, pairs = existing_leads(W)
    reqs = requests_of(part)
    reqs = reqs[start:start + count] if count else reqs[start:]
    leads, stat = [], {"requests": 0, "rows": 0, "не по теме": 0, "давно закрыты": 0, "уже в базе": 0, "errors": {}}
    for r in reqs:
        params = {k: v for k, v in r.items() if k != "platform"}
        status, rows = call(params, key)
        stat["requests"] += 1
        if status != "ok":
            stat["errors"][status] = stat["errors"].get(status, 0) + 1
        time.sleep(pause)
        for x in rows:
            stat["rows"] += 1
            title = " ".join(html.unescape(str(x.get("TenderName") or "")).split())
            _, ok = M.terms(title)
            end = ddmmyyyy(x.get("EndTime"))
            if not ok:
                stat["не по теме"] += 1
            elif end and end < (today - dt.timedelta(days=30)).isoformat():
                stat["давно закрыты"] += 1
            elif str(x["ID"]) in ids or (norm_title(title), end) in pairs:
                stat["уже в базе"] += 1
            else:
                ids.add(str(x["ID"]))
                pairs[(norm_title(title), end)] = str(x["ID"])
                price = str(x.get("Price") or "").replace(" ", "").replace(",", ".")
                lead = {"id": str(x["ID"]), "title": title, "customer": "", "region": str(x.get("Region") or ""),
                        "price": float(price) if price.replace(".", "", 1).isdigit() and float(price) > 0 else None,
                        "deadline": end, "law": "", "url": str(x.get("TenderLinkInner") or f"https://www.tenderguru.ru/tender/{x['ID']}"),
                        "source": "tenderguru:etp" if r.get("platform") else "tenderguru", "collectedAt": today.isoformat(), "flags": []}
                if r.get("platform"):
                    lead["platform"] = r["platform"]
                    lead["note"] = f"TenderGuru, фильтр площадки: {r['platform']}; запрос «{r['kwords']}»"
                else:
                    lead["note"] = f"TenderGuru, запрос «{r['kwords']}»"
                leads.append(lead)
    stat["new"] = len(leads)
    stat["open"] = sum(1 for l in leads if l["deadline"] >= today.isoformat())
    os.makedirs(os.path.join(W, "out"), exist_ok=True)
    json.dump({"source": "TenderGuru: расширенный набор запросов", "collectedAt": today.isoformat(), "leads": leads},
              open(os.path.join(W, "out", f"tenderguru-{part}.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(stat, open(os.path.join(W, "out", f"tenderguru-{part}-stat.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return stat


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k, d, f=str: f(a[a.index(k) + 1]) if k in a else d  # noqa: E731
    key = os.environ.get("TENDERGURU_KEY")
    if not key:
        sys.exit("TENDERGURU_KEY is not set")
    print(json.dumps(run(a[0], opt("--part", "all"), opt("--start", 0, int), opt("--count", None, int), opt("--pause", 3.0, float), key), ensure_ascii=False))
