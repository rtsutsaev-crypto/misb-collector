"""license_check.py — партнёрские лиды «победитель закупок обучения без лицензии на образовательную деятельность».

Исполнитель, который выигрывает контракты на обучение, семинары, стратсессии (коллекция winners, выход contracts_build.py),
но не имеет лицензии на образовательную деятельность, не может выдать удостоверение о повышении квалификации — ему нужен
лицензированный партнёр (сетевая форма, ст. 15 273-ФЗ): лицензия, документы и преподаватели МИСБ под его контракт.
Лицензии берутся из карточки компании Чекко (поле Лиценз, вид деятельности «образовательной деятельности»); реестр
Рособрнадзора из облака не открывается.

Запуск: CHECKO_KEY=... python3 license_check.py --winners <папка winners> --cache cache.json --date ГГГГ-ММ-ДД --out lic_out.json
        [--max 40] [--min-wins 2] [--recheck-days 180]
cache (meta/license-cache): {inns: {ИНН: {lic: true|false, num, checkedAt}}}. Выход: {leads, cache, stats}.
Лид: id sig-partner-<ИНН>, source sig-partner, флаг partner; повторно по тому же ИНН — не раньше, чем через recheck-days.
Ключ только из окружения. Бесплатный тариф Чекко — 100 запросов в сутки: --max держит запуск в лимите.
"""
import argparse, datetime as dt, glob, json, os, re, sys, time, urllib.parse, urllib.request

KEY = os.environ.get("CHECKO_KEY", "")
EDU = re.compile(r"(?i)образовательн")


def card(inn):
    url = "https://api.checko.ru/v2/company?" + urllib.parse.urlencode({"key": KEY, "inn": inn})
    for t in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(2 + 2 * t)
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--winners", required=True); a.add_argument("--cache"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--max", type=int, default=40); a.add_argument("--min-wins", type=int, default=2); a.add_argument("--recheck-days", type=int, default=180)
    x = a.parse_args()
    if not KEY: sys.exit("нет CHECKO_KEY")
    today = dt.date.fromisoformat(x.date)
    old = (today - dt.timedelta(days=x.recheck_days)).isoformat()
    cache = {}
    if x.cache and os.path.exists(x.cache):
        c = json.load(open(x.cache, encoding="utf-8")); c = c.get("data", c); cache = c.get("inns", c)
    wins = []
    for f in glob.glob(os.path.join(x.winners, "**", "*.json"), recursive=True):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        inn = str(j.get("inn") or "")
        if len(inn) != 10 or (j.get("wins") or 0) < x.min_wins: continue
        if inn in cache and (cache[inn].get("checkedAt") or "") >= old: continue
        wins.append(j)
    wins.sort(key=lambda j: (-(j.get("wins") or 0), -(j.get("sum") or 0)))
    st = {"candidates": len(wins), "requests": 0, "noLicense": 0, "licensed": 0, "notActive": 0, "errors": 0}
    leads = []
    for w in wins[: x.max]:
        inn = w["inn"]
        j = card(inn); st["requests"] += 1
        time.sleep(0.3)
        if not j or (j.get("meta") or {}).get("status") != "ok":
            st["errors"] += 1; st.setdefault("lastError", str((j or {}).get("meta"))[:200])
            if "limit" in str((j or {}).get("meta") or "").lower(): break
            continue
        d = j.get("data") or {}
        if (d.get("Статус") or {}).get("Код") not in (None, "001") and "Действ" not in str(d.get("Статус")):
            st["notActive"] += 1; cache[inn] = {"lic": None, "checkedAt": x.date, "note": "не действует"}; continue
        lic = [l for l in (d.get("Лиценз") or []) if any(EDU.search(v) for v in (l.get("ВидДеят") or [])) and not l.get("ДатаОконч")]
        cache[inn] = {"lic": bool(lic), "num": lic[0].get("Номер") if lic else "", "checkedAt": x.date}
        if lic:
            st["licensed"] += 1; continue
        st["noLicense"] += 1
        okved = (d.get("ОКВЭД") or {}).get("Наим") or ""
        if str((d.get("ОКВЭД") or {}).get("Код") or "").startswith(("86", "87", "75")):   # медицина и ветеринария — не профиль МИСБ
            st["skippedMed"] = st.get("skippedMed", 0) + 1; continue
        nm = f"{w.get('name') or ''} {d.get('НаимПолн') or ''}"
        if str((d.get("ОКВЭД") or {}).get("Код") or "").startswith("85") or re.search(r"(?i)(?<![а-я])дпо(?![а-я])|образовательн|учебн\w* центр|университет|институт|академи|колледж|школ", nm):
            st["skippedEdu"] = st.get("skippedEdu", 0) + 1; continue   # образовательная организация — лицензия почти наверняка есть, у Чекко нет сведений
        leads.append({"id": f"sig-partner-{inn}", "title": f"Партнёр без лицензии ДПО: {w.get('name') or d.get('НаимСокр') or inn} — {w.get('wins')} контрактов на обучение",
                      "customer": w.get("name") or d.get("НаимСокр") or "", "customerInn": inn, "region": (d.get("Регион") or {}).get("Наим") or "",
                      "price": None, "deadline": "", "validUntil": (today + dt.timedelta(days=x.recheck_days)).isoformat(), "law": "",
                      "url": f"https://checko.ru/company/{d.get('ОГРН') or ''}" if d.get("ОГРН") else "", "source": "sig-partner",
                      "collectedAt": x.date, "country": "RU", "currency": "RUB", "flags": ["partner"],
                      "note": f"Выиграл {w.get('wins')} контрактов на обучение у {w.get('customers')} заказчиков на {round((w.get('sum') or 0) / 1e6, 1)} млн ₽ "
                              f"(последний {w.get('lastDate')}), лицензии на образовательную деятельность нет. Основной ОКВЭД: {okved[:80]}. "
                              f"Предмет: {'; '.join((w.get('topics') or [])[:2])[:300]}. Предложить сетевую форму: лицензия, удостоверения и преподаватели МИСБ."})
    st["leads"] = len(leads)
    json.dump({"leads": leads, "cache": {"inns": cache, "updatedAt": x.date}, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
