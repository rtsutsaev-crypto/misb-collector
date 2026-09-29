#!/usr/bin/env python3
"""Plan entries for registry sources whose list page was read from the cloud on 29.09.2026.

Only pages with a server-rendered list of procedures are added (probe: 200, procurement words and dates in
the text). They are `planned`: the connection counts as working only after a collection run returns rows.

Usage: python3 registry_plan.py PLAN_IN.json PLAN_OUT.json
"""
from __future__ import annotations

import json
import sys

ENTRIES = [
    {"key": "kz-mpkz", "name": "Казахстан · MP.kz, тендеры коммерческих компаний", "type": "pages", "country": "KZ", "source": "kz:mpkz",
     "pause": 3, "planned": 1, "urls": ["https://mp.kz/"],
     "method": "Список «Горячие тендеры» и лента тендеров на главной странице: название, заказчик, город, сумма в тенге, срок.",
     "note": "Пакет источников 29.09.2026, строка 25. Проверено из облака 29.09.2026: страница читается, список виден. country: KZ, currency: KZT. Только первая страница; отбор по словарю."},
    {"key": "kz-qazaqgaz", "name": "Казахстан · QazaqGaz, объявления о закупках", "type": "pages", "country": "KZ", "source": "kz:qazaqgaz",
     "pause": 3, "planned": 1, "urls": ["https://qazaqgaz.kz/ru/obyavleniya-o-zakupkah"],
     "method": "Объявления о закупках АО «НК «QazaqGaz» и дочерних ТОО: название, дата.",
     "note": "Пакет источников 29.09.2026, строка 98. Проверено из облака 29.09.2026: страница читается, в списке 12 объявлений. country: KZ, currency: KZT. Срок подачи в списке может не показываться — оставь пустым."},
    {"key": "corp-akron", "name": "Акрон Холдинг · закупки", "type": "pages", "country": "RU", "source": "corp:akron",
     "pause": 3, "planned": 1, "urls": ["https://www.akron-holding.ru/tenders/"],
     "method": "Список приёма предложений группы: предмет, организация, срок «Прием предложений до».",
     "note": "Пакет источников 29.09.2026, строка 66. Проверено из облака 29.09.2026: список виден, в основном промышленные закупки (обучение встречается редко)."},
]


def apply(plan: dict) -> dict:
    have = {s["key"] for s in plan["sources"]}
    at = [s["key"] for s in plan["sources"]].index("kz-rostender") + 1
    for e in ENTRIES:
        if e["key"] not in have:
            plan["sources"].insert(at, e)
            at += 1
    return plan


if __name__ == "__main__":
    p = apply(json.load(open(sys.argv[1], encoding="utf-8")))
    json.dump(p, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
    print(len(p["sources"]), "sources")
