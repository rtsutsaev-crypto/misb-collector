"""results.py — итоги закрытых закупок 44-ФЗ: кто заключил контракт и на какую сумму (ГосПлан, /fz44/contracts?purchase_number=).

Запуск: GOSPLAN_KEY=... python3 results.py --leadsets <папка leadsets> [--known <папка leadresults>] --dict dictionary.json --date ГГГГ-ММ-ДД --out results_out.json [--max 60] [--min-age 14] [--max-age 120]
Берёт профильные лиды 44-ФЗ (19-значный номер) со сроком подачи от сегодня−max-age до сегодня−min-age дней, по которым итога ещё нет (--known), самые свежие первыми, не больше --max запросов.
Выход: {"docs": [{id, key, contractPrice, nmck, supplier, regNum, signedAt, checkedAt}], "stats": {...}}; документ — в коллекцию leadresults (doc_id = key, как у лида на сайте).
Лид без контракта (закупка не состоялась или контракт ещё не опубликован) получает документ {"none": true, checkedAt}: повторно такой лид проверяется не раньше чем через 14 дней.
Ключ только из окружения GOSPLAN_KEY, в выход не пишется.
"""
import argparse, datetime as dt, glob, json, os, re, time, urllib.error, urllib.parse, urllib.request

import collector as C

KEY = os.environ.get("GOSPLAN_KEY", "")
BASE = "https://v2.gosplan.info"


def get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(dict(params, apikey=KEY))
    for t in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500: time.sleep(2 + 3 * t); continue
            return None
        except Exception:
            time.sleep(2)
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leadsets", required=True); a.add_argument("--known"); a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--max", type=int, default=60); a.add_argument("--min-age", type=int, default=14); a.add_argument("--max-age", type=int, default=120)
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    lo, hi = (today - dt.timedelta(days=x.max_age)).isoformat(), (today - dt.timedelta(days=x.min_age)).isoformat()
    m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
    known = {}
    if x.known and os.path.isdir(x.known):
        for f in glob.glob(os.path.join(x.known, "**", "*.json"), recursive=True):
            j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
            known[os.path.basename(f)[:-5]] = j
    cand, seen = [], set()
    for f in glob.glob(os.path.join(x.leadsets, "*.json")):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []):
            i = str(l.get("id"))
            if i in seen or not re.fullmatch(r"0\d{18}", i) or not (lo <= (l.get("deadline") or "") <= hi): continue
            seen.add(i)
            k = C.site_key(i); old = known.get(k)
            if old and (not old.get("none") or (today - dt.date.fromisoformat(old.get("checkedAt", "2000-01-01"))).days < 14): continue
            if m.classify(l.get("title", ""), l.get("okpd2") or ())[0]: cand.append((l["deadline"], i, l))
    cand.sort(reverse=True)
    docs, st = [], {"candidates": len(cand), "requests": 0, "found": 0, "none": 0}
    for _, i, l in cand[: x.max]:
        j = get("/fz44/contracts", {"purchase_number": i, "limit": 5}); st["requests"] += 1
        time.sleep(0.15)
        if j is None: continue
        k = C.site_key(i)
        if isinstance(j, list) and j:
            c = sorted(j, key=lambda r: r.get("published_at") or "")[0]
            docs.append({"id": i, "key": k, "contractPrice": float(c["price"]) if c.get("price") not in (None, "") else None, "nmck": l.get("price"),
                         "supplier": (c.get("suppliers") or [""])[0], "regNum": c.get("reg_num") or "", "signedAt": (c.get("published_at") or "")[:10], "checkedAt": x.date})
            st["found"] += 1
        else:
            docs.append({"id": i, "key": k, "none": True, "checkedAt": x.date}); st["none"] += 1
    json.dump({"docs": docs, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
