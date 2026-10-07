"""egrul.py — владельцы и дочерние общества заказчиков по ЕГРЮЛ (API Чекко, /v2/company, ключ из окружения CHECKO_KEY).

Два прохода в порядке плана egrul:
1) upward — заказчики лидов (и кандидаты с известным ИНН) без записи в коллекции ownership поднимаются по учредителям
   к группе: учредитель-организация с долей не меньше minShare, до maxDepthUp шагов. Физические лица не сохраняются.
2) downward — головные компании групп из meta/holdings (ИНН в aliases): дочерние общества из «СвязУчред» карточки
   (без доли: в ответе API она не приходит, поле share = null и note «доля не проверена»).

Бесплатный ключ Чекко: около 100 запросов в сутки. Счётчик today_request_count в каждом ответе: при 90 и выше скрипт
останавливается (status skipped, «лимит Чекко на сегодня исчерпан»), 401/403 — status failed.
Запуск: CHECKO_KEY=... python3 egrul.py --leadsets <папка leadsets> --ownership <папка ownership> --holdings meta_holdings.json
        --candidates group_candidates.json --date ГГГГ-ММ-ДД --out egrul_out.json [--max 40] [--max-depth 3] [--min-share 50]
Выход: {docs: [ownership-документы], stats}. Ключ в выход не пишется.
"""
import argparse, datetime as dt, glob, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("CHECKO_KEY", "")
BASE = "https://api.checko.ru/v2"
LIMIT_TODAY = 90
PERSON_CATS = {"ФЛ"}                                 # физлица не сохраняем (персональные данные)


class Stop(Exception):
    pass


def card(inn, st):
    url = BASE + "/company?" + urllib.parse.urlencode({"key": KEY, "inn": inn})
    for t in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                j = json.loads(r.read().decode())
            st["requests"] += 1
            meta = j.get("meta") or {}
            st["today"] = meta.get("today_request_count", st.get("today"))
            if meta.get("status") != "ok":
                st["errors"] += 1; st.setdefault("lastError", str(meta)[:200])
                if "limit" in str(meta).lower() or (st["today"] or 0) >= LIMIT_TODAY: raise Stop("лимит Чекко на сегодня исчерпан")
                return None
            if (st["today"] or 0) >= LIMIT_TODAY: raise Stop("лимит Чекко на сегодня исчерпан")
            return j.get("data") or {}
        except urllib.error.HTTPError as e:
            st["requests"] += 1
            if e.code in (401, 403): raise Stop(f"ответ {e.code}: ключ или доступ — проверить")
            time.sleep(2 + 2 * t)
        except Stop:
            raise
        except Exception:
            time.sleep(2 + 2 * t)
    st["errors"] += 1
    return None


def share_pct(item):
    """Доля участия в процентах из карточки учредителя; None, если в ответе её нет."""
    d = item.get("Доля")
    if isinstance(d, dict):
        for k in ("Процент", "Значение", "Проценты"):
            v = d.get(k)
            if isinstance(v, (int, float)): return float(v)
            if isinstance(v, str) and re.match(r"^\s*\d+([.,]\d+)?\s*%?\s*$", v): return float(v.strip(" %").replace(",", "."))
    if isinstance(d, (int, float)): return float(d)
    return None


def founders(data, min_share):
    """Учредители-организации: [{inn, name, share, type}]; физлица и государственные образования — без ИНН-обхода."""
    out = []
    for cat, items in (data.get("Учред") or {}).items():
        if cat in PERSON_CATS or not isinstance(items, list): continue
        for it in items:
            if not isinstance(it, dict): continue
            inn = str(it.get("ИНН") or "")
            name = it.get("НаимПолн") or it.get("НаимМО") or ""
            if not inn:
                op = (it.get("ОргОсущПрав") or [{}])[0]
                inn = str(op.get("ИНН") or ""); name = name or op.get("НаимПолн") or ""
            out.append({"inn": inn if re.fullmatch(r"\d{10}", inn) else "", "name": name, "share": share_pct(it),
                        "type": cat if cat != "РФ" else "госорган/МО"})
    return out


