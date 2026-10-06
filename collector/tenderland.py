"""tenderland.py — TenderLand API (tenderland.ru/Api/v1/, ключ — параметр apiKey, только GET, JSON).

Новые тендеры из автопоисков личного кабинета TenderLand: автопоиск настраивается в ЛК (слова, исключения, регионы,
площадки — в том числе коммерческие ЭТП, которых нет в ЕИС), его номер виден в адресе автопоиска и записывается в план
(поле autosearchIds источника tenderland). Выгрузка в два шага: Export/Create?autosearchId=N&limit=…&format=json&exportViewId=1
→ {Id}; Export/Get?exportId=Id → {items: [{tender: {regNumber, name, beginPrice, publishDate, endDate, region, typeName,
lotCategories, etpLink, linkToCard, customers: [{lotCustomerShortName}]}}]} — по 100 записей, следующие — offset=100, 200…
Цена (beginPrice) уже в рублях, в том числе у тендеров СНГ (module «СНГ»). Строки проходят отбор collector.py (Matcher, Known):
номер ЕИС совпадает с id лидов ГосПлана, повторов нет. Лиды source tenderland.
Нет автопоисков — скрипт ничего не запрашивает (stats.status = no-autosearch).

Запуск: TENDERLAND_KEY=... python3 tenderland.py --autosearch ID [ID …] --dict dictionary.json --known known.json
        --date ГГГГ-ММ-ДД --out tl_out.json [--limit 600] [--days 2]
Выход: {leads, updates, stats}. Ключ только из окружения. Ответы API содержат ключ в ссылках на файлы (files) —
такие поля не сохраняются, а выход проверяется на отсутствие ключа.
"""
import argparse, datetime as dt, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C

KEY = os.environ.get("TENDERLAND_KEY", "")
BASE = "https://tenderland.ru/Api/v1/"


def hide(s):
    return str(s).replace(KEY, "<ключ>") if KEY else str(s)


def get(path, params, st):
    url = BASE + path + "?" + urllib.parse.urlencode(dict(params, apiKey=KEY))
    for t in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "misb-collector/1.0"}), timeout=90) as r:
                st["requests"] += 1
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            st["requests"] += 1
            body = e.read().decode("utf-8", "ignore")
            if e.code == 400 and "ключ" in body.lower(): raise SystemExit("ключ TenderLand не принят: " + hide(body)[:200])
            if e.code == 400: return {"Success": False, "Description": hide(body)[:300]}
            time.sleep(3 + 3 * t)
        except Exception:
            time.sleep(3 + 3 * t)
    st["errors"] += 1
    return None


def export(aid, limit, since, st):
    """Выгрузка автопоиска → список tender (новые сначала). Export/Get отдаёт по 100 записей, дальше — параметр offset;
    чтение останавливается на тендерах, опубликованных раньше since."""
    c = get("Export/Create", {"autosearchId": aid, "limit": limit, "format": "json", "exportViewId": 1}, st)
    if not c or not c.get("Id"):
        raise RuntimeError(hide((c or {}).get("Description") or "нет ответа")[:200])
    total, out = int(c.get("TotalCount") or limit), []
    while len(out) < total:
        items = None
        for t in range(6):                               # выгрузка готовится на сервере — несколько попыток
            g = get("Export/Get", {"exportId": c["Id"], "offset": len(out)}, st)
            if g and isinstance(g.get("items"), list):
                items = [it.get("tender") or {} for it in g["items"]]; break
            time.sleep(5 + 5 * t)
        if items is None:
            if out: st["cut"] = "выгрузка оборвалась"; break
            raise RuntimeError("выгрузка не готова")
        out += items
        if len(items) < 100 or any(str(x.get("publishDate") or "")[:10] and str(x["publishDate"])[:10] < since for x in items): break
        time.sleep(0.5)
    return out


