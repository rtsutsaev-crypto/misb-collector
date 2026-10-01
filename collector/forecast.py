"""forecast.py — прогноз повторных закупок обучения по ЕИС (ГосПлан).

Идея: многие заказчики покупают одно и то же обучение каждый год. Берём профильные закупки, опубликованные
год назад в окне [сегодня+ahead_from; сегодня+ahead_to] − 12 месяцев, и проверяем, не купил ли заказчик уже
снова (есть ли у него профильная закупка с похожим предметом за последние 10 месяцев). Если нет — лид-прогноз:
id "fc-<номер прошлогодней закупки>", flags ["plan", "forecast"], deadline "", ожидаемый месяц в note и expectedAt.

Запуск: GOSPLAN_KEY=... python3 forecast.py --dict dictionary.json --known known.json --state state.json
        --date ГГГГ-ММ-ДД --out out.json [--ahead-from 14 --ahead-to 90 --max-requests 700 --min-price 50000]
state.json (meta/forecast-state): {"done_until": "ГГГГ-ММ-ДД"} — до какой даты прошлого года окно уже разобрано.
out.json: {leads, stats, state}.
"""
import argparse, json, os, re, subprocess, sys, time, threading, urllib.parse, datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C
from gosplan_delta import REG, EIS

KEY = os.environ.get("GOSPLAN_KEY", "")
WORDS = ["обучение", "повышение квалификации", "переподготовка", "семинар", "тренинг", "вебинар", "конференции",
         "форум", "стратегической сессии", "оценка персонала", "образовательных услуг", "мастер-класс", "наставничеств", "коучинг"]
CLASSES = ["85.42", "85.41", "85.59", "85.60", "82.30"]
STOP = set("оказание услуг услуги услуга по на для в и с о об от за к по нужд нужды проведение проведению организации организация обучение обучению образовательных "
           "программе программам программы работников сотрудников года год году г".split())


LOCK = threading.Lock()


