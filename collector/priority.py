"""priority.py — приоритет лида и «очередь на сегодня» (план роста, волна 2: PIPELINE-1/2).

Запуск (в конце сбора, после записи лидов): python3 priority.py --leadsets <папка leadsets> --updates <папка leadupdates> --verdicts <meta/collector-verdicts.json>
  --watchlist <meta/watchlist.json> --buyers <meta/contract-buyers.json> --fit fit.json --icp icp.json --dict dictionary.json --date ГГГГ-ММ-ДД --out queue.json [--state <папка state>]
queue.json (документ meta/queue): {date, count, bands, items: [{id, priority, tier, profile, parts}], note}; items — лучшие icp.queue.stored лидов по priority (не исключённые словарём,
не закрытые и не отменённые); сайт читает документ и показывает «Очередь на сегодня» без пересчёта.

priority = profile(0–100, как fit сайта: база, направления МИСБ, формы, слова, аудитория; обязательное обучение — потолок) × k_price × k_deadline × k_repeat × k_region × k_proc × k_noise.
tier: ядро — есть бизнес-направление МИСБ и штраф fit > −10; ядро-общ — форма обучения и взрослая аудитория, тема не названа; обязательное — только ОТ/ПТМ/пожарная и т. п.
(поднимается в ядро-общ у корпоративного заказчика при цене ≥ icp.mandatory.minPriceRub или теме «руководителей и специалистов», «промышленная безопасность»…); периферия — маркеры
понижения (рабочие профессии, форумы без обучения, гостайна, школьная педагогика, безработные…); исключить — медицина, СПО/вуз, культура (скрыто verdicts). Константы — в icp.json.
"""
import argparse, collections, datetime as dt, glob, json, os, re

from collector import Matcher, site_key

W = "а-яёa-z0-9"
CORE_DIRS = {"Лидерство, управление, командообразование", "Бухгалтерский учёт, налоги, аудит", "Право и договорная работа", "Снабжение, закупки и логистика",
             "Управление персоналом и кадры", "Экономика, планирование, инвестиции", "Финансы и казначейство", "Строительство, ценообразование, сметное дело",
             "Переговоры, конфликты, деловые коммуникации", "Личная эффективность и soft skills", "Делопроизводство, ДОУ, архив, секретарь",
             "Экономика и организация труда, оплата и мотивация", "Стратегия и бизнес-моделирование", "Бережливое производство и операционная эффективность",
             "Управление проектами и изменениями", "Грамотное письмо и деловая переписка", "Продажи, клиентский сервис, маркетинг", "Цифровизация, ИИ, ИТ-навыки",
             "Экология и природопользование", "Промышленная безопасность, охрана труда, пожарная безопасность", "Энергосбережение и энергоэффективность",
             "Метрология, качество, стандартизация", "Антикоррупция, корпоративная этика, комплаенс", "Предпринимательство и МСП", "Госслужба и муниципальное управление",
             "Устойчивое развитие, ESG", "Управление рисками и принятие решений", "Методика обучения и развитие ДПО", "Экономика и управление предприятиями ТЭК (отраслевое)",
             "Деловой иностранный язык и межкультурная коммуникация"}
MAND_DIR = "Обязательное обучение"
ADULT = re.compile(r"работник|сотрудник|персонал|специалист|служащ|руководител|кадр|управленц|менеджер|бухгалтер|юрист|закупщик|контрактн|главбух|директор|лидер|команд")
PERIPHERY = [
    r"фгос|дошкольн\w* образован|воспитател|учител|классн\w* руковод|общеобразоват|(?<!\w)егэ|(?<!\w)огэ|педагогическ\w* работник|педагогов|для педагог|воспитан|обучающихся|учащихся",
    r"стропальщ|машинист|крановщ|электромонт|слесар|сварщ|сварк|люльк|сосуд\w* под (избыточн\w* )?давлен|тепловы\w* энергоустанов|электроустановк|газоопасн|котельн|(?<!\w)лифт|погрузчик|такелаж|станочн|рабоч\w* професси|присвоени\w* разряд|частн\w* охранник|продавец|повар|кассир|парикмахер|маникюр|швея|тракторист|грузоподъемн|промышленн\w* альпин",
    r"организац\w* участия|обеспечени\w* участия|регистрационн\w* взнос|организационн\w* взнос|оргвзнос",
    r"^(?!.*(обучен|семинар|тренинг|квалификац|переподготов|лекци|мастер-класс|курс)).*(организац\w* и проведени\w*|проведени\w*|подготовк\w* и проведени\w*)\w* .{0,60}(форум|конгресс|конференц|съезд|фестивал|слет|церемон|выставк|мероприят)",
    r"безработн|занятост|ищущ\w* работ|многодетн|предпенсион|инвалид|реабилитац|опекун|приемн\w* родител",
    r"государственн\w* тайн|гостайн|антитеррор|гражданск\w* оборон|го и чс|мобилизац|воинск\w* учет|защит\w* от чрезвычайн|транспортн\w* безопасн",
    r"администриров|linux|astra|kaspersky|cisco|oracle|java|python|devops|программиров|nanocad|autocad|критическ\w* информационн",
    r"английск|иностранн\w* язык|китайск|немецк|французск",
]
PERIPHERY_RX = re.compile("|".join(f"(?:{x})" for x in PERIPHERY))
NOISE = re.compile(r"(^|\W)(поставк|оборудован|комплектующ|выполнение работ|обследован|сканирован|программному продукту|лицензи[яи] на программ|ремонт|строительств|изготовлен|"
                   r"конференц[\s\-]*связ|организаци\w* (форум|конгресс|мероприят)|сопровождени\w* конференц)", re.I)
