"""source_report.py — еженедельная таблица по источникам и предложения по режиму чтения (политика жизненного цикла источников, план роста MISSING-4).

Запуск раз в 7 дней: python3 source_report.py --runs <папка документов коллекции runs> --date ГГГГ-ММ-ДД --out report.json [--plan sources-plan.json]
По каждому источнику за 7 и 28 дней: запусков, свежих лидов, с открытым приёмом, отказов (failed/partial), вызовов и минут (если есть durationSec/requests). Правила (только предложения, решает человек):
  • 4 недели подряд 0 свежих при ≥ 8 запусках → «читать раз в 30 дней (refreshDays 30)»; 
  • 8 недель 0 свежих → «отключить (type skip, prevType сохранить)»; (пока истории меньше — не предлагается);
  • failed в ≥ 70 % запусков за 28 дней → «задача доступа: не читать ежедневно»;
  • источник с type skip или refreshDays, у которого за 7 дней свежих > 0 при ≥ 2 запусках → «вернуть в ежедневный круг».
Результат — документ meta/source-report и строка в log: сколько предложений. Исключение: сезонные источники (планы 223-ФЗ в декабре) — решение человека.
"""
import argparse, collections, datetime as dt, glob, json, os


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--runs", required=True); a.add_argument("--date", required=True); a.add_argument("--out", required=True); a.add_argument("--plan")
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    plan = {}
    if x.plan and os.path.exists(x.plan):
        ps = json.load(open(x.plan, encoding="utf-8")).get("sources", [])
        plan = {s["key"]: s for s in ps}
        plan.update({s.get("name"): s for s in ps if s.get("name")})   # в старых runs у источника только название
    rows = collections.defaultdict(lambda: {"w7": collections.Counter(), "w28": collections.Counter(), "min28": 0.0, "first": None})
    nruns = set()
    for f in glob.glob(os.path.join(x.runs, "**", "*.json"), recursive=True):
        d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
        day = str(d.get("startedAt") or "")[:10]
        if len(day) < 10: continue
        age = (today - dt.date.fromisoformat(day)).days
        if age > 28 or age < 0: continue
        nruns.add(d.get("runId"))
        for s in d.get("sources") or []:
            k = s.get("key") or s.get("name")
            for w, lim in (("w7", 7), ("w28", 28)):
                if age > lim: continue
                c = rows[k][w]
                if s.get("status") == "skipped": c["skipped"] += 1; continue
                c["runs"] += 1; c["fresh"] += s.get("fresh") or 0; c["found"] += s.get("found") or 0
                c["failed"] += s.get("status") == "failed"; c["partial"] += s.get("status") == "partial"
                c["requests"] += s.get("requests") or 0
            if age <= 28: rows[k]["min28"] += (s.get("durationSec") or 0) / 60
            rows[k]["name"] = s.get("name") or k
    out = []
    for k, r in sorted(rows.items(), key=lambda kv: -kv[1]["w28"]["fresh"]):
        w7, w28 = r["w7"], r["w28"]
        sug = ""
        p = plan.get(k, {})
        if w28["runs"] >= 8 and w28["fresh"] == 0 and not p.get("refreshDays") and p.get("type") != "skip": sug = "0 свежих за 28 дней: читать раз в 30 дней (refreshDays 30)"
        elif w28["runs"] >= 5 and w28["failed"] / max(w28["runs"], 1) >= 0.7: sug = "отказы в ≥ 70 % запусков: задача доступа, не читать ежедневно"
        elif (p.get("type") == "skip" or p.get("refreshDays")) and w7["fresh"] > 0 and w7["runs"] >= 2: sug = "свежие лиды за 7 дней при редком чтении: вернуть в ежедневный круг"
        out.append({"key": k, "name": r.get("name", k), "runs7": w7["runs"], "fresh7": w7["fresh"], "runs28": w28["runs"], "fresh28": w28["fresh"], "found28": w28["found"],
                    "failed28": w28["failed"], "partial28": w28["partial"], "requests28": w28["requests"], "minutes28": round(r["min28"], 1), "suggest": sug})
    doc = {"date": x.date, "runs28": len(nruns), "sources": out, "note": "Предложения только для человека: режим источника в sources-plan.json меняется отдельным коммитом."}
    json.dump(doc, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = sum(1 for o in out if o["suggest"])
    print(f"запусков за 28 дней {len(nruns)}, источников {len(out)}, предложений {n}")
    for o in out:
        if o["suggest"]: print(f"  {o['key']}: {o['suggest']} (запусков {o['runs28']}, свежих {o['fresh28']})")


if __name__ == "__main__":
    main()
