"""llm_recheck.py — второй проход по лидам, которые фильтр отсёк как «нет формы обучения»: модель смотрит названия и возвращает подходящие по смыслу.

  prepare: python3 llm_recheck.py prepare --leadsets <папка leadsets> --verdicts <meta/collector-verdicts.json> [--accepted <meta/llm-accept.json>] --date ГГГГ-ММ-ДД --out cand.json [--max 100] [--days 3]
           → cand.json: [{id, key, title, customer, price, deadline}] — лиды последних --days дней с открытым приёмом или без срока, отсечённые по «нет формы обучения», которых ещё не проверяли.
  apply:   python3 llm_recheck.py apply --answers answers.json [--accepted <meta/llm-accept.json>] --date ГГГГ-ММ-ДД --out llm-accept.json
           answers.json: [{"id": "...", "ok": true|false, "why": "…"}]; в llm-accept.json попадают только ok (не больше 3000 записей; старые выпадают первыми).
Принятые модель лиды не удаляются из базы и не получают чужих флагов: collector.py verdicts --accepted убирает их из «исключено по профилю», priority.py --accepted поднимает их в ярус «ядро-общ».
Ограничение для модели: подходит — если по названию это обучение, развитие, оценка персонала, деловое или корпоративное мероприятие, консалтинг по персоналу и управлению; не подходит — поставки, ремонт, питание, аренда, ИТ-услуги, медицина, спорт, культура, праздники.
"""
import argparse, glob, json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C


def load(path):
    if not path or not os.path.exists(path): return None
    j = json.load(open(path, encoding="utf-8")); return j.get("data", j)


def main():
    a = argparse.ArgumentParser(); a.add_argument("cmd", choices=["prepare", "apply"])
    a.add_argument("--leadsets"); a.add_argument("--verdicts"); a.add_argument("--accepted"); a.add_argument("--answers")
    a.add_argument("--date", required=True); a.add_argument("--out", required=True); a.add_argument("--max", type=int, default=100); a.add_argument("--days", type=int, default=3)
    x = a.parse_args()
    acc = (load(x.accepted) or {}).get("accepted", {})
    if x.cmd == "prepare":
        ex = (load(x.verdicts) or {}).get("excluded", {})
        today = dt.date.fromisoformat(x.date); lo = (today - dt.timedelta(days=x.days)).isoformat()
        cand, seen = [], set()
        for f in glob.glob(os.path.join(x.leadsets, "*.json")):
            d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
            for l in d.get("leads", []):
                i = str(l.get("id")); k = C.site_key(i)
                if k in seen or k not in ex or not str(ex[k]).startswith("нет формы") or k in acc: continue
                if (l.get("collectedAt") or "")[:10] < lo: continue
                dl = l.get("deadline") or ""
                if dl and dl < x.date: continue
                seen.add(k)
                cand.append({"id": i, "key": k, "title": (l.get("title") or "")[:240], "customer": (l.get("customer") or "")[:80], "price": l.get("price"), "deadline": dl})
        cand.sort(key=lambda c: (c["deadline"] == "", c["deadline"]))
        json.dump(cand[: x.max], open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
        print(json.dumps({"candidates": len(cand), "written": min(len(cand), x.max)}, ensure_ascii=False)); return
    ans = json.load(open(x.answers, encoding="utf-8"))
    for r in ans:
        if r.get("ok"): acc[C.site_key(str(r["id"]))] = {"why": (r.get("why") or "")[:120], "at": x.date}
    keep = dict(list(acc.items())[-3000:])
    json.dump({"updatedAt": x.date, "accepted": keep, "note": "Лиды, принятые вторым проходом модели (llm_recheck.py)."}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"answers": len(ans), "accepted_now": sum(1 for r in ans if r.get("ok")), "total": len(keep)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