MAND_LIFT = re.compile(r"руководителей и специалистов|промышленн\w* безопасн|поведенческ\w* аудит|культур\w* безопасн")
CORP = re.compile(r"(?<!\w)(ао|пао|ооо|оао|зао)(?!\w)|холдинг|корпорац|госкорпорац", re.I)


class Fit:
    def __init__(self, fit):
        def kre(k):
            words = [re.escape(w) for w in k.lower().replace("ё", "е").strip().split()]
            src = "(^|[^" + W + "])" + ("[" + W + "-]*[\\s,.«»\"()/-]+").join(words)
            if k.endswith(" ") or (len(k.strip()) <= 3 and not k.strip().endswith("-")): src += "(?=$|[^" + W + "])"
            return re.compile(src)
        self.f = fit
        self.dirs = {d: [kre(k) for k in ks] for d, ks in fit["directions"].items()}
        self.forms = {d: [kre(k) for k in ks] for d, ks in fit["forms"].items()}
        self.terms = {g: {k: (kre(k), w) for k, w in t.items()} for g, t in fit.get("terms", {}).items()}
        v2 = fit.get("v2", {})
        self.aud = re.compile(v2.get("audienceRe") or "(руковод|резерв|менеджер|молод[а-я]* специалист|наставник|топ-)")
        self.prog = re.compile(v2.get("programRe") or "(цикл|сери[ия]|программ|курсов|ежегодн|2027|договоров на проведение)")

    def profile(self, l):
        f, v4, v2 = self.f, self.f.get("v4", {}), self.f.get("v2", {})
        t = ((l.get("title") or "") + " " + (l.get("topic") or "")).lower().replace("ё", "е")
        s = f["base"]; why = []
        ds = [d for d, rs in self.dirs.items() if any(r.search(t) for r in rs)]
        s += min(sum(v4.get("dirW", {}).get(d, 0) for d in ds), v4.get("dirCap", 99))
        fs = [x for x, rs in self.forms.items() if any(r.search(t) for r in rs)]
        s += min(sum(v4.get("formW", {}).get(x, 0) for x in fs), v4.get("formCap", 99))
        pen = 0
        for g, tt in self.terms.items():
            for k, (r, w) in tt.items():
                if r.search(t):
                    s += w; pen += min(w, 0); why.append(f"{k}:{w:+}")
        if self.aud.search(t): s += v2.get("audienceBonus", 0); why.append("аудитория")
        if self.prog.search(t): s += v2.get("programBonus", 0); why.append("программа")
        mand = "mandatory" in (l.get("flags") or []) or (MAND_DIR in ds and len(ds) == 1)
        if mand and s > v2.get("mandatoryCap", 35): s = v2["mandatoryCap"]; why.append("обяз.потолок")
        return max(0, min(100, s)), ds, fs, pen, mand, why


def price_k(l, icp):
    p, cur = l.get("price"), l.get("currency") or "RUB"
    if not isinstance(p, (int, float)) or p <= 0: return icp["noPrice"], "цена не указана"
    if cur != "RUB": return icp["foreignPrice"], f"цена в {cur}"
    for lim, k in icp["price"]:
        if p < lim: return k, f"{round(p / 1000)} тыс. ₽"
    return 1.0, ""


