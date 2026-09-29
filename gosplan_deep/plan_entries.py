#!/usr/bin/env python3
"""Plan entries added on 29.09.2026 (config/sources-plan 5.5): deeper EIS passes, plans,
requests for quotes, neighbouring markets. Same data the manual passes used.

Usage: python3 plan_entries.py PLAN_IN.json PLAN_OUT.json
The plan document is read as it is in the database (top-level fields note/sources/updatedAt/version);
existing sources are kept, new ones are inserted next to their relatives, existing gosplan gets the
extra requests and `deep`.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from gp_deep import CLASSES_NEW, OPEN_WORDS, SMALL_PRICE, TOPIC_STEMS, QUOTE_WORDS  # noqa: E402

API = "https://v2.gosplan.info"
RFQ_TYPES_44 = ["epNotificationEZK", "purchaseNoticeZK", "epNotificationEZP"]
RFQ_TYPES_223 = ["purchaseNoticeZKESMBO", "purchaseNoticeZPESMBO", "epNotificationEZK"]
RFQ_WORDS = QUOTE_WORDS[:3]
TG_QUOTES = [
    "запрос коммерческих предложений обучение", "запрос коммерческих предложений семинар",
    "запрос коммерческих предложений тренинг", "запрос коммерческих предложений мероприятие",
    "запрос цен обучение персонала", "запрос цен повышение квалификации", "запрос цены обучение", "запрос цены семинар",
    "запрос предложений обучение", "запрос предложений тренинг", "запрос предложений конференция",
    "запрос котировок обучение", "запрос ценовой информации семинар", "запрос ценовых предложений",
    "изучение рынка услуг обучения", "изучение рынка тренинг", "изучение рынка семинар", "изучение рынка конференция",
    "анализ рынка обучение", "анализ рынка тренинг", "сбор коммерческих предложений обучение",
    "ценовая информация обучение", "определение начальной цены обучение", "маркетинговые исследования обучение",
]


def req(law: str, **kw) -> dict:
    r = {**kw, "law": law, "path": f"/fz{law}/purchases"}
    return dict(sorted(r.items()))


def entries() -> dict:
    main_extra = []
    for law in ("44", "223"):
        for c in ("70.22", "74.90", "85.59"):
            main_extra.append(req(law, classifier=c))
    main_extra.append(req("223", classifier="78.10"))
    for c in ("85.42", "85.41", "82.30"):
        main_extra.append(req("44", classifier=c, max_price_le=str(SMALL_PRICE)))
    topics = [req(law, classifier="85.42", object_info=w) for law in ("44", "223") for w in TOPIC_STEMS]
    quotes = [req("44", purchase_type=t, object_info=w) for t in RFQ_TYPES_44 for w in RFQ_WORDS] + \
             [req("223", purchase_type=t, object_info=w) for t in RFQ_TYPES_223 for w in RFQ_WORDS]
    # every open purchase by subject words: the server-side deadline filter keeps closed ones out of the pages;
    # {today} is replaced by the collector with the current date (YYYY-MM-DD)
    open_reqs = []
    for w in OPEN_WORDS + TOPIC_STEMS:
        open_reqs.append(req("44", object_info=w, collecting_finished_after="{today}"))
        open_reqs.append(req("223", object_info=w, submission_close_after="{today}"))
    gp = {"base": API, "common": "limit=50&sort=published_at_desc", "pause": 2, "type": "api"}
    new = {
        "gosplan-open": {**gp, "key": "gosplan-open", "name": "ЕИС через ГосПлан · все открытые закупки по словам", "pause": 1,
            "batch": 30, "deep": 6, "planned": 30, "source": "gosplan:fz44 | gosplan:fz223", "requests": open_reqs,
            "method": "Слова форм обучения и тем словаря без привязки к коду ОКПД2 (у многих извещений код не указан) с серверным фильтром «приём заявок ещё идёт»: страницы содержат только открытые закупки.",
            "note": "Правила как у источника gosplan. В запросах значение {today} — сегодняшняя дата ГГГГ-ММ-ДД. Проверено 29.09.2026: по 73 словам открытых извещений 23 тыс., подходящих по словарю около 1,2 тыс., не известных базе 30 — ЕИС покрыт почти полностью, источник добирает остаток."},
        "gosplan-topics": {**gp, "key": "gosplan-topics", "name": "ЕИС через ГосПлан · темы словаря внутри ОКПД2 85.42",
            "batch": 24, "deep": 2, "planned": 24, "source": "gosplan:fz44 | gosplan:fz223", "requests": topics,
            "method": "Слова из тем словаря (охрана труда, бухгалтерский учёт, кадры, делопроизводство, сметное дело, закупки и др.) внутри учебного класса 85.42: так выдача глубже, чем 50 новейших закупок класса. Порциями по 24 запроса (ротация).",
            "note": "Правила как у источника gosplan. Проверено 29.09.2026: в первом проходе 94 запроса дали 153 новые закупки по теме (13 с открытым приёмом)."},
        "gosplan-quotes": {**gp, "key": "gosplan-quotes", "name": "ЕИС через ГосПлан · запросы котировок и предложений",
            "planned": len(quotes), "source": "gosplan:fz44 | gosplan:fz223", "requests": quotes,
            "method": "Закупки в форме запроса котировок / предложений (purchase_type) по обучению, семинарам, тренингам: малые закупки, где заказчик выбирает поставщика по ценам. Прямой спрос без конкурса.",
            "note": "Правила как у источника gosplan; лиду добавь flags [\"rfq\"] и note «Запрос котировок/предложений». Проверено 29.09.2026: встречаются редко (в основном 223-ФЗ, запросы котировок среди СМП)."},
        "gosplan-plan44": {**gp, "key": "gosplan-plan44", "name": "Планы закупок 44-ФЗ · позиции на будущие годы",
            "path": "/fz44/tenderplans/positions", "deep": 6, "planned": len(CLASSES_PLAN), "source": "gosplan:plan44",
            "requests": [{"classifier": c, "path": "/fz44/tenderplans/positions"} for c in CLASSES_PLAN],
            "method": "Позиции планов закупок 44-ФЗ учебных и смежных классов ОКПД2: закупка видна за месяцы и годы до объявления. Брать только позиции на годы позже текущего.",
            "note": "Ответ: массив позиций; у позиции plan_number, customer (ИНН), region, published_at и source.commonInfo: positionNumber, IKZ, publishYear, purchaseObjectInfo (предмет), OKPD2Info.OKPDCode; source.financeInfo.total — сумма. Лид: id \"plan44-\" + positionNumber, title = purchaseObjectInfo, customer = \"ИНН \" + customer, customerInn, region по коду, price = financeInfo.total (если > 0), deadline \"\", flags [\"plan\"], law 44-ФЗ, source gosplan:plan44, url https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=<IKZ>, note «План закупок 44-ФЗ на <publishYear> год, позиция <positionNumber>». Только publishYear больше текущего года и отбор по словарю (название и ОКПД2). Проверено 29.09.2026: из 1530 позиций 226 на будущие годы, 70 по теме."},
        "gosplan-plan223": {"key": "gosplan-plan223", "name": "Планы закупок 223-ФЗ · позиции с плановым месяцем", "type": "plans223",
            "base": API, "path": "/fz223/purchaseplans", "pause": 1, "newest": 150, "maxDetails": 100, "planned": 1, "source": "gosplan:plan223",
            "method": "Планы закупок заказчиков по 223-ФЗ: каждая позиция плана с предметом, суммой и плановым годом, кварталом и месяцем — закупка видна за месяцы до объявления (у Росатома и его дочерних — на годы вперёд).",
            "note": "См. правило типа plans223 в задании. Состояние — документ meta/plans-state (поле since). Проверено 29.09.2026: из 300 новейших планов 9636 позиций, по теме 224, новых лидов 80."},
    }
    country = {
        "by-icetrade": {"key": "by-icetrade", "name": "Беларусь · Портал государственных закупок (icetrade.by)", "type": "pages", "country": "BY",
            "source": "by:icetrade", "pause": 3, "planned": 4,
            "urls": ["https://www.icetrade.by/search/auctions?search_text=" + q + "&search=%D0%9D%D0%B0%D0%B9%D1%82%D0%B8" for q in (
                "%D0%BE%D0%B1%D1%83%D1%87%D0%B5%D0%BD%D0%B8%D0%B5", "%D1%81%D0%B5%D0%BC%D0%B8%D0%BD%D0%B0%D1%80",
                "%D1%82%D1%80%D0%B5%D0%BD%D0%B8%D0%BD%D0%B3", "%D0%BF%D0%BE%D0%B2%D1%8B%D1%88%D0%B5%D0%BD%D0%B8%D0%B5+%D0%BA%D0%B2%D0%B0%D0%BB%D0%B8%D1%84%D0%B8%D0%BA%D0%B0%D1%86%D0%B8%D0%B8")],
            "method": "Открытый поиск процедур белорусского портала по словам обучения (обучение, семинар, тренинг, повышение квалификации); первая страница выдачи.",
            "note": "country: BY, currency: BYN, region «Беларусь, <город>». Проверено 29.09.2026 из облака сеанса Claude: сайт не открылся — ошибка цепочки сертификата (корневой сертификат белорусского НУЦ не входит в общие); проверку сертификата не отключать. Если и в задании адрес не открывается, status failed с причиной."},
        "by-goszakupki": {"key": "by-goszakupki", "name": "Беларусь · goszakupki.by", "type": "pages", "country": "BY", "source": "by:goszakupki",
            "pause": 3, "planned": 1, "urls": ["https://goszakupki.by/"],
            "method": "Второй белорусский портал закупок; список процедур на главной странице.",
            "note": "country: BY, currency: BYN. Из облака сеанса Claude 29.09.2026 не отвечал (соединение сброшено). Нет списка — status failed с причиной."},
        "uz-uzex": {"key": "uz-uzex", "name": "Узбекистан · UZEX eTender (etender.uzex.uz)", "type": "pages", "country": "UZ", "source": "uz:uzex",
            "pause": 3, "planned": 2, "urls": ["https://etender.uzex.uz/lots/2/0", "https://etender.uzex.uz/lots/1/0"],
            "method": "Списки лотов UZEX eTender (лоты 2 и 1); поля лота: номер (Lot raqami), название, начальная цена (Boshlang'ich narx), срок (Tugash sanasi).",
            "note": "country: UZ, currency: UZS (или валюта из карточки), region «Узбекистан, <область>». Страница строится скриптом: из облака сеанса Claude 29.09.2026 отдаёт только оболочку без лотов; прикладной интерфейс xarid.uzex.uz отвечает 403 без браузерного user-agent — его не обходим. Если в списке нет лотов — status failed «нужен разбор скрипта». Слова обучения по-узбекски: o'qitish, malaka oshirish, masofaviy ta'lim, tadbir, konferensiya."},
        "kg-zakupki": {"key": "kg-zakupki", "name": "Кыргызстан · Портал государственных закупок (zakupki.gov.kg)", "type": "pages", "country": "KG", "source": "kg:zakupki",
            "pause": 3, "planned": 1, "urls": ["https://zakupki.gov.kg/popp/view/order/list.xhtml"],
            "method": "Первая страница списка объявлений: 10 новейших (номер, организация, вид закупки, название, планируемая сумма, дата публикации, срок подачи).",
            "note": "country: KG, currency: KGS, region «Кыргызстан». Проверено 29.09.2026: страница читается, список из 10 объявлений; следующие страницы и поиск по словам — через форму (JSF), из задания недоступны. Названия часто на кыргызском (окутуу, квалификацияны жогорулатуу, аралыктан окутуу, иш чара) — учитывай. Сайт-сборщик открытых данных ocds.zakupki.gov.kg 29.09.2026 не отвечал."},
    }
    tg = {"tenderguru-quotes": {"key": "tenderguru-quotes", "name": "TenderGuru API · запросы цен и анализ рынка", "type": "api", "base": "https://www.tenderguru.ru",
          "path": "/api2.3/export", "common": "dtype=json", "auth": "ключ TenderGuru из задания", "auth_param": "api_code", "pause": 3, "batch": 12,
          "planned": 12, "source": "tenderguru", "requests": [{"kwords": q} for q in TG_QUOTES],
          "method": "Запросы TenderGuru по признакам прямого спроса: запрос цен, запрос коммерческих предложений, запрос предложений, изучение и анализ рынка вместе со словами обучения. Заказчик выбирает поставщика до или вместо конкурса. Порциями по 12 запросов (ротация).",
          "note": "Правила как у tenderguru-more; каждому лиду добавь flags [\"rfq\"] и note «Прямой спрос: запрос цен или предложений; запрос «…»». Проверено 29.09.2026: 16 запросов дали 36 новых по теме."}}
    return {"rad": rad_entry(), "main_extra": main_extra, "new": new, "country": country, "tg": tg}


CLASSES_PLAN = ["85.42", "85.41", "82.30", "74.90", "70.22", "78.10"]


RAD_WORDS = ["повышение квалификации", "образовательные услуги", "обучение", "семинар", "тренинг", "конференция",
             "форум", "профессиональная подготовка", "оценка персонала", "коучинг"]


def rad_entry() -> dict:
    """ЭТП РАД (tender.lot-online.ru): public JSON list that the site's own main page loads without login."""
    return {"key": "rad-lots", "name": "ЭТП РАД · открытые закупки по словам", "type": "api", "country": "RU", "source": "rad",
            "base": "https://tender.lot-online.ru/api-gateway/indexer/api", "path": "/lots/query-extended",
            "common": "statusGroup=DEMANDS_STARTED&limit=100", "pause": 2, "planned": len(RAD_WORDS),
            "requests": [{"search": w} for w in RAD_WORDS],
            "method": "Публичный список открытых закупок площадки (тот же JSON, что грузит её главная страница без входа), поиск по слову параметром search, до 100 записей на запрос.",
            "note": "Проверено 29.09.2026 браузером и прямым запросом: 1 993 открытые закупки, по словам МИСБ около 36; у части есть номер ЕИС (уже приходят через ГосПлан). Ответ: {count, data:[{etpNumber, eisNumber, title, organizationTitle, price, publicationDate, demandEndDate, purchaseMethod, regionOkato}]}. robots.txt площадки этот адрес не закрывает."}


