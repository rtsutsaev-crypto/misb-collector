"""forecast2.py — прогноз повторной закупки по дате окончания действующего контракта на обучение (реестр контрактов, meta/contract-buyers).

Запуск: python3 forecast2.py --buyers contract-buyers.json --dict dictionary.json --known known.json --date ГГГГ-ММ-ДД --out out.json [--from-days 14] [--to-days 120]
Без запросов к API. Покупатель с nextEnd (дата окончания ближайшего контракта) в окне [сегодня + from-days; сегодня + to-days] даёт лид fc2-<ИНН>-<nextEnd>:
«Ожидается повтор: контракт на … заканчивается ДД.ММ.ГГГГ»; expectedAt = nextEnd − 30 дней, validUntil = expectedAt + 30 (сайт считает прогноз открытым до validUntil);
флаги plan, forecast. У заказчика, который в основном заключает прямые договоры (доля ≥ 90 %), в note: «обычно прямой договор, извещения не будет: нужен контакт заранее».
Не берутся образовательные организации, службы занятости (покупают лекторов почасово) и медицинские учреждения и контракты, предмет которых не проходит словарь. Лид не создаётся повторно (known.json: id).
"""
import argparse, datetime as dt, json, re

import collector as C
from forecast import REG

EDU = re.compile(r"фбуз|фкуз|(?<!\w)гуз|бузоо|стоматолог|поликлиник|больниц|гигиен|эпидемиолог|мсч|(?<!\w)окб|цзн|гку|гбу|гау|гбоу|гаоу|мбоу|мбдоу|школ|гимнази|лице[йя]|колледж|техникум|университет|институт|академи|(?<!\w)вуз|дпо|ггту|(?<!\w)(гту|гсу|мгту|спбгу)|кадров\w* центр|центр\w* (занятост|подготовк|оценк|компетенц|дополнительн)|(?<!\w)цок|учебн|образован|управление образован", re.I)


def main():
    a = argparse.ArgumentParser()
    for k in ("buyers", "dict", "known", "date", "out"): a.add_argument("--" + k, required=k != "known")
    a.add_argument("--from-days", type=int, default=14); a.add_argument("--to-days", type=int, default=120)
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    j = json.load(open(x.buyers, encoding="utf-8")); j = j.get("data", j)
    m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
    known = json.load(open(x.known, encoding="utf-8")).get("ids", {}) if x.known else {}
    lo, hi = today + dt.timedelta(days=x.from_days), today + dt.timedelta(days=x.to_days)
    leads, st = [], {"buyers": len(j.get("buyers", [])), "inWindow": 0, "skippedEdu": 0, "skippedSubject": 0, "known": 0}
    for b in j.get("buyers", []):
        try: end = dt.date.fromisoformat(b.get("nextEnd") or "")
        except ValueError: continue
        if not (lo <= end <= hi): continue
        st["inWindow"] += 1
        if EDU.search(b.get("name") or ""): st["skippedEdu"] += 1; continue
        subj = next((s for s in b.get("subjects", []) if m.classify(s, ())[0]), "")
        if not subj: st["skippedSubject"] += 1; continue
        lid = f"fc2-{b['inn']}-{end.isoformat()}"
        if lid in known: st["known"] += 1; continue
        exp = end - dt.timedelta(days=30)
        direct = b.get("contracts") and b.get("direct", 0) / b["contracts"] >= 0.9
        l = C.build_lead({"id": lid, "title": f"Ожидается повтор: контракт на обучение заканчивается {end.strftime('%d.%m.%Y')} — {subj[:160]}", "customer": b.get("name") or "",
                          "customerInn": b["inn"], "region": REG.get(int(b.get("region") or 0), ""), "price": None, "deadline": "",
                          "url": "https://zakupki.gov.ru/epz/contract/search/results.html?searchString=" + b["inn"], "law": ""}, m, "gosplan:forecast", x.date)
        l["flags"] = sorted(set(l["flags"]) | {"plan", "forecast"})
        l["expectedAt"] = exp.isoformat(); l["validUntil"] = (exp + dt.timedelta(days=30)).isoformat()
        l["note"] = (f"Прогноз по реестру контрактов: у заказчика {b.get('contracts')} профильных контрактов за 12 месяцев на {int(b.get('sum') or 0):,} ₽ (прямых {b.get('direct', 0)}); ".replace(",", " ")
                     + f"ближайший заканчивается {end.isoformat()}. "
                     + ("Обычно прямой договор: извещения не будет, нужен контакт заранее. " if direct else "")
                     + "Не извещение: связаться с заказчиком заранее.")
        leads.append(l)
    json.dump({"leads": leads, "stats": dict(st, forecasts=len(leads))}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(dict(st, forecasts=len(leads)), ensure_ascii=False))


if __name__ == "__main__":
    main()
