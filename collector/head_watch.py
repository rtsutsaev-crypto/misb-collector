"""head_watch.py — смена руководителя у заказчиков (ЕГРЮЛ через DaData): ранний сигнал «новый директор».

Новый руководитель в первые 90 дней обычно заказывает стратегическую сессию, оценку команды, пересматривает бюджет обучения.
Скрипт перепроверяет заказчиков из коллекции customers (сначала из watchlist — холдинги, дочки, повторные покупатели обучения),
у которых сведения старше --recheck-days, и сравнивает руководителя с прежним. Изменился (или по ЕГРЮЛ назначен не раньше
чем --fresh-days назад, а прежнего значения не было) — сигнал sig-head-<ИНН>-<дата> (source sig-head, флаг early).

Запуск: DADATA_KEY=... python3 head_watch.py --customers <папка customers> --watch watch.json --date ГГГГ-ММ-ДД
        --out head_out.json [--max 800] [--recheck-days 30] [--fresh-days 90] [--watch-only]
Выход: {customers: {ИНН: обновлённый документ (prevHead, headChangedAt)}, leads, stats}. Документы customers — set с if_version.
Ключ DaData только из окружения. Бесплатный тариф — 10 000 запросов в сутки; --max держит запуск в лимите.
"""
import argparse, datetime as dt, glob, json, os, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dadata


def norm(s):
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--customers", required=True); a.add_argument("--watch"); a.add_argument("--date", required=True)
    a.add_argument("--out", required=True); a.add_argument("--max", type=int, default=800)
    a.add_argument("--recheck-days", type=int, default=30); a.add_argument("--fresh-days", type=int, default=90)
    a.add_argument("--watch-only", action="store_true"); a.add_argument("--parallel", type=int, default=4)
    x = a.parse_args()
    if not dadata.KEY: sys.exit("нет DADATA_KEY")
    today = dt.date.fromisoformat(x.date)
    old = (today - dt.timedelta(days=x.recheck_days)).isoformat()
    fresh = (today - dt.timedelta(days=x.fresh_days)).isoformat()
    watch = {}
    if x.watch and os.path.exists(x.watch):
        w = json.load(open(x.watch, encoding="utf-8")); w = w.get("data", w); watch = w.get("inns", w)
    docs = {}
    for f in glob.glob(os.path.join(x.customers, "**", "*.json"), recursive=True):
        try: j = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        j = j.get("data", j) if isinstance(j, dict) else {}
        inn = str(j.get("inn") or os.path.basename(f)[:-5])
        if len(inn) != 10 or not j.get("name") or (j.get("status") and j["status"] != "ACTIVE"): continue
        if (j.get("checkedAt") or "") > old: continue
        if x.watch_only and inn not in watch: continue
        docs[inn] = j
    order = sorted(docs, key=lambda i: (i not in watch, docs[i].get("checkedAt") or ""))[: x.max]
    st = {"candidates": len(docs), "requested": 0, "changed": 0, "fresh": 0, "errors": 0}
    out, leads = {}, []
    with ThreadPoolExecutor(x.parallel) as ex:
        for inn, doc, fatal in ex.map(lambda i: dadata.lookup(i, x.date), order):
            st["requested"] += 1
            if "error" in doc:
                st["errors"] += 1; st.setdefault("lastError", doc["error"])
                if fatal: st["stopped"] = doc["error"]; break
                continue
            if not doc.get("name"): continue
            prev = docs[inn]
            ph, nh = prev.get("head") or "", doc.get("head") or ""
            new = dict(prev, **{k: v for k, v in doc.items() if v not in (None, "", {})})
            changed = bool(ph and nh and norm(ph) != norm(nh))
            since = doc.get("headSince") or ""
            recent = bool(nh and since and since >= fresh)
            if changed:
                new["prevHead"] = ph; new["headChangedAt"] = x.date; st["changed"] += 1
            out[inn] = new
            if changed or (recent and not prev.get("headSince") and inn in watch):   # «назначен недавно» без прежнего значения — только watchlist
                if recent and not changed: st["fresh"] += 1
                w = watch.get(inn) or {}
                when = since if recent else x.date
                leads.append({"id": f"sig-head-{inn}-{when}", "title": f"Новый руководитель: {nh}" + (f" ({doc.get('headPost')})" if doc.get("headPost") else ""),
                              "customer": doc["name"], "customerInn": inn, "region": doc.get("region") or "", "price": None, "deadline": "",
                              "validUntil": (dt.date.fromisoformat(when) + dt.timedelta(days=x.fresh_days)).isoformat(), "law": "",
                              "url": f"https://egrul.nalog.ru/index.html?query={inn}", "source": "sig-head", "collectedAt": x.date,
                              "country": "RU", "currency": "RUB", "flags": sorted({"early"} | ({"watch"} if w else set()) | ({"tek"} if w.get("group") else set())),
                              "note": (f"Смена руководителя по ЕГРЮЛ: было «{ph}», стало «{nh}»" if changed else f"Руководитель «{nh}» назначен {since}")
                                      + ". В первые 90 дней новый руководитель обычно заказывает стратсессию и оценку команды."
                                      + (f" Группа: {w['group']}." if w.get("group") else "")})
    st["leads"] = len(leads)
    json.dump({"customers": out, "leads": leads, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
