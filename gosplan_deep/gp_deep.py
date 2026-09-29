#!/usr/bin/env python3
"""Deeper passes over the EIS through the GosPlan API (v2.gosplan.info).

The regular collector reads the 50 newest purchases per request. This script
goes deeper with the same API and the same relevance rules as the group
profiles (dictionary formats/topics/OKPD2, exclusions):

  * more pages of the existing requests (`skip`), new OKPD2 classes, subject
    words from the dictionary topics inside the training OKPD2 class,
  * 44-FZ small-volume purchases (max_price_le),
  * requests for quotes / proposals (purchase_type) — demand before a tender,
  * plans: 44-FZ plan positions (/fz44/tenderplans/positions) and 223-FZ
    purchase plans (/fz223/purchaseplans, each plan document lists its items
    with a planned year/quarter/month).

Usage (key only from the environment, never written anywhere):
  GOSPLAN_KEY=... python3 gp_deep.py explore W [--pause 0.3] [--workers 3] [--only NAME_PREFIX]
  GOSPLAN_KEY=... python3 gp_deep.py plans44 W [--pages 6]
  GOSPLAN_KEY=... python3 gp_deep.py plans223 W [--newest 300] [--since ISO]

W is a work dir with the site's database dump (ArtifactData with out_dir):
  W/db/config/dictionary.json, W/db/config/sources-plan.json, W/db/leadsets/*.json
Results: W/out/deep-*.json (leadset documents) and W/out/deep-*-stat.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "b2b_center"))
sys.path.insert(0, os.path.join(HERE, "..", "group_profiles"))
from b2b_core import existing_leads, norm_title  # noqa: E402
from profile_groups import REGIONS, GroupMatcher, load  # noqa: E402

BASE = "https://v2.gosplan.info"
PAGE = 50
TRAINING_CLASS = "85.42"
CLASSES_NEW = ["74.90", "85.59", "70.22", "78.10", "85.60"]
CLASSES_SMALL = ["85.42", "85.41", "82.30", "74.90", "70.22"]
SMALL_PRICE = 600000  # 44-FZ art. 93 items 4 and 5: purchases up to 600 000 RUB
# subject stems from config/dictionary topics (training class 85.42 keeps them on topic)
TOPIC_STEMS = [
    "охран труда", "промышленн безопасн", "электробезопасн", "пожарн", "первой помощ", "бережлив",
    "управлен проект", "цифров", "искусственн интеллект", "бухгалтер", "налог", "мсфо", "казначей",
    "закупк", "контрактн", "переговор", "продаж", "лидерств", "мотивац", "стратег", "кадров",
    "аттестац", "наставнич", "комплаенс", "антикоррупц", "гражданск оборон", "эколог",
    "энергосбережен", "энергоэффективн", "английск", "делопроизводств", "архив", "сметн", "трудов",
    "персональн данн", "воинск учет", "управлен персонал", "госслужащ", "муниципальн служащ",
    "педагог", "экономи", "финанс", "юрид", "договорн", "земельн", "кадастр", "логистик",
]
QUOTE_TYPES = {  # purchase_type values that are requests for quotes / proposals
    "epNotificationEZK": "запрос котировок в электронной форме",
    "epNotificationEZK2020": "запрос котировок в электронной форме",
    "purchaseNoticeZK": "запрос котировок",
    "purchaseNoticeZKESMBO": "запрос котировок (СМП)",
    "purchaseNoticeZPESMBO": "запрос предложений (СМП)",
    "epNotificationEZP": "запрос предложений в электронной форме",
}
QUOTE_WORDS = ["обучение", "семинар", "тренинг", "повышение квалификации", "конференц", "консультационные"]


class Limiter:
    def __init__(self, per_sec: float):
        self.gap, self.next, self.lock = 1.0 / per_sec, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


def key() -> str:
    k = os.environ.get("GOSPLAN_KEY")
    if not k:
        sys.exit("GOSPLAN_KEY is not set")
    return k


def get(path: str, params: dict, lim: Limiter, tries: int = 5):
    q = dict(params)
    q.setdefault("limit", PAGE)
    q["apikey"] = key()
    url = BASE + path + "?" + urllib.parse.urlencode(q, doseq=True)
    err = ""
    for a in range(tries):
        lim.wait()
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code == 429:
                time.sleep(2 + a * 2)
            elif e.code in (400, 404, 422):
                return e.code, None
            else:
                time.sleep(2 + a)
        except Exception as e:  # noqa: BLE001 - connection resets happen
            err = type(e).__name__
            time.sleep(2 + a)
    return err, None


def region_name(code) -> str:
    return REGIONS.get(int(code), "") if str(code or "").isdigit() else ""


def lead_of(row: dict, law: str, today: str, note: str) -> dict:
    """Same lead the collector builds from a GosPlan purchase row."""
    num = str(row.get("purchase_number") or "")
    title = " ".join(str(row.get("object_info") or "").split())
    if law == "44":
        inn = (row.get("customers") or [""])[0]
        close = str(row.get("collecting_finished_at") or "")[:10]
    else:
        inn = row.get("customer") or ""
        close = str(row.get("submission_close_at") or "")[:10]
    lead = {"collectedAt": today, "customer": f"ИНН {inn}" if inn else "", "customerInn": str(inn),
            "deadline": close, "flags": [], "id": num, "law": f"{law}-ФЗ", "price": row.get("max_price"),
            "region": region_name(row.get("region")), "source": f"gosplan:fz{law}", "title": title,
            "url": f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={num}",
            "country": "RU", "currency": "RUB"}
    if note:
        lead["note"] = note
    return lead


def spec_explore(W: str) -> list[dict]:
    plan = load(os.path.join(W, "db", "config", "sources-plan.json"), {}) or {}
    have = next((s for s in plan.get("sources", []) if s.get("key") == "gosplan"), {}).get("requests", [])
    out = []
    for r in have:  # E1: pages 2 and 3 of what the collector already asks for
        p = {k: v for k, v in r.items() if k not in ("law", "path")}
        for n in (1, 2):
            out.append({"name": f"E1 {r['law']} {sorted(p.items())} p{n + 1}", "law": r["law"], "path": r["path"],
                        "params": {**p, "skip": PAGE * n}})
    for law in ("44", "223"):
        path = f"/fz{law}/purchases"
        for c in CLASSES_NEW:  # E2: OKPD2 classes the collector does not ask for yet
            for n in (0, 1):
                out.append({"name": f"E2 {law} {c} p{n + 1}", "law": law, "path": path,
                            "params": {"classifier": c, "skip": PAGE * n}})
        for w in TOPIC_STEMS:  # E3: dictionary subjects inside the training class
            out.append({"name": f"E3 {law} {TRAINING_CLASS}+{w}", "law": law, "path": path,
                        "params": {"classifier": TRAINING_CLASS, "object_info": w}})
    for c in CLASSES_SMALL:  # E4: 44-FZ small volume
        for n in (0, 1):
            out.append({"name": f"E4 44 {c} <=600k p{n + 1}", "law": "44", "path": "/fz44/purchases",
                        "params": {"classifier": c, "max_price_le": SMALL_PRICE, "skip": PAGE * n}})
    for law in ("44", "223"):  # E5: requests for quotes / proposals
        for t in QUOTE_TYPES:
            for w in QUOTE_WORDS:
                out.append({"name": f"E5 {law} {t}+{w}", "law": law, "path": f"/fz{law}/purchases",
                            "params": {"purchase_type": t, "object_info": w}})
    return out


def explore(W: str, pause: float, workers: int, only: str | None) -> None:
    today = dt.date.today().isoformat()
    cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    M = GroupMatcher(load(os.path.join(W, "db", "config", "dictionary.json"), {}))
    ids, pairs = existing_leads(W)
    spec = [s for s in spec_explore(W) if not only or s["name"].startswith(only)]
    lim = Limiter(1.0 / max(pause, 0.05))
    results = {}

    def one(i):
        s = spec[i]
        st, rows = get(s["path"], s["params"], lim)
        results[i] = (st, rows or [])

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, range(len(spec))))
    leads, table, seen = [], [], set()
    for i, s in enumerate(spec):
        st, rows = results[i]
        rel = new = new_open = 0
        for r in rows:
            title = " ".join(str(r.get("object_info") or "").split())
            terms, ok = M.terms(title, r.get("okpd2"))
            if not ok:
                continue
            rel += 1
            lead = lead_of(r, s["law"], today, f"ГосПлан, углублённый проход: {s['name']}; совпало: {', '.join(terms[:3])}")
            num = lead["id"]
            if lead["deadline"] and lead["deadline"] < cutoff:
                continue
            if not num or num in ids or num in seen or (norm_title(title), lead["deadline"]) in pairs:
                continue
            seen.add(num)
            new += 1
            new_open += bool(lead["deadline"] and lead["deadline"] >= today)
            leads.append(lead)
        table.append({"name": s["name"], "status": st, "rows": len(rows), "relevant": rel, "new": new, "new_open": new_open})
    os.makedirs(os.path.join(W, "out"), exist_ok=True)
    json.dump({"source": "ГосПлан: углублённый проход", "collectedAt": today, "leads": leads},
              open(os.path.join(W, "out", "deep-purchases.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(table, open(os.path.join(W, "out", "deep-purchases-stat.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by = {}
    for t in table:
        g = t["name"].split(" ")[0]
        a = by.setdefault(g, [0, 0, 0, 0, 0])
        a[0] += 1; a[1] += t["rows"]; a[2] += t["relevant"]; a[3] += t["new"]; a[4] += t["new_open"]
    print("group: requests, rows, relevant, new, new_open")
    for g, a in sorted(by.items()):
        print(g, a)
    print("total new leads:", len(leads), "open:", sum(a[4] for a in by.values()))


# ---------- plans ----------
def plans44(W: str, pages: int) -> None:
    """44-FZ plan positions of the training classes for future years."""
    today = dt.date.today()
    M = GroupMatcher(load(os.path.join(W, "db", "config", "dictionary.json"), {}))
    ids, pairs = existing_leads(W)
    lim = Limiter(3)
    leads, stat = [], {"rows": 0, "future": 0, "relevant": 0, "new": 0}
    seen = set()
    for c in ["85.42", "85.41", "82.30", "74.90", "70.22", "78.10"]:
        for n in range(pages):
            st, rows = get("/fz44/tenderplans/positions", {"classifier": c, "skip": PAGE * n}, lim)
            for r in rows or []:
                stat["rows"] += 1
                ci = (r.get("source") or {}).get("commonInfo") or {}
                year = int(ci.get("publishYear") or 0)
                if year <= today.year:
                    continue
                stat["future"] += 1
                pos = str(ci.get("positionNumber") or "")
                title = " ".join(str(ci.get("purchaseObjectInfo") or "").split())
                okpd = (ci.get("OKPD2Info") or {}).get("OKPDCode")
                terms, ok = M.terms(title, [okpd] if okpd else [])
                if not ok:
                    continue
                stat["relevant"] += 1
                lid = "plan44-" + pos
                if pos in seen or lid in ids:
                    continue
                seen.add(pos)
                fin = ((r.get("source") or {}).get("financeInfo") or {}).get("total")
                try:
                    price = float(fin) if fin not in (None, "") and float(fin) > 0 else None
                except ValueError:
                    price = None
                stat["new"] += 1
                leads.append({"collectedAt": today.isoformat(), "customer": f"ИНН {r.get('customer')}", "customerInn": str(r.get("customer") or ""),
                              "deadline": "", "flags": ["plan"], "id": lid, "law": "44-ФЗ", "price": price,
                              "region": region_name(r.get("region")), "source": "gosplan:plan44", "title": title,
                              "url": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=" + str(ci.get("IKZ") or pos),
                              "note": f"План закупок 44-ФЗ на {year} год, позиция {pos}; совпало: {', '.join(terms[:3])}",
                              "country": "RU", "currency": "RUB"})
            if not rows or len(rows) < PAGE:
                break
    os.makedirs(os.path.join(W, "out"), exist_ok=True)
    json.dump({"source": "ГосПлан: планы закупок 44-ФЗ", "collectedAt": today.isoformat(), "leads": leads},
              open(os.path.join(W, "out", "deep-plan44.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(stat, ensure_ascii=False))


def plans223(W: str, newest: int, since: str | None) -> None:
    """223-FZ purchase plans: every plan document lists its items with a planned month."""
    today = dt.date.today()
    M = GroupMatcher(load(os.path.join(W, "db", "config", "dictionary.json"), {}))
    ids, pairs = existing_leads(W)
    lim = Limiter(3)
    metas, skip = [], 0
    while skip < newest:
        st, rows = get("/fz223/purchaseplans", {"skip": skip, "limit": PAGE}, lim)
        if not rows:
            break
        metas += rows
        skip += PAGE
    stat = {"plans": 0, "items": 0, "relevant": 0, "new": 0, "errors": 0, "lastUpdated": ""}
    leads, seen = [], set()
    for m in metas:
        upd = str(m.get("doc_updated_at") or "")
        if since and upd <= since:
            continue
        st, doc = get(f"/fz223/purchaseplans/{m['registration_number']}", {}, lim)
        if not doc:
            stat["errors"] += 1
            continue
        stat["plans"] += 1
        stat["lastUpdated"] = max(stat["lastUpdated"], upd)
        for d in doc.get("docs") or []:
            src = d.get("source") or {}
            items = ((src.get("purchasePlanItems") or {}).get("purchasePlanItem")) or []
            if isinstance(items, dict):
                items = [items]
            for it in items:
                stat["items"] += 1
                title = " ".join(str(it.get("contractSubject") or "").split())
                rows = (((it.get("purchasePlanDataItemRows") or {}).get("purchasePlanRowItem")))
                rows = [rows] if isinstance(rows, dict) else rows or []
                okpd = [str((x.get("okpd2") or x.get("OKPD2") or {}).get("code") or "") for x in rows if isinstance(x, dict)]
                terms, ok = M.terms(title, [c for c in okpd if c])
                if not ok:
                    continue
                stat["relevant"] += 1
                year, month = it.get("purchasePeriodYear"), it.get("purchasePeriodMonth")
                planned = f"{int(year):04d}-{int(month):02d}-01" if str(year).isdigit() and str(month).isdigit() else ""
                if planned and planned < today.replace(day=1).isoformat():
                    continue  # the planned month has passed: the purchase is announced or dropped
                pn = str(m["registration_number"])
                lid = f"plan223-{pn}-{it.get('ordinalNumber')}"
                if lid in ids or lid in seen:
                    continue
                seen.add(lid)
                try:
                    price = float(it.get("maximumContractPrice")) or None
                except (TypeError, ValueError):
                    price = None
                stat["new"] += 1
                leads.append({"collectedAt": today.isoformat(), "customer": f"ИНН {m.get('customer')}", "customerInn": str(m.get("customer") or ""),
                              "deadline": planned, "flags": ["plan"], "id": lid, "law": "223-ФЗ", "price": price,
                              "region": region_name(m.get("region")), "source": "gosplan:plan223", "title": title,
                              "url": f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={pn}",
                              "note": f"План закупок 223-ФЗ {pn}, позиция {it.get('ordinalNumber')}: планируется {planned[:7] or 'без даты'}; {str(it.get('purchaseMethodName') or '')[:80]}; совпало: {', '.join(terms[:3])}",
                              "country": "RU", "currency": "RUB"})
    os.makedirs(os.path.join(W, "out"), exist_ok=True)
    json.dump({"source": "ГосПлан: планы закупок 223-ФЗ", "collectedAt": today.isoformat(), "leads": leads},
              open(os.path.join(W, "out", "deep-plan223.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(stat, ensure_ascii=False))


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k, d, f=str: f(a[a.index(k) + 1]) if k in a else d  # noqa: E731
    if not a or a[0] not in ("explore", "plans44", "plans223"):
        sys.exit(__doc__)
    if a[0] == "explore":
        explore(a[1], opt("--pause", 0.3, float), opt("--workers", 3, int), opt("--only", None))
    elif a[0] == "plans44":
        plans44(a[1], opt("--pages", 6, int))
    else:
        plans223(a[1], opt("--newest", 300, int), opt("--since", None))