def apply(plan: dict) -> dict:
    E = entries()
    S = plan["sources"]
    keys = [s["key"] for s in S]

    def insert_after(after: str, doc: dict) -> None:
        S[[s["key"] for s in S].index(after) + 1:0] = [doc]

    g = next(s for s in S if s["key"] == "gosplan")
    have = {json.dumps(r, sort_keys=True) for r in g["requests"]}
    g["requests"] += [r for r in E["main_extra"] if json.dumps(r, sort_keys=True) not in have]
    g["planned"] = len(g["requests"])
    g["deep"] = 3
    g["method"] = ("Открытый API с данными ЕИС; 44-ФЗ и 223-ФЗ по кодам ОКПД2 и словам; ключ из задания; пауза 2 с. "
                   "Углубление: страницы 2–3 (skip=50, 100), пока на странице есть новые номера; коды ОКПД2 70.22, 74.90, 85.59, 78.10; малый объём 44-ФЗ (max_price_le=600000)")
    order = ["gosplan-topics", "gosplan-open", "gosplan-quotes", "gosplan-plan44", "gosplan-plan223"]
    prev = "gosplan"
    for k in order:
        if k not in keys:
            insert_after(prev, E["new"][k])
        prev = k
    if "tenderguru-quotes" not in keys:
        insert_after("tenderguru-etp", E["tg"]["tenderguru-quotes"])
    if "rad-lots" not in [s["key"] for s in S]:
        insert_after("tenderguru-quotes", E["rad"])
    prev = "kz-rostender"
    for k in ("by-icetrade", "by-goszakupki", "uz-uzex", "kg-zakupki"):
        if k not in keys:
            insert_after(prev, E["country"][k])
        prev = k
    for s in S:  # neighbouring countries are taken now: no region drops for KZ and BY
        if "drop_regions" in s:
            left = [r for r in s["drop_regions"] if r not in ("Казахстан", "Беларусь")]
            if left:
                s["drop_regions"] = left
            else:
                del s["drop_regions"]
        if s["key"] in ("energybase", "energybase-cos") and "«ТОО»" in s.get("note", ""):
            s["note"] = s["note"].replace("Заказчики с формой «ТОО» — Казахстан, отбрасывай.", "Заказчики с формой «ТОО» — Казахстан: берём, country KZ, currency KZT.")
    return plan


if __name__ == "__main__":
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    plan = apply(plan)
    json.dump(plan, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
    print(len(plan["sources"]), "sources")
