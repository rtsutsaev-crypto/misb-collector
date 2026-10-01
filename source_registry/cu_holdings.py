#!/usr/bin/env python3
"""Russian groups of the corporate universities package -> the holdings search (TenderGuru: training purchases by group name).

Usage: python3 cu_holdings.py institutions.jsonl PLAN(collector/sources-plan.json) [--write]

TenderGuru covers Russian purchases only, so Belarusian and Kazakh institutions are not added (their channels are read
by the cu-* rotations and the cu-search templates). Groups already searched by the holdings source are not added
again. A term is the name the group uses in its purchases (checked by hand: short abbreviations that would match other
customers — Б1, Т1, ФБК, Kept — are left out). New terms are appended at the end of `terms`, so the cursor
meta/holdings-stats.next keeps its place; each term carries the institution ids it was added for (`cu`). The batch
grows so that the circle stays 31 runs (one daily run a month).
Idempotent: a second run adds nothing.
"""
import json, sys

CIRCLE = 31  # runs per circle of the holdings search
# corporate_group of the package -> search term
TERMS = {
    "Газпром нефть": "Газпром нефть", "ВСМПО-АВИСМА": "ВСМПО-АВИСМА", "ГАЗ": "Группа ГАЗ", "Силовые машины": "Силовые машины",
    "Ростсельмаш": "Ростсельмаш", "Askona Life Group": "Аскона", "Банк УРАЛСИБ": "Уралсиб", "Росгосстрах": "Росгосстрах",
    "РЕСО-Гарантия": "РЕСО-Гарантия", "Банк России": "Банк России", "IBS": "АйБиЭс", "КРОК": "КРОК инкорпорейтед",
    "Softline": "Софтлайн", "ЛАНИТ": "ЛАНИТ", "СТД Петрович": "СТД Петрович", "СК Согласие": "СК Согласие",
    "Технологии Доверия": "Технологии Доверия", "СКБ Контур": "СКБ Контур", "Тензор / Saby": "Тензор", "Nexign": "Нэксайн",
    "ВСК": "САО ВСК", "Ак Барс Банк": "Ак Барс Банк", "1С": "Фирма 1С", "Группа «Волга-Днепр»": "Волга-Днепр",
    "Холдинг «Аэропорты Регионов»": "Аэропорты Регионов", "ООО «Воздушные Ворота Северной Столицы»": "Воздушные Ворота Северной Столицы",
    "S7 Group / авиакомпания «Сибирь»": "авиакомпания Сибирь", "АО «ЮТэйр-Вертолетные услуги»": "ЮТэйр",
    "Группа DME / ООО «ДОМОДЕДОВО ТРЕЙНИНГ»": "Домодедово", "АО «Аэропорт Толмачёво» / Новапорт": "Толмачево", "СДЭК": "СДЭК",
    "Транспортная группа FESCO": "ДВМП", "ООО «НИПИ НГ «Петон»": "Петон", "Производственная компания ТЕХНОНИКОЛЬ": "ТЕХНОНИКОЛЬ",
    "КНАУФ Россия": "КНАУФ", "Агрохолдинг «РУСЛАКТО»": "РУСЛАКТО", "ГК «ЭкоНива»": "ЭкоНива", "ИНВИТРО": "ИНВИТРО",
    "BIOCAD": "БИОКАД", "Группа «Р-Фарм»": "Р-Фарм", "Cosmos Hotel Group": "Cosmos Hotel", "Курорт «Мрия»": "Мрия",
    "ФАУ «Главгосэкспертиза России»": "Главгосэкспертиза", "Строительно-инвестиционный холдинг «Автобан»": "Автобан",
    "Группа «ЛокоТех»": "ЛокоТех", "Медскан / Hadassah Medical Moscow": "Медскан", "Европейский медицинский центр (ЕМС)": "Европейский медицинский центр",
    "Эталон": "ГК Эталон", "А101": "А101", "Мосводоканал": "Мосводоканал", "СМ-Клиника": "СМ-Клиника", "Гемотест": "Гемотест",
    "Мать и дитя": "Мать и дитя", "МОСГАЗ": "МОСГАЗ", "ИПХиК": "ИПХиК", "Хеликс": "Хеликс",
    "Водоканал Санкт-Петербурга": "Водоканал Санкт-Петербурга",
}


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    inst = [json.loads(x) for x in open(a[0], encoding="utf-8") if x.strip()]
    plan = json.load(open(a[1], encoding="utf-8"))
    h = next(s for s in plan["sources"] if s["key"] == "holdings")
    have = {t["q"].lower() for t in h["terms"]}
    by_group = {}
    for i in inst:
        if i.get("country") == "RU":
            by_group.setdefault(i.get("corporate_group"), []).append(i["institution_id"])
    missing = sorted(set(TERMS) - set(by_group))
    if missing:
        sys.exit("нет таких групп в пакете: " + ", ".join(missing))
    add = [{"group": g, "q": q, "cu": by_group[g]} for g, q in TERMS.items() if q.lower() not in have]
    h["terms"] += add
    # the circle stays about a month (31 runs, as before the package): a larger batch, not a longer circle
    h["batch"] = max(h["batch"], -(-len(h["terms"]) // CIRCLE))
    h["planned"] = h["batch"] * len(h.get("words") or [1])
    print(f"добавлено {len(add)} групп; всего {len(h['terms'])}, за запуск {h['batch']}, круг {-(-len(h['terms']) // h['batch'])} запусков")
    if "--write" in a and add:
        json.dump(plan, open(a[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(a[1], "a", encoding="utf-8").write("\n")


if __name__ == "__main__":
    main()