def deadline_k(l, today, icp):
    if l.get("cancelled"): return icp["deadline"]["closed"], "отменена"
    dl = l.get("deadline") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl): return icp["deadline"]["none"], "срока нет"
    d = (dt.date.fromisoformat(dl) - today).days
    k = icp["deadline"]
    if d < 0: return k["closed"], "закрыт"
    if d == 0: return k["today"], "срок сегодня"
    if d <= 2: return k["d1_2"], f"{d} дн."
    if d <= 21: return k["upTo21"], f"{d} дн."
    if d <= 60: return k["upTo60"], f"{d} дн."
    return k["later"], f"{d} дн."


def region_k(l, icp):
    r, c, k = (l.get("region") or "").lower(), l.get("country") or "RU", icp["region"]
    if c != "RU": return k["foreign"], c
    if re.search(r"петербург|спб|санкт", r): return k["spb"], "СПб"
    if "ленинград" in r: return k["lo"], "ЛО"
    if re.search(r"москва|московск", r): return k["moscow"], "Москва"
    return (k["unknown"], "регион не указан") if not r else (k["rf"], "")


def proc_k(l, icp):
    fl, src, k = set(l.get("flags") or []), l.get("source") or "", icp["proc"]
    if "forecast" in fl: return k["forecast"], "прогноз"
    if "plan" in fl: return k["plan"], "позиция плана"
    if "rfq" in fl or l.get("law") == "Коммерческий" or src.startswith(("zmo", "Электронный магазин", "b2b-center")): return k["rfq"], "запрос цен/КП"
    if l.get("law") == "223-ФЗ": return k["law223"], "223-ФЗ"
    if l.get("law") == "44-ФЗ": return k["law44"], "44-ФЗ"
    if l.get("topic") or re.match(r"(fd|cu|v7|reg2|reg3)-", src) or "спикер" in src.lower(): return k["signal"], "сигнал"
    return k["other"], ""


def tier_of(l, ds, fs, pen, mand, title, icp, price_rub):
    t = title.lower().replace("ё", "е")
    corp = bool(CORP.search(l.get("customer") or "")) or "tek" in (l.get("flags") or []) or l.get("law") in ("223-ФЗ", "Коммерческий")
    if mand:
        lift = icp["mandatory"]["corporateRaisesToCore"] and corp and ((price_rub or 0) >= icp["mandatory"]["minPriceRub"] or MAND_LIFT.search(t))
        return "ядро-общ" if lift else "обязательное"
    if PERIPHERY_RX.search(t): return "периферия"
    if set(ds) & CORE_DIRS and pen > -10: return "ядро"
    if fs and ADULT.search(t): return "ядро-общ"
    return "периферия"


