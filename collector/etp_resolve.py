"""etp_resolve.py — электронная площадка для карточек, у которых сайт не может определить её сам.

Сайт (monitor.html, etpOf) знает площадку из разобранного извещения ЕИС (коллекция leaddocs), из ссылки карточки и из
поля platform лида. Всё остальное он показывает как «площадка не определена». Этот скрипт добирает площадку из того, что
уже лежит в базе, без запросов в сеть:
  1. источник лида: выгрузка «Монитора тендеров РФ» (source «mtrf:<площадка>»), лиды с известной площадкой по самому
     источнику (B2B-Center, Госзакупки РК, MITWORK, магазин ЛО, РАД), ссылки на площадки, которых нет в списке сайта;
  2. извещение ЕИС без площадки: неэлектронная закупка 223-ФЗ или закупка без торгов — это «без ЭТП», а не «неизвестно»;
  3. сверка карточки агрегатора с ЕИС (meta/etp-match, готовит etp_match.py): номер извещения и площадка из него;
  4. запрос на сайте заказчика (центры «Мой бизнес», корпоративные порталы) — «сайт заказчика, без ЭТП»;
  5. группа заказчика (справочник закупочных маршрутов) и прошлые закупки того же заказчика в базе — с пометкой «вероятно».

Запуск: python3 etp_resolve.py --leadsets <папка leadsets> --leaddocs <папка leaddocs> [--match <meta/etp-match.json>]
        [--verdicts <meta/collector-verdicts.json>] --date ГГГГ-ММ-ДД --out leadetp.json [--part 600]
Выход: {"parts": [{"items": {id: {...}}}, ...], "stats": {...}} — каждую часть записать документом leadetp/part-N
(set целиком; лишние старые части удалить). Элемент: {kind: "etp"|"own"|"noetp", etp, section?, conf: "точно"|"вероятно",
by, why, eis?}. Ключ — id лида, как в leaddocs.
"""
import argparse, collections, glob, json, os, re

ETP_HOST = [  # как ETP_HOST сайта, плюс площадки, которых в нём нет
    (r"(^|\.)etpgaz\.gazprombank\.ru$", "ЭТП ГПБ", "Газпром"), (r"(^|\.)(etp\.gpb\.ru|etpgpb\.ru|etp\.gazprombank\.ru)$", "ЭТП ГПБ", ""),
    (r"rts-tender\.ru$", "РТС-тендер", ""), (r"b2b-center\.ru$", "B2B-Center", ""), (r"agregatoreat\.ru$", "Единый агрегатор торговли", ""),
    (r"lot-online\.ru$", "РАД", ""), (r"market\.mosreg\.ru$", "Электронный магазин МО", ""), (r"rzd-medicine\.ru$", "РЖД-Медицина", ""),
    (r"bidzaar\.com$", "Bidzaar", ""), (r"otc\.ru$", "OTC", ""), (r"zakupki\.lenreg\.ru$", "Электронный магазин ЛО", ""),
    (r"mitwork\.kz$", "MITWORK ЕЭП", ""), (r"goszakup\.gov\.kz$", "Госзакупки РК", ""), (r"tenders\.mts\.ru$", "МТС Закупки", ""),
    (r"sberbank-ast\.ru$", "Сбербанк-АСТ", ""), (r"roseltorg\.ru$", "Росэлторг", ""), (r"fabrikant\.ru$", "Фабрикант", ""),
    (r"zakazrf\.ru$", "АГЗ РТ", ""), (r"etp\.r-est\.ru$|(^|\.)rts?-est\.ru$", "ЭТП РЭСТ", ""), (r"tektorg\.ru$", "ТЭК-Торг", ""),
    (r"rftorgi\.ru$", "Торги РФ", ""), (r"fedtorgi\.ru$", "Торги Федерации", ""), (r"etp-ets\.ru$", "Фабрикант", ""),
]
EXTRA_HOST = [(r"zakupki\.tochka\.com$", "Точка Закупки"), (r"(^|\.)tender\.pro$", "Tender.Pro"), (r"sberb2b\.ru$", "SberB2B"),
              (r"tendem\.ru$", "Тендем"), (r"eshoprzd\.ru$", "ЭТП РЖД (eshoprzd)"), (r"zakupki\.mos\.ru$", "Портал поставщиков Москвы"),
              (r"zakup\.sk\.kz$", "Закупки Самрук-Казына")]