def law(type_name):
    t = str(type_name or "")
    return "44-ФЗ" if re.search(r"44", t) else "223-ФЗ" if re.search(r"223", t) else ""


CIS = [("казахстан", "KZ"), ("беларус", "BY"), ("узбекистан", "UZ"), ("ташкент", "UZ"), ("кыргыз", "KG"), ("киргиз", "KG"),
       ("армени", "AM"), ("азербайджан", "AZ"), ("таджикистан", "TJ"), ("молдов", "MD"), ("абхаз", "AB")]


def country(t):
    """Страна тендера СНГ по региону; без совпадения — Россия (цена у TenderLand уже в рублях)."""
    if str(t.get("module") or "") != "СНГ": return "RU"
    r = str(t.get("region") or "").lower()
    return next((c for k, c in CIS if k in r), "")


def row(t):
    cust = next((c.get("lotCustomerShortName") or c.get("lotCustomerFullName") or "" for c in t.get("customers") or [] if isinstance(c, dict)), "")
    tid = str(t.get("regNumber") or t.get("id") or "").strip()
    m = re.search(r"id=(\d+)", str(t.get("linkToCard") or ""))
    if not tid and m: tid = "TL" + m.group(1)
    etp = str(t.get("etpLink") or "")
    return {"id": tid, "title": re.sub(r"\s+", " ", str(t.get("name") or "")).strip()[:500], "customer": cust.strip(),
            "region": str(t.get("region") or ""), "price": t.get("beginPrice") or None,
            "deadline": str(t.get("endDate") or "")[:10], "law": law(t.get("typeName")),
            "url": t.get("linkToCard") or "", "publishedAt": str(t.get("publishDate") or "")[:10], "country": country(t),
            "note": "; ".join(x for x in [str(t.get("module") or ""), str(t.get("typeName") or ""), ", ".join(t.get("lotCategories") or []),
                                          ("площадка " + etp) if etp and KEY not in etp else ""] if x)[:300]}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--autosearch", nargs="*", default=[]); a.add_argument("--dict", default="dictionary.json"); a.add_argument("--known")
    a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--limit", type=int, default=600); a.add_argument("--days", type=int, default=2)
    x = a.parse_args()
    st = {"requests": 0, "errors": 0, "autosearch": len(x.autosearch)}
    leads, updates = [], []
    if not x.autosearch:
        st["status"] = "no-autosearch"
    else:
        if not KEY: sys.exit("нет TENDERLAND_KEY")
        m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
        K = C.Known()
        if x.known and os.path.exists(x.known):
            kj = json.load(open(x.known, encoding="utf-8")); K.ids = kj.get("ids", {}); K.keys = set(kj.get("keys", []))
        since = (dt.date.fromisoformat(x.date) - dt.timedelta(days=x.days)).isoformat()
        for aid in x.autosearch:
            s1 = st.setdefault("by", {}).setdefault(str(aid), {})
            try:
                items = export(aid, x.limit, since, st)
            except RuntimeError as e:
                s1.update(status="failed", note=str(e)); continue
            rows = [r for r in map(row, items) if r["id"] and r["title"] and (not r["publishedAt"] or r["publishedAt"] >= since)]
            L, U, s = C.process_rows(rows, m, K, "tenderland", x.date)
            for l in L: K.add(l)
            leads += L; updates += U
            s1.update(status="ok", rows=len(items), fresh=len(rows), matched=s["matched"], new=len(L), updates=len(U))
        st["status"] = "ok" if any(v.get("status") == "ok" for v in st.get("by", {}).values()) else "failed"
    st["leads"] = len(leads)
    text = json.dumps({"leads": leads, "updates": updates, "stats": st}, ensure_ascii=False)
    if KEY and KEY in text: text = text.replace(KEY, "")          # страховка: ключ не попадает в базу
    open(x.out, "w", encoding="utf-8").write(text)
    print(hide(json.dumps(st, ensure_ascii=False)))


if __name__ == "__main__":
    main()
