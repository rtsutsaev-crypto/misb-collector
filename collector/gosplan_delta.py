"""gosplan_delta.py — полный инкрементальный проход по ЕИС через ГосПлан: все закупки 44-ФЗ и 223-ФЗ,
опубликованные после курсора, без поисковых слов. Отбор делает collector.py; для заказчиков из watchlist
(холдинги, дочки, повторные покупатели) — расширенный отбор: консалтинг и «мероприятия» тоже берутся, с флагом watch.
Плюс проход по updated_after: у известных закупок ловит новый срок и отмену.
С --precursors (precursors.json): закупки-предвестники (СОУТ, ISO, внедрение 1С/ERP, разработка стратегии, учебный центр,
бережливое производство) у заказчиков из watchlist или с НМЦК от minPrice — ранние сигналы sig-pre-<номер> (source sig-precursor,
флаг early, validUntil = публикация + months): через 2–6 месяцев такой заказчик почти всегда покупает обучение.

Запуск: GOSPLAN_KEY=... python3 gosplan_delta.py --state state.json --known known.json --dict dictionary.json
        --watch watch.json [watch_extra.json] --date ГГГГ-ММ-ДД --out out.json [--max-requests 600] [--precursors precursors.json]
state.json: {"published": {"fz44": iso, "fz223": iso}, "updated": {"fz44": iso, "fz223": iso}} (пусто — 3 дня назад).
out.json: {leads, updates, stats, state}. state из out.json записывается в meta/gosplan-delta.
"""
import argparse, json, os, re, subprocess, sys, time, urllib.parse, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C

KEY = os.environ.get("GOSPLAN_KEY", "")
BASE = "https://v2.gosplan.info"
REG = {1:'Адыгея',2:'Башкортостан',3:'Бурятия',4:'Алтай республика',5:'Дагестан',6:'Ингушетия',7:'Кабардино-Балкария',8:'Калмыкия',9:'Карачаево-Черкесия',10:'Карелия',11:'Коми',12:'Марий Эл',13:'Мордовия',14:'Якутия (Саха)',15:'Северная Осетия',16:'Татарстан',17:'Тыва',18:'Удмуртия',19:'Хакасия',20:'Чечня',21:'Чувашия',22:'Алтайский край',23:'Краснодарский край',24:'Красноярский край',25:'Приморский край',26:'Ставропольский край',27:'Хабаровский край',28:'Амурская область',29:'Архангельская область',30:'Астраханская область',31:'Белгородская область',32:'Брянская область',33:'Владимирская область',34:'Волгоградская область',35:'Вологодская область',36:'Воронежская область',37:'Ивановская область',38:'Иркутская область',39:'Калининградская область',40:'Калужская область',41:'Камчатский край',42:'Кемеровская область',43:'Кировская область',44:'Костромская область',45:'Курганская область',46:'Курская область',47:'Ленинградская область',48:'Липецкая область',49:'Магаданская область',50:'Московская область',51:'Мурманская область',52:'Нижегородская область',53:'Новгородская область',54:'Новосибирская область',55:'Омская область',56:'Оренбургская область',57:'Орловская область',58:'Пензенская область',59:'Пермский край',60:'Псковская область',61:'Ростовская область',62:'Рязанская область',63:'Самарская область',64:'Саратовская область',65:'Сахалинская область',66:'Свердловская область',67:'Смоленская область',68:'Тамбовская область',69:'Тверская область',70:'Томская область',71:'Тульская область',72:'Тюменская область',73:'Ульяновская область',74:'Челябинская область',75:'Забайкальский край',76:'Ярославская область',77:'Москва',78:'Санкт-Петербург',79:'Еврейская АО',82:'Крым',83:'Ненецкий АО',86:'ХМАО',87:'Чукотский АО',89:'ЯНАО',92:'Севастополь',99:'Байконур'}
# расширенный отбор для заказчиков из watchlist: слова «около обучения», только с начала слова
WATCH_RX = re.compile(r"(?<![а-яё])(мероприяти\w* (для|по) (персонал|работник|сотрудник|руководител)|стратегическ|консалтинг|методическ\w* сопровожд|"
                      r"развити\w* (персонал|компетенц|кадр|руководител|команд)|корпоративн\w* (университет|академи|культур)|учебн\w* (центр|програм|мероприят)|"
                      r"акселер|образоват|кадров\w* (резерв|аудит|политик)|оценк\w* (персонал|компетенц)|производственн\w* систем|бережлив|программ\w* развития)")
EIS = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString="
CANCEL_STAGES = {"4"}
PRE_LIMIT = 40          # сигналов-предвестников за запуск, не больше (самые дорогие первыми)   # stage 4 — определение поставщика отменено (у 223 — «отменена»)


