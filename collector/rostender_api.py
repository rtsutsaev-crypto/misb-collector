"""rostender_api.py — Client API РосТендера (rostender.info/api/tenders/get/, ключ в заголовке X-API-KEY, только GET).

Два режима, оба за один запуск, общий дневной лимит ключа (200 успешных ответов в сутки по Москве; info не списывается):
1) enrich — карточки открытых лидов РосТендера (source rostender:*, id — номер РосТендера). Страницы каталога не показывают
   заказчика: из карточки берутся заказчик и ИНН, контакт из извещения (лицо, телефон, e-mail), номер ЕИС и площадки.
   Документ — в коллекцию leadcontacts (doc_id = key, как у contacts.py): {id, key, name, phone, email, org, customer,
   customerInn, eis, law, src: "rostender", checkedAt} или {id, key, none: true, src, checkedAt}. Сайт подставляет заказчика
   в лид без заказчика и показывает контакт строкой «Контакт закупки».
2) templates — шаблоны поиска из личного кабинета РосТендера (Профиль → шаблоны поиска; нет шаблонов — режим пропускается):
   список тендеров шаблона (100 на страницу, новые сначала), для новых номеров — карточка, отбор collector.py (Matcher, Known).
   Лиды source rostender:api.

Запуск: ROSTENDER_KEY=... python3 rostender_api.py --leadsets <папка leadsets> [--known-contacts <папка leadcontacts>]
        [--known known.json] --dict dictionary.json --date ГГГГ-ММ-ДД --out rt_api_out.json
        [--max 150] [--reserve 10] [--templates-share 0.5] [--days 3]
Выход: {docs, leads, updates, stats}. Ключ только из окружения, в выход и журнал не пишется.
"""
import argparse, datetime as dt, glob, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C

KEY = os.environ.get("ROSTENDER_KEY", "")
BASE = "https://rostender.info/api/tenders/get/"


class Limit(Exception):
    pass


def get(path, st, params=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"X-API-KEY": KEY, "Accept": "application/json", "User-Agent": "misb-collector/1.0"})
    for t in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                if path != "info": st["requests"] += 1
                return json.loads(r.read().decode()).get("data")
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            if e.code in (401, 403): raise Limit(f"HTTP {e.code}: " + e.read().decode("utf-8", "ignore")[:150])
            time.sleep(2 + 3 * t)
        except Exception:
            time.sleep(2 + 2 * t)
    st["errors"] += 1
    return None


def law(card):
    t = str(card.get("type") or "")
    return "44-ФЗ" if t == "44" else "223-ФЗ" if t == "223" else "коммерческая" if t == "commerce" else ""


def row(card):
    """Карточка → строка для collector.process_rows."""
    cu = card.get("customer") or {}
    dl = str(card.get("dte-formatted") or card.get("dte") or "")[:10]
    okpd = sorted({c for p in card.get("positions") or [] for c in ((p.get("classifier") or {}).get("OKPD2") or [])})
    regs = card.get("regions") or []
    return {"id": str(card["id"]), "title": re.sub(r"\s+", " ", str(card.get("descr") or "")).strip()[:500],
            "customer": (cu.get("name") or "").strip(), "customerInn": str(cu.get("inn") or ""),
            "region": regs[0] if isinstance(regs, list) and regs else str(regs or ""),
            "price": (card.get("price") or {}).get("value") or None, "deadline": dl if re.fullmatch(r"\d{4}-\d\d-\d\d", dl) else "",
            "law": law(card), "url": card.get("url") or f"https://rostender.info/tender/{card['id']}", "okpd2": okpd,
            "eisNumber": str(card.get("eis") or ""), "publishedAt": str(card.get("dts") or "")[:10]}


