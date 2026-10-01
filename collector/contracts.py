"""contracts.py — реестр контрактов 44-ФЗ и 223-ФЗ через ГосПлан: кто покупает обучение и консалтинг и у кого.

Запуск: GOSPLAN_KEY=... python3 contracts.py --dict dictionary.json --from 2026-09-01 --to 2026-09-30 --out contracts_out.json
         [--classes 85.42 85.41 ...] [--parallel 6] [--max-requests 6000]
Классы по умолчанию — okpd2 словаря (обрезанные до 5 знаков). Окна по одному дню (skip API ≤ 1000).
Каждый контракт проходит отбор collector.py (Matcher.classify по предмету и ОКПД2): в выход попадают только профильные.
Выход: {contracts: [...профильные...], buyers: {ИНН: {...}}, suppliers: {ИНН: {...}}, stats}.
Ничего не выдумывает: всё из ответа API. Ключ только из окружения, в выход не пишется.
"""
import argparse, json, os, sys, time, datetime as dt, urllib.request, urllib.parse, urllib.error, threading
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ.get("GOSPLAN_KEY", "")
BASE = "https://v2.gosplan.info"
LOCK = threading.Lock()
ST = {"requests": 0, "errors": 0, "capped": []}


def get(path, params):
    p = dict(params, apikey=KEY)
    url = BASE + path + "?" + urllib.parse.urlencode(p, doseq=True)
    for t in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                with LOCK: ST["requests"] += 1
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            with LOCK: ST["requests"] += 1
            if e.code == 429 or e.code >= 500: time.sleep(2 + 3 * t); continue
            with LOCK: ST["errors"] += 1
            return None
        except Exception:
            time.sleep(2 + 3 * t)
    with LOCK: ST["errors"] += 1
    return None


def day_rows(law, cls, day, maxreq):
    out, skip = [], 0
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    while True:
        if ST["requests"] >= maxreq: break
        rows = get(f"/{law}/contracts", {"classifier": cls, "published_after": day, "published_before": nxt,
                                         "limit": 100, "skip": skip, "sort": "published_at_asc"})
        if not rows: break
        out += rows
        if len(rows) < 100: break
        skip += 100
        if skip > 1000:
            with LOCK: ST["capped"].append(f"{law} {cls} {day}")
            break
    for r in out: r["_law"] = "44-ФЗ" if law == "fz44" else "223-ФЗ"
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dict", required=True); a.add_argument("--from", dest="frm", required=True); a.add_argument("--to", required=True)
    a.add_argument("--classes", nargs="*"); a.add_argument("--parallel", type=int, default=6)
    a.add_argument("--max-requests", type=int, default=6000); a.add_argument("--out", default="contracts_out.json")
    x = a.parse_args()
    if not KEY: sys.exit("нет GOSPLAN_KEY")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import collector
    dic = json.load(open(x.dict, encoding="utf-8")); dic = dic.get("data", dic)
    m = collector.Matcher(dic)
    classes = x.classes or sorted({c[:5] for c in dic.get("okpd2", [])})
    today = dt.date.today()
    d0, d1 = dt.date.fromisoformat(x.frm), min(dt.date.fromisoformat(x.to), today)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    jobs = [(law, c, d) for law in ("fz44", "fz223") for c in classes for d in days]
    raw = {}
    with ThreadPoolExecutor(x.parallel) as ex:
        for rows in ex.map(lambda j: day_rows(*j, x.max_requests), jobs):
            for r in rows: raw[r["reg_num"]] = r                      # один контракт может прийти по двум классам
    contracts, rej = [], 0
    for r in raw.values():
        ok, why, terms = m.classify(r.get("subject") or "", r.get("okpd2") or [])
        if not ok: rej += 1; continue
        contracts.append({"regNum": r["reg_num"], "law": r["_law"], "customerInn": r.get("customer") or "",
                          "suppliers": r.get("suppliers") or [], "subject": (r.get("subject") or "")[:500],
                          "okpd2": r.get("okpd2") or [], "price": r.get("price"), "publishedAt": (r.get("published_at") or "")[:10],
                          "exeStart": r.get("exe_start") or "", "exeEnd": r.get("exe_end") or "",
                          "purchaseNumber": r.get("purchase_number") or "", "direct": not r.get("purchase_number"),
                          "region": r.get("region"), "why": why})
    buyers, sups = {}, {}
    for c in sorted(contracts, key=lambda c: c["publishedAt"]):
        b = buyers.setdefault(c["customerInn"], {"inn": c["customerInn"], "contracts": 0, "direct": 0, "sum": 0.0, "lastDate": "",
                                                 "nextEnd": "", "region": c["region"], "suppliers": {}, "subjects": []})
        b["contracts"] += 1; b["direct"] += c["direct"]; b["sum"] += c["price"] or 0; b["lastDate"] = c["publishedAt"]
        if c["exeEnd"] and c["exeEnd"] >= today.isoformat() and (not b["nextEnd"] or c["exeEnd"] < b["nextEnd"]): b["nextEnd"] = c["exeEnd"]
        for s in c["suppliers"]: b["suppliers"][s] = b["suppliers"].get(s, 0) + 1
        b["subjects"] = (b["subjects"] + [c["subject"][:160]])[-5:]
        for s in c["suppliers"]:
            q = sups.setdefault(s, {"inn": s, "wins": 0, "direct": 0, "sum": 0.0, "lastDate": "", "customers": {}, "topics": []})
            q["wins"] += 1; q["direct"] += c["direct"]; q["sum"] += c["price"] or 0; q["lastDate"] = c["publishedAt"]
            q["customers"][c["customerInn"]] = q["customers"].get(c["customerInn"], 0) + 1
            q["topics"] = (q["topics"] + [c["subject"][:160]])[-5:]
    stats = {"from": x.frm, "to": d1.isoformat(), "classes": classes, "requests": ST["requests"], "errors": ST["errors"],
             "capped": ST["capped"][:20], "raw": len(raw), "matched": len(contracts), "rejected": rej,
             "direct": sum(c["direct"] for c in contracts), "buyers": len(buyers), "suppliers": len(sups)}
    json.dump({"contracts": contracts, "buyers": buyers, "suppliers": sups, "stats": stats},
              open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