def ownership_doc(inn, data, min_share, today):
    fs = founders(data, min_share)
    top = max((f for f in fs if f["share"] is not None and f["share"] >= min_share), key=lambda f: f["share"], default=None)
    okv = (data.get("ОКВЭД") or {}).get("Наим") or ""
    return {"inn": inn, "name": data.get("НаимПолн") or data.get("НаимСокр") or "", "status": (data.get("Статус") or {}).get("Наим") if isinstance(data.get("Статус"), dict) else data.get("Статус") or "",
            "okved": okv[:200], "founders": [{k: v for k, v in f.items() if k != "type" or v} for f in fs if f["inn"] or f["name"]],
            "group": top["name"] if top else "", "groupShare": top["share"] if top else None,
            "chain": [], "via": "checko company", "checkedAt": today, "contacts": {}}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leadsets", required=True); a.add_argument("--ownership"); a.add_argument("--holdings")
    a.add_argument("--candidates"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--max", type=int, default=40); a.add_argument("--max-depth", type=int, default=3); a.add_argument("--min-share", type=float, default=50)
    a.add_argument("--recheck-days", type=int, default=180)
    x = a.parse_args()
    if not KEY: sys.exit("нет CHECKO_KEY")
    today = dt.date.fromisoformat(x.date)
    old = (today - dt.timedelta(days=x.recheck_days)).isoformat()
    own = {}
    if x.ownership and os.path.isdir(x.ownership):
        for f in glob.glob(os.path.join(x.ownership, "**", "*.json"), recursive=True):
            j = json.load(open(f, encoding="utf-8")); own[os.path.basename(f)[:-5]] = j.get("data", j)
    # заказчики лидов, свежие первыми
    cust = {}
    for f in glob.glob(os.path.join(x.leadsets, "**", "*.json"), recursive=True):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []) if isinstance(j, dict) else []:
            i = str(l.get("customerInn") or "")
            if re.fullmatch(r"\d{10}", i):
                cust[i] = max(cust.get(i, ""), l.get("collectedAt") or "")
    cands = []
    if x.candidates and os.path.exists(x.candidates):
        c = json.load(open(x.candidates, encoding="utf-8")); c = c.get("data", c)
        cands = [str(r.get("innHint")) for r in c.get("candidates", []) if re.fullmatch(r"\d{10}", str(r.get("innHint") or ""))]
    heads = []
    if x.holdings and os.path.exists(x.holdings):
        h = json.load(open(x.holdings, encoding="utf-8")); h = h.get("data", h)
        for g in h.get("groups", []):
            hi = next((a_ for a_ in g.get("aliases", []) if re.fullmatch(r"\d{10}", str(a_))), None)
            if hi: heads.append((hi, g.get("name", "")))

    def fresh(i):
        d = own.get(i)
        return bool(d) and (d.get("checkedAt") or "2000-01-01") >= old

    st = {"requests": 0, "errors": 0, "today": None, "up": 0, "down": 0, "children": 0}
    docs, seen = [], set()
    queue = [(i, 0) for i in sorted(cust, key=lambda k: cust[k], reverse=True) + cands if not fresh(i)]
    status = "ok"
    try:
        # 1) upward: заказчики и кандидаты → учредители до maxDepthUp
        while queue and st["requests"] < x.max:
            inn, depth = queue.pop(0)
            if inn in seen or fresh(inn): continue
            seen.add(inn)
            data = card(inn, st)
            if data is None: continue
            d = ownership_doc(inn, data, x.min_share, x.date)
            docs.append(d); st["up"] += 1
            if depth < x.max_depth:
                for f in founders(data, x.min_share):
                    if f["inn"] and f["inn"] not in seen and not fresh(f["inn"]): queue.append((f["inn"], depth + 1))
        # 2) downward: головные компании групп и их дочерние общества из карточки
        for hi, gname in heads:
            if st["requests"] >= x.max: break
            if fresh(hi) or hi in seen: continue
            seen.add(hi)
            data = card(hi, st)
            if data is None: continue
            docs.append(ownership_doc(hi, data, x.min_share, x.date)); st["down"] += 1
            for ch in (data.get("СвязУчред") or [])[:200]:
                if not isinstance(ch, dict): continue                       # в ответе бывают только ОГРН-строки — без ИНН не сохраняем
                ci = str(ch.get("ИНН") or "")
                if not re.fullmatch(r"\d{10}", ci) or ci in seen or fresh(ci): continue
                seen.add(ci); st["children"] += 1
                docs.append({"inn": ci, "name": ch.get("НаимПолн") or ch.get("НаимСокр") or "", "status": ch.get("Статус") or "",
                             "okved": (ch.get("ОКВЭД") or {}).get("Наим", "")[:200] if isinstance(ch.get("ОКВЭД"), dict) else str(ch.get("ОКВЭД") or "")[:200],
                             "founders": [{"inn": hi, "name": gname, "share": None, "type": "головная компания"}], "group": gname, "groupShare": None,
                             "chain": [hi], "via": "checko linked", "checkedAt": x.date, "contacts": {},
                             "note": "доля не проверена: в списке участий API не отдаёт долю"})
    except Stop as e:
        status = "skipped" if "лимит" in str(e) else "failed"
        st["stopped"] = str(e)
    if queue and st["requests"] >= x.max and "stopped" not in st: st["stopped"] = "лимит запросов запуска"
    st["status"] = status
    st["docs"] = len(docs)
    st["partial"] = bool(queue) or bool(st.get("stopped")) or st["requests"] >= x.max
    json.dump({"docs": docs, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
