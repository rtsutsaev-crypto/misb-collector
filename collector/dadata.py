"""dadata.py — названия и реквизиты заказчиков по ИНН через DaData (suggestions API, findById/party).

Бесплатный тариф: 10 000 запросов в сутки. Ключ — только из текста запуска (DADATA_KEY), в базу не записывается.
Запуск: DADATA_KEY=... python3 dadata.py --inns inns.json --out out.json [--parallel 4] [--max 3000]
inns.json: ["7708503727", ...]. out.json: {"<ИНН>": документ для коллекции customers, ...}, stats.
Документ: {inn, name, fullName, opf, status, okved, okvedName, employees, region, address, head, contacts:{phone,email,site},
           branches, registered, checkedAt, via:"dadata"}; не найден — {inn, name:"", checkedAt, via:"dadata", note}.
"""
import argparse, json, os, sys, time, datetime as dt, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ.get("DADATA_KEY", "")
URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


def lookup(inn, today):
    body = json.dumps({"query": inn, "branch_type": "MAIN", "count": 1}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "Authorization": "Token " + KEY})
    for t in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403): return inn, {"error": "ключ не принят: HTTP %d" % e.code}, True
            if e.code == 429: time.sleep(5); continue
            return inn, {"error": "HTTP %d" % e.code}, False
        except Exception as e:
            if t == 3: return inn, {"error": str(e)[:120]}, False
            time.sleep(2)
    s = (j.get("suggestions") or [])
    if not s:
        return inn, {"inn": inn, "name": "", "checkedAt": today, "via": "dadata", "note": "в ЕГРЮЛ/ЕГРИП не найден"}, False
    d = s[0]["data"]; nm = d.get("name") or {}; st = d.get("state") or {}; ad = d.get("address") or {}; adata = ad.get("data") or {}
    mg = d.get("management") or {}
    phones = [p.get("value") for p in (d.get("phones") or []) if isinstance(p, dict) and p.get("value")]
    emails = [e.get("value") for e in (d.get("emails") or []) if isinstance(e, dict) and e.get("value")]
    doc = {"inn": inn, "name": nm.get("short_with_opf") or s[0].get("value") or "", "fullName": nm.get("full_with_opf") or "",
           "opf": (d.get("opf") or {}).get("short") or "", "status": st.get("status") or "", "okved": d.get("okved") or "",
           "okvedName": next((o.get("name") for o in (d.get("okveds") or []) if o.get("main")), "") or "",
           "employees": d.get("employee_count"), "region": adata.get("region_with_type") or "", "address": ad.get("value") or "",
           "head": mg.get("name") or "", "headPost": mg.get("post") or "", "branches": d.get("branch_count"),
           "registered": (dt.datetime.utcfromtimestamp(d["ogrn_date"] / 1000).date().isoformat() if d.get("ogrn_date") else ""),
           "contacts": {k: v for k, v in {"phone": phones[0] if phones else "", "email": emails[0] if emails else "",
                                           "site": (d.get("sites") or [""])[0] if isinstance(d.get("sites"), list) else ""}.items() if v},
           "checkedAt": today, "via": "dadata"}
    if st.get("status") and st["status"] != "ACTIVE": doc["note"] = "статус в ЕГРЮЛ: " + st["status"]
    return inn, doc, False


def main():
    a = argparse.ArgumentParser(); a.add_argument("--inns", required=True); a.add_argument("--out", required=True)
    a.add_argument("--parallel", type=int, default=4); a.add_argument("--max", type=int, default=3000); a.add_argument("--date")
    x = a.parse_args(); today = x.date or dt.date.today().isoformat()
    if not KEY: sys.exit("нет DADATA_KEY")
    inns = [str(i).strip() for i in json.load(open(x.inns, encoding="utf-8")) if str(i).strip().isdigit() and len(str(i).strip()) in (10, 12)]
    inns = list(dict.fromkeys(inns))[: x.max]
    out, stats, stop = {}, {"requested": 0, "found": 0, "notFound": 0, "errors": 0}, False
    with ThreadPoolExecutor(x.parallel) as ex:
        for inn, doc, fatal in ex.map(lambda i: lookup(i, today), inns):
            stats["requested"] += 1
            if "error" in doc:
                stats["errors"] += 1; stats.setdefault("lastError", doc["error"])
                if fatal: stats["stopped"] = doc["error"]; break
                continue
            out[inn] = doc; stats["found" if doc.get("name") else "notFound"] += 1
    json.dump({"customers": out, "stats": stats}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