NOT_PLATFORM = r"(^|\.)(rostender\.info|bicotender\.ru|tenderguru\.ru|komtender\.ru|energybase\.ru|synapsenet\.ru|hrtime\.ru|t\.me|zakupki\.gov\.ru|bestspeakers\.ru|speakermarket\.ru|boosty\.to)$"
SIG_RX = re.compile(r"^(demand:|fd-|cu-|v7-|reg2-|reg3-search)")

# выгрузка «Монитора тендеров РФ»: колонка площадки стала суффиксом источника
MTRF = {"РТС-тендер": "РТС-тендер", "Росэлторг": "Росэлторг", "Фабрикант": "Фабрикант", "Сбербанк-АСТ": "Сбербанк-АСТ", "OTC": "OTC",
        "ТЭК-Торг": "ТЭК-Торг", "SberB2B": "SberB2B", "Точка Закупки": "Точка Закупки", "ЭТП ГПБ": "ЭТП ГПБ", "Tender.Pro": "Tender.Pro",
        "ЗаказРФ": "АГЗ РТ", "РАД": "РАД", "ЕАТ Березка (discovery)": "ЕАТ «Берёзка»", "СИНАПС/ТЭК-Торг": "ТЭК-Торг",
        "Портал поставщиков Москвы (discovery)": "Портал поставщиков Москвы", "B2B-Center": "B2B-Center"}
MTRF_OWN = re.compile(r" direct$| procurement$")          # «НОВАТЭК direct», «ВНИКТИ procurement» — сайт заказчика
SRC_PLAT = [(r"^b2b-center", "B2B-Center"), (r"^kz:goszakup", "Госзакупки РК"), (r"^kz:mitwork|^kz-mitwork", "MITWORK ЕЭП"),
            (r"^zmo", "Электронный магазин ЛО"), (r"^rad(-lots)?(:|$)", "РАД"), (r"^etp-mosreg|^Электронный магазин МО", "Электронный магазин МО")]
OWN_SRC = re.compile(r"^(grants-msp|corp-|corp:)")         # запрос или закупка на сайте самого заказчика

# группа заказчика → площадка группы (справочник закупочных маршрутов meta/holdings-routes и практика групп); «вероятно»
GROUPS = [
    (re.compile(r"(^|[\s«\"])(РН-|Роснефт|Самотлорнефтегаз|Юганскнефтегаз|Сызранский НПЗ|Куйбышевский НПЗ|Новокуйбышевск|Удмуртнефть|Томскнефть|СИБИНТЕК|Славнефть|Башнефть|Ангарская нефтехим|Варьеганнефтегаз|Оренбургнефть|Самаранефтегаз|Ванкорнефть|Таас-Юрях|Верхнечонскнефтегаз|Комсомольский НПЗ)", re.I),
     "ТЭК-Торг", "Роснефть", "группа «Роснефть» проводит закупки на ТЭК-Торг (секция организатора «Роснефть»)"),
    (re.compile(r"Зарубежнефт", re.I), "ТЭК-Торг", "", "АО «Зарубежнефть» публикует закупки на ТЭК-Торг"),
    (re.compile(r"(^|[\s«\"])Газпром(?!\s*нефть|нефть)|Газстройпром|Мосэнерго|Газпром энергохолдинг|Газпром переработка|Газпром нефтехим", re.I),
     "ЭТП ГПБ", "Газпром", "группа «Газпром» закупает через ЭТП ГПБ (секция «Газпром») и tenders.gazprom.ru"),
    (re.compile(r"КазМунайГаз|QazaqGaz|Самрук", re.I), "Закупки Самрук-Казына", "", "компании фонда «Самрук-Казына» закупают через zakup.sk.kz"),
]


