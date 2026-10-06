"""head_watch.py — смена руководителя у заказчиков (ЕГРЮЛ через DaData): ранний сигнал «новый директор».

Новый руководитель в первые 90 дней обычно заказывает стратегическую сессию, оценку команды, пересматривает бюджет обучения.
Проверяются заказчики из списков наблюдения (meta/watchlist — холдинги, дочки, повторные покупатели обучения;
meta/watchlist-extra — инфраструктура МСП и др.), у которых последняя проверка старше --recheck-days. Прежний руководитель
хранится в небольшом документе состояния (meta/head-state), коллекцию customers читать не нужно.
Изменился руководитель — сигнал sig-head-<ИНН>-<дата>; при первой проверке ИНН сигнал только если по ЕГРЮЛ руководитель
назначен не раньше чем --fresh-days назад и заказчик из основного watchlist с группой (холдинг, дочка).

Запуск: DADATA_KEY=... python3 head_watch.py --watch watch.json [watch_extra.json] --state hstate.json --date ГГГГ-ММ-ДД
        --out head_out.json [--max 400] [--recheck-days 30] [--fresh-days 90]
state (meta/head-state): {inns: {ИНН: "Фамилия И.О.|дата назначения|дата проверки"}, updatedAt} — компактно, чтобы ~3 000 ИНН
укладывались в документ до 240 КБ.
Выход: {leads, state, stats}. Ключ DaData только из окружения. Бесплатный тариф — 10 000 запросов в сутки.
"""
import argparse, datetime as dt, json, os, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dadata


def norm(s):
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def short(name):
    """«Иванов Иван Иванович» → «Иванов И.И.» (для сравнения и хранения хватает)."""
    p = str(name or "").split()
    return (p[0] + " " + "".join(x[0] + "." for x in p[1:3])).strip() if p else ""


def unpack(v):
    if isinstance(v, dict): return v
    h, s, c = (str(v or "") + "||").split("|")[:3]
    return {"h": h, "s": s, "c": c}


def load_watch(paths):
    main_, extra = {}, {}
    for n, p in enumerate(paths):
        if not p or not os.path.exists(p): continue
        w = json.load(open(p, encoding="utf-8")); w = w.get("data", w); w = w.get("inns", w)
        for k, v in w.items():
            if len(str(k)) == 10:
                (main_ if n == 0 else extra).setdefault(str(k), v)
    return main_, extra


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--watch", nargs="+", required=True); a.add_argument("--state"); a.add_argument("--date", required=True)
    a.add_argument("--out", required=True); a.add_argument("--max", type=int, default=400)
    a.add_argument("--recheck-days", type=int, default=30); a.add_argument("--fresh-days", type=int, default=90)
    a.add_argument("--parallel", type=int, default=4)
    x = a.parse_args()
    if not dadata.KEY: sys.exit("нет DADATA_KEY")
    today = dt.date.fromisoformat(x.date)
    old = (today - dt.timedelta(days=x.recheck_days)).isoformat()
    fresh = (today - dt.timedelta(days=x.fresh_days)).isoformat()
    watch, extra = load_watch(x.watch)
    state = {}
    if x.state and os.path.exists(x.state):
        s = json.load(open(x.state, encoding="utf-8")); s = s.get("data", s); state = {k: unpack(v) for k, v in s.get("inns", s).items()}
    allw = dict(extra); allw.update(watch)
    due = [i for i in allw if (state.get(i) or {}).get("c", "") <= old]
    # сначала перепроверки (только они ловят смену), основной watchlist раньше, затем новые ИНН; внутри — самые давние
    due.sort(key=lambda i: (i not in state, i not in watch, (state.get(i) or {}).get("c", "")))
    order = due[: x.max]
    st = {"watch": len(allw), "due": len(due), "requested": 0, "changed": 0, "fresh": 0, "errors": 0}
    leads = []
    with ThreadPoolExecutor(x.parallel) as ex:
        for inn, doc, fatal in ex.map(lambda i: dadata.lookup(i, x.date), order):
            st["requested"] += 1
            if "error" in doc:
                st["errors"] += 1; st.setdefault("lastError", doc["error"])
                if fatal: st["stopped"] = doc["error"]; break
                continue
            prev = state.get(inn) or {}
            nh, since = doc.get("head") or "", doc.get("headSince") or ""
            state[inn] = {"h": short(nh), "s": since, "c": x.date}
            if not doc.get("name") or (doc.get("status") and doc["status"] != "ACTIVE"): continue
            ph = prev.get("h") or ""
            changed = bool(ph and nh and norm(short(ph)) != norm(short(nh)))
            if changed and since and since < (today - dt.timedelta(days=365)).isoformat():
                changed = False                                   # по ЕГРЮЛ назначен давно — другое написание имени, не смена
            recent = bool(nh and since and since >= fresh)
            w0 = watch.get(inn) or {}
            if not (changed or (recent and not prev and w0.get("group"))): continue   # при первой проверке «недавно назначен» — только компании холдингов (не соцучреждения и органы власти)
            st["changed" if changed else "fresh"] += 1
            w = allw.get(inn) or {}
            when = since if recent else x.date
            leads.append({"id": f"sig-head-{inn}-{when}", "title": f"Новый руководитель: {nh}" + (f" ({doc.get('headPost')})" if doc.get("headPost") else ""),
                          "customer": doc["name"], "customerInn": inn, "region": doc.get("region") or "", "price": None, "deadline": "",
                          "validUntil": (dt.date.fromisoformat(when) + dt.timedelta(days=x.fresh_days)).isoformat(), "law": "",
                          "url": f"https://egrul.nalog.ru/index.html?query={inn}", "source": "sig-head", "collectedAt": x.date,
                          "country": "RU", "currency": "RUB",
                          "flags": sorted({"early"} | ({"watch"} if inn in watch else set()) | ({"tek"} if w.get("group") else set())),
                          "note": (f"Смена руководителя по ЕГРЮЛ: было «{ph}», стало «{nh}»" + (f" (с {since})" if since else "") if changed
                                   else f"Руководитель «{nh}» назначен {since}")
                                  + ". В первые 90 дней новый руководитель обычно заказывает стратсессию и оценку команды."
                                  + (f" Группа: {w['group']}." if w.get("group") else "")})
    st["leads"] = len(leads)
    state = {k: v for k, v in state.items() if k in allw}          # ИНН, выбывшие из списков наблюдения, не храним
    while len(json.dumps(state, ensure_ascii=False).encode()) > 225_000:   # документ до 240 КБ: сначала выбывают давние проверки
        for k in sorted(state, key=lambda k: state[k].get("c", ""))[: max(1, len(state) // 20)]: state.pop(k)
    json.dump({"leads": leads, "state": {"inns": {k: f"{v.get('h', '')}|{v.get('s', '')}|{v.get('c', '')}" for k, v in state.items()}, "updatedAt": x.date}, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
