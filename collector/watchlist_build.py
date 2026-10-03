"""watchlist_build.py — пересборка уровня «повторный покупатель» в meta/watchlist: ≥ 2 релевантных лида заказчика по текущему словарю.

Запуск: python3 watchlist_build.py --watchlist <meta/watchlist.json> --leadsets <папка leadsets> --dict dictionary.json --date ГГГГ-ММ-ДД --out watchlist.json
Релевантный лид — прошёл Matcher.classify (прогнозы gosplan:forecast не считаются). Заказчики с ложным уровнем (без релевантных лидов) удаляются, новые добавляются;
остальные уровни (головная, дочка, внучка, компания группы, покупатель по контрактам) не меняются. Документ пишется компактно (≈ 180 КБ из лимита 240 КБ).
"""
import argparse, collections, glob, json

from collector import Matcher


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--watchlist", required=True); a.add_argument("--leadsets", required=True)
    a.add_argument("--dict", default="dictionary.json"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    x = a.parse_args()
    m = Matcher(json.load(open(x.dict, encoding="utf-8")))
    w = json.load(open(x.watchlist, encoding="utf-8")); w = w.get("data", w)
    cnt, seen = collections.Counter(), set()
    for f in glob.glob(x.leadsets.rstrip("/") + "/*.json"):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []):
            i = str(l.get("id"))
            if i in seen or str(l.get("source", "")).startswith("gosplan:forecast"): continue
            seen.add(i)
            if l.get("customerInn") and m.classify(l.get("title", ""), l.get("okpd2") or ())[0]:
                cnt[str(l["customerInn"])] += 1
    rep = {k for k, v in cnt.items() if v >= 2}
    drop = [k for k, v in w["inns"].items() if v.get("level") == "повторный покупатель" and k not in rep]
    add = [k for k in rep if k not in w["inns"]]
    for k in drop: w["inns"].pop(k)
    for k in add: w["inns"][k] = {"level": "повторный покупатель"}
    w["updatedAt"] = x.date
    w["note"] = w.get("note", "") + f" | {x.date}: «повторный покупатель» пересобран (≥ 2 релевантных лида): −{len(drop)}, +{len(add)}."
    json.dump(w, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"убрано {len(drop)}, добавлено {len(add)}, всего {len(w['inns'])}")


if __name__ == "__main__":
    main()