def host(u):
    m = re.match(r"^https?://([^/:?#]+)", u or "", re.I)
    return m.group(1).lower().removeprefix("www.") if m else ""


def plat_by_host(h):
    if not h or re.search(NOT_PLATFORM, h): return None
    for rx, n, sec in ETP_HOST:
        if re.search(rx, h): return n, sec
    for rx, n in EXTRA_HOST:
        if re.search(rx, h): return n, ""
    return None


def site_etp(l, d):
    """Что сайт определит сам (повтор etpOf0 из monitor.html): 'etp', 'off' (план, сигнал) или 'none'."""
    for u in (d.get("platformCard"), d.get("platformUrl"), l.get("platformUrl"), l.get("url")):
        h = host(u)
        if not h or re.search(NOT_PLATFORM, h): continue
        if any(re.search(rx, h) for rx, _, _ in ETP_HOST): return "etp"
    nm = d.get("platform") or l.get("platform")
    if nm and not re.match(r"^(Запросы на спикеров|speakermarket|boosty|ЕИС)", nm, re.I): return "etp"
    fl = l.get("flags") or []
    if "plan" in fl or "forecast" in fl: return "off"
    src = str(l.get("source") or "")
    if l.get("sigPkg") or SIG_RX.match(src) or re.search(r"спикер|speaker", src, re.I) or "speaker" in fl: return "off"
    return "none"


def known_platform(l, d):
    """Площадка, которую сайт уже показывает, — для истории заказчика."""
    for u in (d.get("platformCard"), d.get("platformUrl"), l.get("platformUrl"), l.get("url")):
        p = plat_by_host(host(u))
        if p: return p[0]
    nm = d.get("platform") or l.get("platform")
    if nm and not re.match(r"^(Запросы на спикеров|speakermarket|boosty|ЕИС)", nm, re.I): return nm
    return None


def cust_key(l):
    inn = str(l.get("customerInn") or "")
    if re.fullmatch(r"\d{10}|\d{12}", inn): return "inn:" + inn
    m = re.search(r"ИНН\s*(\d{10,12})", l.get("customer") or "")
    if m: return "inn:" + m.group(1)
    n = (l.get("customer") or "").lower()
    n = re.sub(r"\b(ооо|оао|зао|пао|ао|нао|гбу|гау|гку|мбу|мку|фгбу|фгуп|гуп|муп|анo|ано)\b|[«»\"'()]", " ", n)
    n = " ".join(n.split())
    return ("name:" + n) if len(n) >= 6 else ""