def get(path, params, st):
    url = "https://v2.gosplan.info" + path + "?" + urllib.parse.urlencode(dict(params, apikey=KEY), doseq=True)
    for t in range(5):
        r = subprocess.run(["curl", "-s", "-m", "120", "-w", "\n%{http_code}", url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        with LOCK: st["requests"] += 1
        if code == "200":
            try: return json.loads(body)
            except Exception: pass
        if code in ("401", "403"): raise SystemExit("ключ не принят")
        if code == "422": st["errors"] += 1; return None
        time.sleep(2 if code == "429" else 4)
    st["errors"] += 1
    return None


def words(t):
    return {w[:6] for w in re.findall(r"[а-яa-z]{4,}", C.norm_text(t)) if w not in STOP}


def main():
    a = argparse.ArgumentParser()
    for k in ("dict", "known", "state", "date", "out"): a.add_argument("--" + k)
    a.add_argument("--ahead-from", type=int, default=14); a.add_argument("--ahead-to", type=int, default=90)
    a.add_argument("--max-requests", type=int, default=700); a.add_argument("--min-price", type=float, default=100000)
    a.add_argument("--parallel", type=int, default=5)
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
    kj = json.load(open(x.known, encoding="utf-8")); known = kj.get("ids", {})
    state = json.load(open(x.state, encoding="utf-8")) if x.state and os.path.exists(x.state) else {}
    state = state.get("data", state)
    win_from = today + dt.timedelta(days=x.ahead_from) - dt.timedelta(days=365)
    win_to = today + dt.timedelta(days=x.ahead_to) - dt.timedelta(days=365)
    if state.get("done_until"):
        win_from = max(win_from, dt.date.fromisoformat(state["done_until"]))
    st = {"requests": 0, "errors": 0, "window": [win_from.isoformat(), win_to.isoformat()]}
    if win_from >= win_to:
        json.dump({"leads": [], "stats": dict(st, note="окно уже разобрано"), "state": state}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False); return
    # 1. прошлогодние профильные закупки в окне
    past = {}
    def fetch(law, q):
        dlkey = "collecting_finished_at" if law == "fz44" else "submission_close_at"
        got = []
        for skip in range(0, 1001, 100):
            if st["requests"] >= x.max_requests: break
            j = get(f"/{law}/purchases", dict(q, published_after=win_from.isoformat(), published_before=win_to.isoformat(), limit=100, skip=skip), st)
            if not isinstance(j, list) or not j: break
            for r in j:
                n = r.get("purchase_number")
                if not n or not m.classify(r.get("object_info", ""), r.get("okpd2") or ())[0]: continue
                inn = (r.get("customers") or [r.get("customer") or ""])[0] if law == "fz44" else (r.get("customer") or "")
                if not inn: continue
                got.append({"num": n, "law": law, "title": r.get("object_info", ""), "inn": inn, "price": r.get("max_price") or 0,
                            "published": (r.get("published_at") or "")[:10], "deadline": (r.get(dlkey) or "")[:10],
                            "region": REG.get(int(r.get("region") or 0), ""), "okpd2": r.get("okpd2") or []})
            if len(j) < 100: break
        return got
    tasks = [(law, q) for law in ("fz44", "fz223") for q in [{"object_info": w} for w in WORDS] + [{"classifier": c} for c in CLASSES]]
    with ThreadPoolExecutor(x.parallel) as ex:
        for got in ex.map(lambda t: fetch(*t), tasks):
            for p in got: past.setdefault(p["num"], p)
    st["pastFound"] = len(past)
    # схлопнуть: один прогноз на (заказчик, похожий предмет); крупнейшая закупка
    groups = {}
    for p in sorted(past.values(), key=lambda p: -(p["price"] or 0)):
        if p["price"] and p["price"] < x.min_price: continue
        key = p["inn"]; ws = words(p["title"])
        g = next((g for g in groups.get(key, []) if len(ws & g["ws"]) >= max(1, min(len(ws), len(g["ws"])) // 3)), None)
        if g: g["items"].append(p); g["ws"] |= ws
        else: groups.setdefault(key, []).append({"ws": ws, "items": [p]})
    cands = sorted(((inn, g) for inn, gs in groups.items() for g in gs), key=lambda c: -(c[1]["items"][0]["price"] or 0))
    st["candidates"] = len(cands)
    # 2. купил ли заказчик уже снова (профильная закупка с похожим предметом за последние ~10 месяцев)
    recent_after = (today - dt.timedelta(days=300)).isoformat()
    cands = [(inn, g) for inn, g in cands if ("fc-" + g["items"][0]["num"]) not in known]
    def recent_of(inn):
        if st["requests"] >= x.max_requests: return None
        out = []
        for law in ("fz44", "fz223"):
            j = get(f"/{law}/purchases", {"customer": inn, "published_after": recent_after, "limit": 100, "sort": "published_at_desc"}, st)
            if j is None: return None
            out += [r.get("object_info", "") for r in j if m.classify(r.get("object_info", ""), r.get("okpd2") or ())[0]]
        return out
    inns = list(dict.fromkeys(inn for inn, _ in cands))
    with ThreadPoolExecutor(x.parallel) as ex:
        cache = dict(zip(inns, ex.map(recent_of, inns)))
    leads = []
    for inn, g in cands:
        if cache.get(inn) is None: st["stopped"] = "лимит запросов"; continue
        p0 = g["items"][0]
        if any(len(words(t) & g["ws"]) >= max(1, len(g["ws"]) // 3) for t in cache[inn]):
            continue          # уже купил снова в этом году — не прогноз
        exp = dt.date.fromisoformat(p0["published"]) + dt.timedelta(days=365)
        l = C.build_lead({"id": "fc-" + p0["num"], "title": "Ожидается повтор: " + p0["title"], "customer": "ИНН " + inn, "customerInn": inn,
                          "region": p0["region"], "price": p0["price"] or None, "deadline": "", "url": EIS + p0["num"],
                          "law": "44-ФЗ" if p0["law"] == "fz44" else "223-ФЗ"}, m, "gosplan:forecast", x.date)
        l["flags"] = sorted(set(l["flags"]) | {"plan", "forecast"})
        l["expectedAt"] = exp.isoformat()
        others = ", ".join(f"{q['num']} ({q['published']})" for q in g["items"][1:4])
        l["note"] = (f"Прогноз повторной закупки: в {p0['published'][:7]} заказчик закупал «{p0['title'][:160]}» (№ {p0['num']}, НМЦ {int(p0['price'] or 0):,} ₽"
                     .replace(",", " ") + (f"; ещё {others}" if others else "") + f"). В {today.year}–{exp.year} похожей закупки у заказчика нет; "
                     f"ожидаемо около {exp.isoformat()[:7]}. Не извещение: связаться с заказчиком заранее.")
        leads.append(l)
    done_to = win_to if not st.get("stopped") else win_from   # не дошли до конца — окно разберётся заново в следующий запуск (уже созданные прогнозы отсеются по known)
    out = {"leads": leads, "stats": dict(st, forecasts=len(leads)), "state": {"done_until": done_to.isoformat(), "updatedAt": x.date}}
    json.dump(out, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(out["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