def main():
    a = argparse.ArgumentParser()
    for k in ("leadsets", "updates", "verdicts", "watchlist", "buyers", "out", "date"): a.add_argument("--" + k, required=k in ("leadsets", "out", "date"))
    a.add_argument("--fit", default="fit.json"); a.add_argument("--icp", default="icp.json"); a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--state"); a.add_argument("--all", help="записать priority всех лидов в этот файл (для проверки)")
    x = a.parse_args()
    icp = json.load(open(x.icp, encoding="utf-8"))
    fit = Fit((lambda d: d.get("data", d))(json.load(open(x.fit, encoding="utf-8"))))
    m = Matcher(json.load(open(x.dict, encoding="utf-8")))
    today = dt.date.fromisoformat(x.date)
    leads = {}
    for f in glob.glob(os.path.join(x.leadsets, "*.json")):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []): leads.setdefault(str(l["id"]), dict(l))
    if x.updates and os.path.isdir(x.updates):
        for f in glob.glob(os.path.join(x.updates, "*.json")):
            u = json.load(open(f, encoding="utf-8")); u = u.get("data", u)
            l = leads.get(str(u.get("id")))
            if l:
                if u.get("deadline"): l["deadline"] = u["deadline"]
                if u.get("cancelled"): l["cancelled"] = True
    verdicts = {}
    if x.verdicts and os.path.exists(x.verdicts):
        j = json.load(open(x.verdicts, encoding="utf-8")); verdicts = (j.get("data", j)).get("excluded", {})
    wl, cb = set(), {}
    if x.watchlist and os.path.exists(x.watchlist):
        j = json.load(open(x.watchlist, encoding="utf-8")); wl = set((j.get("data", j)).get("inns", {}))
    if x.buyers and os.path.exists(x.buyers):
        j = json.load(open(x.buyers, encoding="utf-8")); cb = {b["inn"]: b for b in (j.get("data", j)).get("buyers", []) if b.get("inn")}
    marked = set()
    if x.state and os.path.isdir(x.state):
        for f in glob.glob(os.path.join(x.state, "**", "*.json"), recursive=True):
            j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
            if j.get("status") and j["status"] != "new": marked.add(os.path.basename(f)[:-5])
    inn_cnt = collections.Counter(str(l.get("customerInn")) for l in leads.values() if l.get("customerInn"))
    name_cnt = collections.Counter((l.get("customer") or "").strip().lower() for l in leads.values() if l.get("customer") and not str(l.get("customer")).lower().startswith("инн"))
    rows = []
    for lid, l in leads.items():
        key = site_key(lid)
        if key in verdicts or not m.classify(l.get("title", ""), l.get("okpd2") or ())[0] and not str(l.get("source", "")).startswith(("Запросы на спикеров", "fd-", "reg2-", "cu-", "v7-")): continue
        kd, wd = deadline_k(l, today, icp)
        if kd == 0: continue
        pf, ds, fs, pen, mand, why = fit.profile(l)
        kp, wp = price_k(l, icp); kg, wg = region_k(l, icp); kc, wc = proc_k(l, icp)
        inn = str(l.get("customerInn") or "")
        n = inn_cnt.get(inn, 0) if inn else name_cnt.get((l.get("customer") or "").strip().lower(), 0)
        r = icp["repeat"]; kr, wr = 1.0, []
        if n >= 3: kr *= r["n3"]; wr.append(f"заказчик: {n} лидов")
        elif n == 2: kr *= r["n2"]; wr.append("заказчик: 2 лида")
        if inn in cb: kr *= r["contracts"]; wr.append(f"контрактов на обучение: {cb[inn].get('contracts')}")
        if inn in wl: kr *= r["watchlist"]; wr.append("в списке наблюдения")
        kr = min(kr, r["cap"])
        kn = icp["noise"] if NOISE.search(l.get("title") or "") else 1.0
        pr = round(pf * kp * kd * kr * kg * kc * kn)
        price_rub = l.get("price") if (l.get("currency") or "RUB") == "RUB" else None
        tier = tier_of(l, ds, fs, pen, mand, l.get("title") or "", icp, price_rub)
        parts = [p for p in (wd, wp, wg, wc, *wr, "похоже на поставку или мероприятие: проверить предмет" if kn < 1 else "") if p]
        rows.append({"id": lid, "key": key, "priority": pr, "tier": tier, "profile": pf, "late": kd <= icp["deadline"]["today"] and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", l.get("deadline") or "")),
                     "parts": "; ".join(parts), "days": (dt.date.fromisoformat(l["deadline"]) - today).days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", l.get("deadline") or "") else None})
    rows.sort(key=lambda r: -r["priority"])
    q = icp["queue"]
    todo = [r for r in rows if r["key"] not in marked and r["priority"] >= q["minPriority"] and (r["days"] is None or r["days"] >= q["minDaysLeft"]) and r["tier"] in ("ядро", "ядро-общ")]
    bands = collections.Counter("≥80" if r["priority"] >= 80 else "60–79" if r["priority"] >= 60 else "40–59" if r["priority"] >= 40 else "<40" for r in rows)
    doc = {"date": x.date, "count": len(rows), "bands": dict(bands), "tiers": dict(collections.Counter(r["tier"] for r in rows)),
           "queue": [r["id"] for r in todo[: q["size"]]],
           "queueInfo": [{"id": r["id"], "priority": r["priority"], "title": (leads[r["id"]].get("title") or "")[:110], "customer": (leads[r["id"]].get("customer") or "")[:50],
                          "price": leads[r["id"]].get("price"), "currency": leads[r["id"]].get("currency") or "RUB", "deadline": leads[r["id"]].get("deadline") or ""} for r in todo[: q["size"]]],
           "items": [{k: r[k] for k in ("id", "priority", "tier", "profile", "parts", "days")} for r in rows[: q["stored"]]],
           "note": "Приоритет лида по профилю МИСБ, цене, сроку, повтору заказчика, региону и типу процедуры (priority.py, константы — icp.json). queue — до %d лучших неотмеченных лидов ядра." % q["size"]}
    json.dump(doc, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    if x.all: json.dump(rows, open(x.all, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"кандидатов {len(rows)}; ярусы {doc['tiers']}; полосы {doc['bands']}; в очереди {len(doc['queue'])}; размер {os.path.getsize(x.out)} байт")


if __name__ == "__main__":
    main()
