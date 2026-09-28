#!/usr/bin/env python3
"""Tender cards from energybase.ru list pages (catalog «Подготовка персонала»
and the same section of big energy groups).

The regular collector reads these pages itself (plan source `energybase`,
type pages, via WebFetch). This script is the same parse for a pass from a
Claude session: robots.txt allows the list pages and /tender/<id> cards, but
not URLs with «?», /search or /tender-outdated, and asks ClaudeBot for 6 s
between requests — so only the first page of each list is read.

Usage:
  python3 energybase_pages.py fetch DIR          # saves the pages of URLS into DIR (6 s apart)
  python3 energybase_pages.py parse DIR W        # W/db/leadsets/*.json — current leads;
                                                 # writes W/out/energybase.json (new relevant leads)
"""
from __future__ import annotations

import datetime as dt
import glob
import html
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "b2b_center"))
sys.path.insert(0, os.path.join(HERE, "..", "group_profiles"))
from b2b_core import existing_leads, norm_title  # noqa: E402
from profile_groups import GroupMatcher, load  # noqa: E402

BASE = "https://energybase.ru"
SECTION = "tenders/training-services"
COMPANIES = ["integrated/gazprom-neft", "integrated/rosneft", "integrated/lukoil", "integrated/gazprom",
             "integrated/tatneft", "midstream/transneft", "generation/rosatom", "generation/rushydro",
             "petrochemistry/sibur"]
URLS = [f"{BASE}/tender/catalog/training-services"] + [f"{BASE}/{c}/{SECTION}" for c in COMPANIES]
MONTHS = {m: i + 1 for i, m in enumerate(["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                                          "августа", "сентября", "октября", "ноября", "декабря"])}
FOREIGN = re.compile(r"(^|[^А-Яа-яЁё])ТОО([^А-Яа-яЁё]|$)|Казахстан|Беларус")


def ru_date(text: str) -> str:
    m = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", text or "")
    return f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}" if m and m.group(2) in MONTHS else ""


def cards(page: str) -> list[dict]:
    out = []
    for c in page.split('<div data-key="')[1:]:
        c = c.split('<div data-key="')[0]
        u = re.search(r'init-url="https://energybase\.ru/(tender(?:-outdated)?)/([0-9a-f-]{36})"', c)
        t = re.search(r'tender-card__title">\s*<a[^>]*>(.*?)</a>', c, re.S)
        if not (u and t):
            continue
        cust = re.search(r'Заказчик</span>\s*(?:<a[^>]*>)?([^<]+)', c)  # a link, or plain text for companies without a page
        close = re.search(r'окончания подачи заявок</span>\s*([^<]+)', c)
        pub = re.search(r'Дата объявления</span>\s*([^<]+)', c)
        price = re.search(r'tender-card__sum">([^<]*)<', c)
        kind = re.search(r'</div>\s*</div>\s*</div>\s*<div class="tender-card__row">([^<]+)</div>', c)
        amount = re.sub(r"[^\d,]", "", html.unescape(price.group(1))).replace(",", ".") if price else ""
        out.append({"id": u.group(2), "outdated": u.group(1) == "tender-outdated",
                    "title": " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t.group(1))).split()),
                    "customer": " ".join(html.unescape(cust.group(1)).split()) if cust else "",
                    "deadline": ru_date(close.group(1)) if close else "", "published": ru_date(pub.group(1)) if pub else "",
                    "price": float(amount) if amount else None, "kind": html.unescape(kind.group(1)).strip() if kind else ""})
    return out


def fetch(d: str) -> None:
    os.makedirs(d, exist_ok=True)
    for i, url in enumerate(URLS):
        if i:
            time.sleep(6)
        path = os.path.join(d, re.sub(r"\W+", "_", url.split("energybase.ru/")[1]) + ".html")
        code = subprocess.run(["curl", "-sS", "-m", "40", "-A", "Mozilla/5.0 (compatible; MISB-monitor)", "-o", path,
                               "-w", "%{http_code}", url], capture_output=True, text=True).stdout
        print(code, url)


def parse(d: str, W: str) -> dict:
    today = dt.date.today().isoformat()
    M = GroupMatcher(load(os.path.join(W, "db", "config", "dictionary.json"), {}))
    ids, pairs = existing_leads(W)
    seen, leads, stat = set(), [], {"cards": 0, "не по теме": 0, "не РФ": 0, "уже в базе": 0}
    for path in sorted(glob.glob(os.path.join(d, "*.html"))):
        for c in cards(open(path, encoding="utf-8").read()):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            stat["cards"] += 1
            terms, ok = M.terms(c["title"])
            if FOREIGN.search(c["customer"]):
                stat["не РФ"] += 1
            elif not ok:
                stat["не по теме"] += 1
            elif c["id"] in ids or (norm_title(c["title"]), c["deadline"]) in pairs:
                stat["уже в базе"] += 1
            else:
                leads.append({"id": c["id"], "title": c["title"], "customer": c["customer"], "region": "",
                              "price": c["price"], "deadline": c["deadline"], "law": "", "url": f"{BASE}/tender/{c['id']}",
                              "source": "energybase", "collectedAt": today, "flags": [],
                              "note": f"Energybase, раздел «Подготовка персонала»{'; ' + c['kind'] if c['kind'] else ''}; объявлено {c['published'] or '—'}; совпало: {', '.join(terms[:3])}"})
    stat["new"] = len(leads)
    stat["open"] = sum(1 for l in leads if l["deadline"] >= today)
    os.makedirs(os.path.join(W, "out"), exist_ok=True)
    json.dump({"source": "Energybase: первый проход", "collectedAt": today, "leads": leads},
              open(os.path.join(W, "out", "energybase.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return stat


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif sys.argv[1] == "parse":
        print(json.dumps(parse(sys.argv[2], sys.argv[3]), ensure_ascii=False))
    else:
        sys.exit(__doc__)
