"""contracts_build.py — из выхода contracts.py готовит записи для сайта.

Запуск: python3 contracts_build.py --in contracts_out.json --customers customers_dir --names names.json --watch watch.json
        --date ГГГГ-ММ-ДД --outdir pub [--top 300] [--watch-min 3]
Берутся только контракты, где предмет прямо про обучение/консалтинг (why начинается с «форма»): одни коды ОКПД2 дают
школьных преподавателей и досуговые кружки. Выход в outdir:
  winners/<ИНН>.json — исполнители (первые top по сумме): {inn, name, wins, customers, direct, sum, lastDate, topics, via}
  contract-buyers.json — meta/contract-buyers: {from, to, total, updatedAt, buyers: [первые top по числу контрактов]}
  watch_add.json — {ИНН: {level: "покупатель по контрактам"}} для заказчиков с ≥ watch-min контрактами, которых нет в watchlist
  need_names.json — ИНН без названия (для dadata.py)
  gph_buyers.json — заказчики, которые сами нанимают преподавателей-физлиц (≥ gph-min договоров на обучение с ИНН из 12 цифр):
                    {ИНН: {level: "нанимает преподавателей напрямую", gph: N}} — добавляются в watchlist рядом с watch_add.json.
                    ИНН и имена физлиц никуда не пишутся (персональные данные) — только число договоров у заказчика.
names.json — {ИНН: {name}} (выход dadata.py, поле customers); customers_dir — документы коллекции customers.
"""
import argparse, glob, json, os


def main():
    a = argparse.ArgumentParser()
    for k in ("in", "customers", "names", "watch", "date", "outdir"): a.add_argument("--" + k)
    a.add_argument("--gph-min", type=int, default=3)
    a.add_argument("--top", type=int, default=300); a.add_argument("--top-buyers", type=int, default=250); a.add_argument("--watch-min", type=int, default=3)
    x = a.parse_args()
    d = json.load(open(x.__dict__["in"], encoding="utf-8"))
    # только предмет про обучение/консалтинг и не наём физлиц-преподавателей (все исполнители — ИНН из 12 цифр)
    C = [c for c in d["contracts"] if c["why"].startswith("форма") and not (c["suppliers"] and all(len(s) == 12 for s in c["suppliers"]))]
    gph = {}
    for c in d["contracts"]:
        if c["why"].startswith("форма") and c["suppliers"] and all(len(s) == 12 for s in c["suppliers"]) and len(c["customerInn"]) == 10:
            gph[c["customerInn"]] = gph.get(c["customerInn"], 0) + 1
    names = {}
    if x.customers:
        for f in glob.glob(os.path.join(x.customers, "*.json")):
            try: j = json.load(open(f, encoding="utf-8"))
            except Exception: continue
            if not isinstance(j, dict): continue
            j = j.get("data", j)
            if j.get("name"): names[j.get("inn") or os.path.basename(f)[:-5]] = j["name"]
    if x.names and os.path.exists(x.names):
        n = json.load(open(x.names, encoding="utf-8")); n = n.get("customers", n)
        for k, v in n.items():
            if v.get("name"): names[k] = v["name"]
    buyers, sups = {}, {}
    for c in sorted(C, key=lambda c: c["publishedAt"]):
        b = buyers.setdefault(c["customerInn"], {"inn": c["customerInn"], "contracts": 0, "direct": 0, "sum": 0.0, "lastDate": "",
                                                 "nextEnd": "", "region": c["region"], "subjects": [], "sup": {}})
        b["contracts"] += 1; b["direct"] += c["direct"]; b["sum"] += c["price"] or 0; b["lastDate"] = c["publishedAt"]
        if c["exeEnd"] >= x.date and (not b["nextEnd"] or c["exeEnd"] < b["nextEnd"]): b["nextEnd"] = c["exeEnd"]
        b["subjects"] = (b["subjects"] + [c["subject"][:110]])[-2:]
        for s in c["suppliers"]:
            b["sup"][s] = b["sup"].get(s, 0) + 1
            q = sups.setdefault(s, {"inn": s, "wins": 0, "direct": 0, "sum": 0.0, "lastDate": "", "cust": set(), "topics": []})
            q["wins"] += 1; q["direct"] += c["direct"]; q["sum"] += c["price"] or 0; q["lastDate"] = c["publishedAt"]
            q["cust"].add(c["customerInn"]); q["topics"] = (q["topics"] + [c["subject"][:160]])[-3:]
    topS = sorted(sups.values(), key=lambda s: -s["sum"])[:x.top]
    topB = sorted(buyers.values(), key=lambda b: (-b["contracts"], -b["sum"]))[:x.top_buyers]
    need = sorted({s["inn"] for s in topS} | {b["inn"] for b in topB} - set(names))
    os.makedirs(os.path.join(x.outdir, "winners"), exist_ok=True)
    for s in topS:
        doc = {"inn": s["inn"], "name": names.get(s["inn"], ""), "wins": s["wins"], "customers": len(s["cust"]), "direct": s["direct"],
               "sum": round(s["sum"], 2), "lastDate": s["lastDate"], "topics": s["topics"], "via": "contracts", "updatedAt": x.date}
        json.dump(doc, open(os.path.join(x.outdir, "winners", s["inn"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    rows = []
    for b in topB:
        top_sup = max(b["sup"].items(), key=lambda t: t[1])[0] if b["sup"] else ""
        rows.append({"inn": b["inn"], "name": names.get(b["inn"], ""), "contracts": b["contracts"], "direct": b["direct"],
                     "sum": round(b["sum"], 2), "lastDate": b["lastDate"], "nextEnd": b["nextEnd"], "region": b["region"],
                     "subjects": b["subjects"], "topSupplier": top_sup})
    st = d.get("stats", {})
    json.dump({"from": st.get("from", ""), "to": st.get("to", ""), "total": len(buyers), "suppliersTotal": len(sups), "contracts": len(C),
               "direct": sum(c["direct"] for c in C), "updatedAt": x.date, "buyers": rows},
              open(os.path.join(x.outdir, "contract-buyers.json"), "w", encoding="utf-8"), ensure_ascii=False)
    watch = {}
    if x.watch and os.path.exists(x.watch):
        w = json.load(open(x.watch, encoding="utf-8")); w = w.get("data", w); watch = w.get("inns", w)
    add = {b["inn"]: {"level": "покупатель по контрактам"} for b in buyers.values()
           if b["contracts"] >= x.watch_min and b["inn"] not in watch and len(b["inn"]) == 10}
    json.dump(add, open(os.path.join(x.outdir, "watch_add.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(need, open(os.path.join(x.outdir, "need_names.json"), "w", encoding="utf-8"))
    gb = {i: {"level": "нанимает преподавателей напрямую", "gph": n} for i, n in gph.items() if n >= x.gph_min and i not in watch and i not in add}
    json.dump(gb, open(os.path.join(x.outdir, "gph_buyers.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"contracts": len(C), "buyers": len(buyers), "suppliers": len(sups), "winnersDocs": len(topS),
                      "buyersShown": len(rows), "watchAdd": len(add), "needNames": len(need), "gphBuyers": len(gb)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
