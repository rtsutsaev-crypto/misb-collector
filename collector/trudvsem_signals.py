"""trudvsem_signals.py — ранние сигналы по вакансиям «Работы России» (открытый API opendata.trudvsem.ru, без ключа).

Работодатель ищет специалиста или руководителя по обучению персонала, руководителя учебного центра или корпоративного
университета — через 2–4 месяца он закупает программы и провайдеров. Вакансия с ИНН работодателя → сигнал sig-vac-<id>
(source sig-vacancy, флаг early, validUntil = сегодня + 90 дней). Кадровые агентства (hr-agency), школы, детсады и
должности не про обучение отбрасываются; поиск API нечёткий, поэтому отбор — по названию должности.

Запуск: python3 trudvsem_signals.py --known known.json --date ГГГГ-ММ-ДД --out vac_out.json [--days 3] [--max-pages 15]
API медленный (10–15 с на запрос): фильтр modifiedFrom = сегодня − days сужает выдачу; полный сбор — --days 3, первый запуск — 14.
known.json — тот же файл известных номеров, что у collector.py (поле ids): уже записанные сигналы не повторяются.
Выход: {leads, stats}. Контактное лицо (ФИО) не сохраняется — только адрес и телефон организации.
"""
import argparse, datetime as dt, json, re, time, urllib.parse, urllib.request

API = "https://opendata.trudvsem.ru/api/v1/vacancies"
QUERIES = ["обучению персонала", "развитию персонала", "учебного центра", "корпоративного университета", "бизнес-тренер"]
JOB = re.compile(r"(обучени|развити|подготовк)\w* (и \w+ )?(персонал|кадр|сотрудник|работник|руководител)|"
                 r"(отдел|служб|сектор|групп|управлени|центр)\w* (\w+ ){0,2}(обучени|развити\w* персонал|подготовк\w* (персонал|кадр))|"
                 r"учебн\w* (центр|комбинат|пункт)|корпоративн\w* (университет|академи)|бизнес-тренер|тренинг-менеджер|"
                 r"(менеджер|специалист|руководител|директор)\w* по (обучени|развити\w* (персонал|компетенц|кадр))|"
                 r"методолог\w* (по )?обучени|l&d|learning", re.I)
NOT_JOB = re.compile(r"учител|воспитател|педагог дополнительн|преподавател|музыкальн|логопед|тренер по (фитнес|плавани|футбол|спорт)|"
                     r"инструктор по (физ|плавани|вождени)|водител|слесар|кочегар|санитар|повар|продав|кассир", re.I)
NOT_ORG = re.compile(r"школ|детск\w* сад|мбдоу|мдоу|гимнази|лице[йя]|колледж|техникум|университет|институт|академи\w* (наук|образовани)|"
                     r"спортивн|дюсш|кадров\w* агентств|рекрут|персонал-сервис|аутстаффинг|(?<![а-я])(ано|чоу|оу) дпо|дополнительн\w* профессиональн\w* образовани|образовательн\w* (организац|учрежден|центр)", re.I)   # провайдеры обучения — конкуренты


def get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    for t in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(2 + 3 * t)
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--known"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--days", type=int, default=3); a.add_argument("--max-pages", type=int, default=15)
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    since = (today - dt.timedelta(days=x.days)).isoformat()
    known = set()
    if x.known:
        k = json.load(open(x.known, encoding="utf-8")); known = set((k.get("ids") or {}).keys())
    st = {"requests": 0, "rows": 0, "fresh": 0, "job": 0, "leads": 0, "agency": 0}
    seen, leads = set(), []
    for q in QUERIES:
        for page in range(x.max_pages):
            j = get({"text": q, "limit": 100, "offset": page, "modifiedFrom": since + "T00:00:00Z"}); st["requests"] += 1
            vs = ((j or {}).get("results") or {}).get("vacancies") or []
            if not vs: break
            last = len(vs) < 100
            for w in vs:
                v = w.get("vacancy") or {}
                vid = v.get("id")
                if not vid or vid in seen: continue
                seen.add(vid); st["rows"] += 1
                if (v.get("creation-date") or "") < since: continue
                st["fresh"] += 1
                name, comp = v.get("job-name") or "", v.get("company") or {}
                if not JOB.search(name) or NOT_JOB.search(name): continue
                st["job"] += 1
                if comp.get("hr-agency") or NOT_ORG.search(comp.get("name") or ""): st["agency"] += 1; continue
                sid = "sig-vac-" + vid
                if sid in known: continue
                duty = re.sub(r"\s+", " ", (v.get("duty") or "")).strip()
                contacts = [c.get("contact_value") for c in (v.get("contact_list") or []) if c.get("contact_value")]
                if comp.get("email"): contacts.insert(0, comp["email"])
                leads.append({"id": sid, "title": f"Вакансия «{name.strip()[:150]}» — компания строит обучение персонала",
                              "customer": comp.get("name") or "", "customerInn": comp.get("inn") or "",
                              "region": (v.get("region") or {}).get("name") or "", "price": None, "deadline": "",
                              "validUntil": (today + dt.timedelta(days=90)).isoformat(), "law": "", "url": v.get("vac_url") or "",
                              "source": "sig-vacancy", "collectedAt": x.date, "country": "RU", "currency": "RUB", "flags": ["early"],
                              "publishedAt": v.get("creation-date") or "", "contacts": contacts[:3],
                              "note": f"Вакансия от {v.get('creation-date')}: через 2–4 месяца после найма такие компании закупают программы и провайдеров. "
                                      + (f"Обязанности: {duty[:400]}" if duty else "")})
            time.sleep(0.3)
            if last: break
    st["leads"] = len(leads)
    json.dump({"leads": leads, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