def contact_doc(i, card, today):
    cu = card.get("customer") or {}
    c = cu.get("contacts") or {}
    d = {"id": i, "key": C.site_key(i), "name": str(c.get("person") or "").strip(), "phone": str(c.get("phone") or "").strip(),
         "email": str(c.get("email") or "").strip(), "org": (cu.get("name") or "").strip()[:200], "customer": (cu.get("name") or "").strip()[:300],
         "customerInn": str(cu.get("inn") or ""), "eis": str(card.get("eis") or ""), "law": law(card), "src": "rostender", "checkedAt": today}
    d = {k: v for k, v in d.items() if v}
    if not (d.get("customer") or d.get("phone") or d.get("email")):
        return {"id": i, "key": C.site_key(i), "none": True, "src": "rostender", "checkedAt": today}
    return d


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leadsets", required=True); a.add_argument("--known-contacts"); a.add_argument("--known")
    a.add_argument("--dict", default="dictionary.json"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--max", type=int, default=150, help="не больше запросов за запуск (лимит ключа 200 в сутки)")
    a.add_argument("--reserve", type=int, default=10, help="оставить в дневном лимите")
    a.add_argument("--templates-share", type=float, default=0.5, help="доля запросов на шаблоны, если они есть")
    a.add_argument("--days", type=int, default=3, help="templates: тендеры, опубликованные не раньше чем days дней назад")
    a.add_argument("--budget-sec", type=int, default=600)   # РосТендер из облака отвечает медленно: ~3–4 с на карточку
    x = a.parse_args()
    if not KEY: sys.exit("нет ROSTENDER_KEY")
    today = dt.date.fromisoformat(x.date)
    st = {"requests": 0, "errors": 0}
    docs, leads, ups = [], [], []
    try:
        info = get("info", st) or {}
    except Limit as e:
        st["stopped"] = str(e); info = {}
    left = min(x.max, max(0, int(info.get("remaining") or 0) - x.reserve)) if info else 0
    st["remaining"] = info.get("remaining"); st["dailyLimit"] = info.get("daily_limit")
    t0 = time.time()

    def spend():
        return left - st["requests"] > 0 and time.time() - t0 < x.budget_sec

    # templates: шаблоны и избранное из личного кабинета
    tpl = []
    if left:
        try:
            tpl = get("templates", st) or []
        except Limit as e:
            st["stopped"] = str(e); left = 0
    st["templates"] = len(tpl)
    tpl_budget = int(left * x.templates_share) if tpl else 0
    if tpl_budget:
        m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
        K = C.Known()
        if x.known and os.path.exists(x.known):
            kj = json.load(open(x.known, encoding="utf-8")); K.ids = kj.get("ids", {}); K.keys = set(kj.get("keys", []))
        since = (today - dt.timedelta(days=x.days)).isoformat()
        new_ids, listed = [], 0
        try:
            for t in tpl:
                for page in range(1, 6):
                    if st["requests"] >= tpl_budget: break
                    items = get(f"template/{t.get('id')}", st, {"page": page, "sort": "new-first"}) or []
                    old = False
                    for it in items:
                        listed += 1
                        i = str(it.get("id") or "")
                        if str(it.get("dts") or "")[:10] and str(it.get("dts"))[:10] < since: old = True; continue
                        if not i or i in K.ids or i in new_ids: continue
                        if str(it.get("dte") or "")[:10] and str(it.get("dte"))[:10] < x.date: continue
                        new_ids.append(i)
                    if old or len(items) < 100: break
            rows = []
            for i in new_ids:
                if st["requests"] >= tpl_budget or not spend(): st["templatesCut"] = len(new_ids) - len(rows); break
                card = get(i, st)
                time.sleep(0.2)
                if card: rows.append(row(card)); docs.append(contact_doc(i, card, x.date))
            L, U, s = C.process_rows(rows, m, K, "rostender:api", x.date)
            for l in L: l["flags"] = sorted(set(l.get("flags") or []) | {"api"})
            leads += L; ups += U
            st.update(listed=listed, cards=len(rows), matched=s["matched"], new=len(L))
        except Limit as e:
            st["stopped"] = str(e); left = 0

    # enrich: открытые лиды РосТендера без заказчика и контакта
    known = {}
    if x.known_contacts and os.path.isdir(x.known_contacts):
        for f in glob.glob(os.path.join(x.known_contacts, "**", "*.json"), recursive=True):
            j = json.load(open(f, encoding="utf-8")); known[os.path.basename(f)[:-5]] = j.get("data", j)
    done = {d["id"] for d in docs}
    cand, seen = [], set()
    for f in glob.glob(os.path.join(x.leadsets, "**", "*.json"), recursive=True):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []) if isinstance(j, dict) else []:
            i = str(l.get("id") or "")
            if i in seen or i in done or not str(l.get("source") or "").startswith("rostender") or not re.fullmatch(r"\d{6,10}", i): continue
            seen.add(i)
            if (l.get("deadline") or "") < x.date: continue                      # закрытые не обогащаем
            if l.get("customer") and l.get("contacts"): continue
            old = known.get(C.site_key(i))
            if old:
                age = (today - dt.date.fromisoformat(str(old.get("checkedAt") or "2000-01-01")[:10])).days
                if old.get("src") == "rostender" or not old.get("none") or age < 30: continue
            cand.append(((l.get("collectedAt") or ""), i))
    cand.sort(reverse=True)                                                   # сначала свежие лиды
    st["candidates"] = len(cand)
    found = none = 0
    try:
        for _, i in cand:
            if not spend(): st.setdefault("stopped", "лимит запросов запуска" if left - st["requests"] <= 0 else "бюджет времени"); break
            card = get(i, st)
            time.sleep(0.2)
            if not card: continue
            d = contact_doc(i, card, x.date); docs.append(d)
            if d.get("none"): none += 1
            else: found += 1
    except Limit as e:
        st["stopped"] = str(e)
    st.update(enriched=found, none=none, docs=len(docs), leads=len(leads))
    json.dump({"docs": docs, "leads": leads, "updates": ups, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
