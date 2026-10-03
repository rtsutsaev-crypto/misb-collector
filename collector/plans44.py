"""plans44.py — позиции планов-графиков 44-ФЗ (ГосПлан /fz44/tenderplans/positions): закупка видна в плане
до объявления. Два режима:
  delta     — новейшие позиции по кодам ОКПД2 словаря (sort published_at_desc, до --pages страниц по 100 на код);
              позиция попадает сюда, когда заказчик публикует или меняет её в плане. Запускать ежедневно.
  customers — позиции заказчиков из --leadsets (ИНН у релевантных лидов 44-ФЗ) порциями --batch по кругу от --start;
              добирает старые позиции тех, кто уже покупал. Раз в запуск, курсор — в stats.next.
Отбор по теме делает collector.py rows (source gosplan:plan44); здесь только разбор и склейка строк.
Запуск: GOSPLAN_KEY=... python3 plans44.py --mode delta --dict dictionary.json --date ГГГГ-ММ-ДД --out rows.json
        GOSPLAN_KEY=... python3 plans44.py --mode customers --leadsets <папка leadsets> --start N --batch 60 --out rows.json
Ключ — только из окружения. Дальше: python3 collector.py rows --in rows.json --known known.json --source gosplan:plan44 --date ...
"""
import argparse, glob, json, os, subprocess, sys, time, urllib.parse, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gosplan_delta as G

KEY = os.environ.get("GOSPLAN_KEY", "")
PATH = "/fz44/tenderplans/positions"
DEFAULT_CLASSES = ["85.42", "85.41.9", "85.59.19"]   # коды словаря: ДПО, дополнительное образование прочее, прочее образование
MAX_SKIP = 1000                                        # ГосПлан: skip ≤ 1000, limit ≤ 100


def fetch(params, stats):
    G.KEY = KEY
    j = G.get(PATH, params, stats)
    return j if isinstance(j, list) else []


def to_row(x, today, min_price=0):
    s = x.get("source") or {}
    c = s.get("commonInfo") or {}
    if str(c.get("positionCanceled")) == "true": return None
    year = int(c.get("publishYear") or 0)
    if not int(today[:4]) <= year <= int(today[:4]) + 1: return None   # прошлые годы — исполнено; дальше следующего года — многолетние дубли
    fin = s.get("financeInfo") or {}
    try: price = float(fin.get("total") or 0)
    except ValueError: price = 0
    if price < min_price: return None
    inn = x.get("customer") or ""
    ikz = c.get("IKZ") or c.get("positionNumber")
    if not ikz or not c.get("purchaseObjectInfo"): return None
    # месяц в позициях плана-графика не отдаётся: текущий год — конец года, будущий — начало года
    deadline = f"{year}-12-31" if year == int(today[:4]) else f"{year}-01-15"
    okpd = (c.get("OKPD2Info") or {}).get("OKPDCode") or ""
    return {"id": "plan44-" + ikz, "title": c["purchaseObjectInfo"].strip(), "customer": ("ИНН " + inn) if inn else "",
            "customerInn": inn, "region": G.REG.get(int(x.get("region") or 0), ""), "price": price if price > 0 else None,
            "deadline": deadline, "law": "44-ФЗ", "okpd2": [okpd] if okpd else [], "flags": ["plan"],
            "url": G.EIS + (c.get("positionNumber") or ""), "publishedAt": (c.get("publishDate") or "")[:10],
            "note": f"План-график 44-ФЗ {x.get('plan_number')}, позиция {c.get('positionNumber')}: закупка на {year} год, срок объявления в плане не указан"}


def customers_from(leadsets):
    """ИНН заказчиков релевантных лидов 44-ФЗ, свежие лиды первыми."""
    last = {}
    for f in sorted(glob.glob(os.path.join(leadsets, "*.json"))):
        d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
        for l in d.get("leads", []):
            inn = str(l.get("customerInn") or "")
            if not inn.isdigit() or l.get("law") != "44-ФЗ": continue
            last[inn] = max(last.get(inn, ""), l.get("deadline") or "")
    return [i for i, _ in sorted(last.items(), key=lambda t: (-int(t[1].replace("-", "") or 0), t[0]))]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--mode", choices=["delta", "customers"], default="delta")
    a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--date", default=dt.date.today().isoformat())
    a.add_argument("--leadsets"); a.add_argument("--start", type=int, default=0); a.add_argument("--batch", type=int, default=60)
    a.add_argument("--pages", type=int, default=11, help="страниц по 100 на код ОКПД2 в режиме delta")
    a.add_argument("--max-requests", type=int, default=120)
    a.add_argument("--min-price", type=float, default=100000, help="позиции дешевле — прямые закупки без торгов, не берём")
    a.add_argument("--pause", type=float, default=0.3)
    a.add_argument("--out", default="plans44_rows.json")
    x = a.parse_args()
    if not KEY: raise SystemExit("нет GOSPLAN_KEY в окружении")
    stats = {"requests": 0, "errors": 0, "positions": 0, "rows": 0}
    classes = DEFAULT_CLASSES
    try:
        okpd = json.load(open(x.dict, encoding="utf-8")).get("data", {}).get("okpd2")
    except Exception: okpd = None
    rows, seen = [], set()

    def take(batch):
        stats["positions"] += len(batch)
        for p in batch:
            r = to_row(p, x.date, x.min_price)
            if r and r["id"] not in seen: seen.add(r["id"]); rows.append(r)

    if x.mode == "delta":
        for cl in classes:
            for pg in range(x.pages):
                if stats["requests"] >= x.max_requests or pg * 100 > MAX_SKIP: break
                b = fetch({"classifier": [cl], "limit": 100, "skip": pg * 100, "sort": "published_at_desc"}, stats)
                take(b); time.sleep(x.pause)
                if len(b) < 100: break
    else:
        inns = customers_from(x.leadsets) if x.leadsets else []
        n = len(inns)
        part = [inns[(x.start + i) % n] for i in range(min(x.batch, n))] if n else []
        stats["customers"] = len(part); stats["customersTotal"] = n
        stats["next"] = (x.start + len(part)) % n if n else 0
        for inn in part:
            if stats["requests"] >= x.max_requests: stats["next"] = (x.start + part.index(inn)) % n; break
            take(fetch({"customer": inn, "classifier": classes, "limit": 100}, stats)); time.sleep(x.pause)
    stats["rows"] = len(rows)
    json.dump({"rows": rows, "stats": stats}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