def eis_noetp(i, d):
    """Извещение разобрано, площадки нет: без ЭТП ли это закупка (по способу из eisdocs.py)."""
    m = d.get("method") or ""
    if not d or d.get("platform"): return None
    if i.startswith("3"):
        if d.get("electronic") is False or (m and not d.get("electronic")):
            return "закупка 223-ФЗ не в электронной форме" + (f" (способ «{m}»)" if m else "") + ": заявка — по документации заказчика"
    else:
        if re.search(r"единственн|EP44|ЕП", m, re.I): return "закупка у единственного поставщика — без торгов на площадке"
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leadsets", required=True); a.add_argument("--leaddocs", required=True)
    a.add_argument("--match"); a.add_argument("--verdicts"); a.add_argument("--date", required=True)
    a.add_argument("--out", required=True); a.add_argument("--part", type=int, default=600)
    x = a.parse_args()

    def load(f):
        j = json.load(open(f, encoding="utf-8")); return j.get("data", j)
    leads = {}
    for f in sorted(glob.glob(os.path.join(x.leadsets, "**", "*.json"), recursive=True)):
        for l in load(f).get("leads", []):
            leads[str(l.get("id"))] = l
    ld = {}
    files = [(load(f), f) for f in glob.glob(os.path.join(x.leaddocs, "**", "*.json"), recursive=True)]
    for d, _ in sorted(files, key=lambda t: str(t[0].get("updatedAt", ""))):
        ld.update(d.get("items") or {})
    match = (load(x.match).get("items") or {}) if x.match and os.path.exists(x.match) else {}

    # история заказчика: площадки его закупок, которые уже известны
    hist = collections.defaultdict(collections.Counter)
    for i, l in leads.items():
        p = known_platform(l, ld.get(i, {}))
        k = cust_key(l)
        if p and k: hist[k][p] += 1

    items, st = {}, collections.Counter()
    for i, l in leads.items():
        d = ld.get(i, {})
        base = site_etp(l, d)
        st["сайт: " + base] += 1
        if base != "none": continue
        src = str(l.get("source") or "")
        r = None
        if src.startswith("mtrf:"):
            nm = src[5:]
            if nm in MTRF: r = {"kind": "etp", "etp": MTRF[nm], "conf": "точно", "by": "источник", "why": "площадка из выгрузки «Монитора тендеров РФ»"}
            elif MTRF_OWN.search(nm): r = {"kind": "own", "etp": "сайт заказчика", "conf": "точно", "by": "источник", "why": "закупка на сайте заказчика (" + nm + ")"}
        if not r:
            for rx, n in SRC_PLAT:
                if re.search(rx, src): r = {"kind": "etp", "etp": n, "conf": "точно", "by": "источник", "why": "площадка источника"}; break
        if not r:
            p = plat_by_host(host(l.get("url")))
            if p: r = {"kind": "etp", "etp": p[0], "section": p[1], "conf": "точно", "by": "ссылка", "why": "ссылка ведёт на площадку"}
        if not r and d:
            why = eis_noetp(i, d)
            if why: r = {"kind": "noetp", "etp": "без ЭТП", "conf": "точно", "by": "извещение ЕИС", "why": why}
        if not r and i in match:
            mm = match[i]; md = ld.get(str(mm.get("eis") or ""), {})
            if md.get("platform"):
                r = {"kind": "etp", "etp": md["platform"], "conf": "точно" if mm.get("score", 0) >= 0.9 else "вероятно", "by": "сверка с ЕИС",
                     "why": f"та же закупка в ЕИС № {mm['eis']} (совпадение {mm.get('score', 0):.2f})", "eis": mm["eis"]}
            elif md and eis_noetp(str(mm["eis"]), md):
                r = {"kind": "noetp", "etp": "без ЭТП", "conf": "вероятно", "by": "сверка с ЕИС", "why": eis_noetp(str(mm["eis"]), md), "eis": mm["eis"]}
        if not r and (OWN_SRC.match(src) or (host(l.get("url")) and not re.search(NOT_PLATFORM, host(l.get("url"))) and src.startswith(("corp", "grants-msp", "x-")))):
            r = {"kind": "own", "etp": "сайт заказчика", "conf": "точно", "by": "источник", "why": "запрос опубликован на сайте заказчика: " + (host(l.get("url")) or "адрес в карточке")}
        if not r:
            text = " ".join([l.get("customer") or "", src[5:] if src.startswith("mtrf:") else "", l.get("holding") or ""])
            for rx, n, sec, why in GROUPS:
                if rx.search(text):
                    r = {"kind": "etp", "etp": n, "section": sec, "conf": "вероятно", "by": "группа", "why": why}; break
        if not r:
            k = cust_key(l); c = hist.get(k)
            if c:
                top, n = c.most_common(1)[0]; tot = sum(c.values())
                if tot >= 2 and n / tot >= 0.7:
                    r = {"kind": "etp", "etp": top, "conf": "вероятно", "by": "заказчик", "why": f"заказчик провёл на этой площадке {n} из {tot} известных закупок"}
        if r:
            r = {k: v for k, v in r.items() if v not in ("", None)}
            items[i] = r; st["определено: " + r["by"] + " / " + r["conf"]] += 1
        else:
            st["не определено"] += 1
    ids = sorted(items)
    parts = [{"items": {i: items[i] for i in ids[n:n + x.part]}, "updatedAt": x.date} for n in range(0, len(ids), x.part)] or [{"items": {}, "updatedAt": x.date}]
    json.dump({"parts": parts, "stats": dict(st)}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(dict(st), ensure_ascii=False))


if __name__ == "__main__":
    main()