def get(path, params, stats):
    params = dict(params, apikey=KEY)
    url = BASE + path + "?" + urllib.parse.urlencode(params, doseq=True)
    for t in range(5):
        r = subprocess.run(["curl", "-s", "-m", "120", "-w", "\n%{http_code}", url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        stats["requests"] += 1
        if code == "200":
            try: return json.loads(body)
            except Exception: pass
        if code in ("401", "403"): raise SystemExit(f"ключ не принят: {code} {body[:200]}")
        if code == "422": stats["errors"] += 1; stats.setdefault("lastError", body[:300]); return None
        time.sleep(2 if code == "429" else 4)
    stats["errors"] += 1
    return None


def walk(law, field, since, until, stats, limit_req, today):
    """Все записи с field_after > since (keyset по возрастанию, по 100), день за днём до сегодняшнего.
    ГосПлан отдаёт по *_after только записи того же календарного дня (проверено 01.10.2026: после
    2026-09-30T23:59:36 — одна запись этой секунды, ни одной за 1 октября), поэтому, исчерпав день,
    курсор переходит на 00:00:00 следующего дня. Ошибка запроса — стоп без перехода, чтобы не потерять день."""
    sort = ("published_at_asc" if field == "published" else "updated_at_asc")
    key = "published_at" if field == "published" else "updated_at"
    cur, seen, out = since, set(), []
    while stats["requests"] < limit_req:
        p = {f"{field}_after": cur, "limit": 100, "sort": sort}
        if until: p[f"{field}_before"] = until
        j = get(f"/{law}/purchases", p, stats)
        if not isinstance(j, list): break
        fresh = [x for x in j if x.get("purchase_number") not in seen]
        for x in fresh: seen.add(x["purchase_number"]); out.append(x)
        last = (j[-1].get(key) if j else None) or cur
        if fresh and last != cur and len(j) >= 100:
            cur = last
            time.sleep(0.12)
            continue
        cur = max(cur, last)
        nxt = (dt.date.fromisoformat(cur[:10]) + dt.timedelta(days=1)).isoformat()
        if nxt > today or (until and nxt > until[:10]): break
        cur = nxt + "T00:00:00"
        time.sleep(0.12)
    return out, cur


def to_row(x, law):
    dl = (x.get("collecting_finished_at") if law == "fz44" else x.get("submission_close_at")) or ""
    inn = (x.get("customers") or [x.get("customer") or ""])[0] if law == "fz44" else (x.get("customer") or "")
    num = x["purchase_number"]
    return {"id": num, "title": x.get("object_info") or "", "customer": ("ИНН " + inn) if inn else "", "customerInn": inn,
            "region": REG.get(int(x.get("region") or 0), ""), "price": x.get("max_price"), "deadline": dl[:10],
            "url": EIS + num, "okpd2": x.get("okpd2") or [], "law": "44-ФЗ" if law == "fz44" else "223-ФЗ",
            "stage": str(x.get("stage") or ""),
            "flags": (["rfq"] if re.search(r"EZK|ZK\d|ZKB|ZP|Quotation|Proposal", str(x.get("purchase_type") or ""), re.I) else [])
                     + (["smp"] if re.search(r"SMBO|ESMBO", str(x.get("purchase_type") or ""), re.I) else []),
            "purchaseType": x.get("purchase_type") or "", "publishedAt": (x.get("published_at") or "")[:10]}


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, min(d.day, 28)).isoformat()


def precursors(rows, pre, watch, known_ids, today, lead_ids=()):
    """Ранние сигналы по закупкам-предвестникам. rows — строки to_row(); отбор: класс предвестника и (watchlist или НМЦК ≥ minPrice)."""
    classes = [(c, re.compile(c["rx"])) for c in pre.get("classes", [])]
    out = []
    for r in rows:
        sid = "sig-pre-" + r["id"]
        if sid in known_ids: continue
        t = C.norm_text(r["title"])
        hit = next((c for c, rx in classes if rx.search(t)), None)
        if not hit: continue
        if r["id"] in lead_ids and hit["key"] != "combo": continue      # сама закупка уже лид — второй карточки-предвестника не нужно
        w = watch.get(r["customerInn"])
        if not w and (r["price"] or 0) < pre.get("minPrice", 1000000): continue
        pub = dt.date.fromisoformat((r["publishedAt"] or today)[:10])
        l = {"id": sid, "title": f"Предвестник ({hit['key']}): {r['title'][:300]}", "customer": r["customer"], "customerInn": r["customerInn"],
             "region": r["region"], "price": None, "deadline": "", "validUntil": add_months(pub, hit.get("months", 3)),
             "law": r["law"], "url": r["url"], "source": "sig-precursor", "collectedAt": today, "country": "RU", "currency": "RUB",
             "flags": sorted({"partner" if hit["key"] == "combo" else "early"} | ({"watch"} | ({"tek"} if w.get("group") else set()) if w else set())),
             "publishedAt": r["publishedAt"], "eisNumber": r["id"], "signalPrice": r["price"],
             "note": f"Ранний сигнал: заказчик закупает «{hit['key']}» (НМЦК {r['price'] or '—'}). Ждать: {hit['expect']} — через ~{hit.get('months', 3)} мес."
                     + (f" Группа: {w['group']}." if w and w.get("group") else "")}
        out.append(l)
    out.sort(key=lambda l: (not ("watch" in l["flags"]), -(l["signalPrice"] or 0)))
    return out[:PRE_LIMIT]


def main():
    a = argparse.ArgumentParser()
    for k in ("state", "known", "dict", "date", "out"): a.add_argument("--" + k)
    a.add_argument("--watch", nargs="*", default=[])   # meta/watchlist и meta/watchlist-extra: списки сливаются
    a.add_argument("--max-requests", type=int, default=600)
    a.add_argument("--until", default="")
    a.add_argument("--precursors", default="")
    x = a.parse_args()
    today = x.date or dt.date.today().isoformat()
    st = json.load(open(x.state, encoding="utf-8")) if x.state and os.path.exists(x.state) else {}
    st = st.get("data", st)
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=3)).isoformat() + "T00:00:00"
    until = x.until   # API не принимает *_before позже конца текущих суток; по умолчанию без верхней границы
    m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
    kj = json.load(open(x.known, encoding="utf-8")); K = C.Known(); K.ids = kj.get("ids", {}); K.keys = set(kj.get("keys", []))
    watch = {}
    for wf in x.watch:
        if not os.path.exists(wf): continue
        w = json.load(open(wf, encoding="utf-8")); w = w.get("data", w); w = w.get("inns", w)
        for k, v in w.items(): watch.setdefault(k, v)
    stats = {"requests": 0, "errors": 0}
    leads, updates, per = [], [], {}
    pre = json.load(open(x.precursors, encoding="utf-8")) if x.precursors and os.path.exists(x.precursors) else None
    allrows = []
    new_state = {"published": {}, "updated": {}}
    for law in ("fz44", "fz223"):
        src = "gosplan:" + law
        since = (st.get("published") or {}).get(law) or start
        recs, cur = walk(law, "published", since, until, stats, x.max_requests, today)
        new_state["published"][law] = cur
        rows = [to_row(r, law) for r in recs]
        allrows += rows
        L, U, s = C.process_rows(rows, m, K, src, today)
        # расширенный отбор для заказчиков из watchlist
        got = {l["id"] for l in L}
        extra = []
        for r in rows:
            if r["id"] in got or K.has(r) or r["customerInn"] not in watch: continue
            t = C.norm_text(r["title"])
            rel, why, _ = m.classify(r["title"], r["okpd2"])
            if rel or not WATCH_RX.search(t) or why.startswith(("исключение", "предмет не обучение")): continue
            if r["deadline"] and r["deadline"] < today: continue
            l = C.build_lead(r, m, src, today)
            w = watch[r["customerInn"]]
            l["flags"] = sorted(set(l["flags"]) | {"watch", "tek"} if w.get("group") else set(l["flags"]) | {"watch"})
            l["note"] = "Заказчик из списка наблюдения" + (f": {w.get('group')}" if w.get("group") else "") + "; тема не распознана словарём — проверить предмет"
            extra.append(l)
        for l in L:
            w = watch.get(l.get("customerInn", ""))
            if w and w.get("group"): l["flags"] = sorted(set(l["flags"]) | {"tek"}); l["note"] = f"Группа: {w['group']}" + (f" ({w.get('level')})" if w.get("level") else "")
        for l in L + extra: K.add(l); l.pop("stage", None)
        leads += L + extra; updates += U
        # обновления известных закупок: новый срок, отмена
        usince = (st.get("updated") or {}).get(law) or start
        urecs, ucur = walk(law, "updated", usince, until, stats, x.max_requests, today)
        new_state["updated"][law] = ucur
        uu = 0
        for r in (to_row(q, law) for q in urecs):
            k = K.has(r)
            if not k: continue
            prev = K.ids.get(k, "")
            canc = r["stage"] in CANCEL_STAGES
            if (r["deadline"] and r["deadline"] != prev) or canc:
                updates.append({"id": k, "deadline": r["deadline"] or prev, "prevDeadline": prev, "url": r["url"], **({"cancelled": True} if canc else {})}); uu += 1
        per[law] = {"rows": s["rows"], "matched": s["matched"], "new": len(L), "watch": len(extra), "updatedRows": len(urecs), "updates": len(U) + uu}
    sig = precursors(allrows, pre, watch, K.ids, today, {l["id"] for l in leads}) if pre else []
    leads += sig
    per["precursors"] = len(sig)
    # схлопнуть дубли обновлений
    seenu, upd2 = set(), []
    for u in updates:
        if u["id"] in seenu: continue
        seenu.add(u["id"]); upd2.append(u)
    for l in leads:
        l.pop("stage", None)   # okpd2 оставляем в лиде: verdicts и разбор шума видят те же данные
        if "rfq" in l.get("flags", []) and not l.get("note"): l["note"] = "Запрос котировок или предложений"
    out = {"leads": leads, "updates": upd2, "stats": dict(stats, per=per), "state": dict(new_state, updatedAt=today)}
    json.dump(out, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(out["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
