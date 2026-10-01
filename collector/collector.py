"""collector.py — детерминированный отбор, дедуп и оценка лидов «Монитора-консолидатора МИСБ».

Один и тот же код в каждом запуске: отбор не зависит от того, как модель прочтёт словарь.
Словарь (config/dictionary) по-прежнему правится на сайте: formats, okpd2, exclude, topics читаются отсюда.

Команды:
  python3 collector.py test                                  — встроенные примеры (должно быть 0 ошибок)
  python3 collector.py audit --dict D --leadsets DIR [--updates DIR] --date ГГГГ-ММ-ДД --out audit.json
  python3 collector.py score --dict D --in leads.json --date ГГГГ-ММ-ДД --out scored.json
  python3 collector.py rows  --dict D --in rows.json --known known.json --source KEY --date ГГГГ-ММ-ДД --out out.json
     rows.json: [{id,title,customer,region,price,deadline,url,okpd2?,eisNumber?}] — строки любого источника;
     out.json: {leads:[новые подходящие], updates:[{id,deadline,prevDeadline,url}], stats:{rows,matched,new,known,dup}}
"""
import argparse, json, re, sys, os, glob, datetime as dt

VERSION = "1.4"

# ---------- морфология ----------
W = r"[а-яёa-z0-9ʻ'’]"          # символ слова


def norm_text(s):
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def norm_key(title):
    return re.sub(r"[^0-9a-zа-я]", "", norm_text(title))[:90]


def stem(w):
    """Снимает только окончание (гласные, й, ь в конце, не больше 3): «видеокурс» остаётся целым,
    «квалификации» → «квалификац», «переподготовка» → «переподготовк»."""
    w = w.strip()
    if len(w) < 5: return w
    m = re.match(r"^(.*?)([аеиоуыэюяйь]{1,3})$", w)
    return m.group(1) if m and len(m.group(1)) >= 4 else w


def phrase_rx(ph):
    """«повышение квалификации» → повышен\\w* квалификац\\w*; короткие слова — целым словом."""
    words = re.findall(r"[а-яёa-z0-9\-+]+", norm_text(ph))
    if not words: return None
    parts = []
    for w in words:
        if len(w) <= 4: parts.append(re.escape(w) + rf"(?!{W})")
        else: parts.append(re.escape(stem(w)) + rf"{W}*")
    return rf"(?<!{W})" + r"[\s\-]+".join(parts)


# ---------- формы обучения (главный признак) ----------
FORM_CORE = [
    r"обучени", r"обучающ\w* ([а-я\-]+ ){0,2}(мероприят|семинар|курс|программ|занят|сесси)", r"обучить", r"обучению",
    r"повышени\w* квалификац", r"квалификац\w* (работник|сотрудник|специалист|персонал|руководител|служащ|кадр)",
    r"переподготовк", r"дополнительн\w* профессиональн\w* (образован|программ|обучен)", r"(?<!\w)дпо(?!\w)",
    r"профессиональн\w* (обучен|образован|подготовк)", r"подготовк\w* (кадр|персонал|специалист|работник|сотрудник|служащ|руководител)",
    r"(?<!\w)курс(ы|ов|ам|а)?(?!\w)(?! валют)", r"семинар", r"вебинар", r"тренинг", r"мастер-класс", r"воркшоп",
    r"практикум", r"лектори", r"(?<!\w)лекци", r"конференци(?![\s\-]*зал)", r"(?<!\w)форум", r"конгресс",
    r"кругл\w* стол", r"стратегическ\w* сесси", r"проектн\w* сесси", r"форсайт-сесси", r"фасилитац",
    r"тимбилдинг", r"командообразован", r"делов\w* игр", r"бизнес-игр", r"бизнес-симуляц", r"кейс-чемпионат",
    r"хакатон", r"конкурс\w* профессиональн\w* мастерств", r"коучинг", r"(?<!\w)коуч", r"менторинг",
    r"наставничеств", r"супервиз", r"оценк\w* (персонал|компетенц|управленческ|кандидат|работник|сотрудник)",
    r"ассессмент", r"диагностик\w* компетенц", r"аттестаци\w* (персонал|работник|сотрудник|руководител|специалист|служащ)",
    r"учебно-методическ", r"электронн\w* (курс|обучен)", r"видеокурс", r"дистанционн\w* (обучен|курс|образоват)",
    r"(?<!\w)(lms|сдо|lxp|scorm)(?!\w)", r"moodle", r"ispring", r"webtutor", r"teachbase", r"педагогическ\w* дизайн",
    r"образовательн\w* (услуг|программ|мероприят|модул|интенсив|сесси)", r"стажировк", r"обмен\w* опыт",
    r"учебн\w* (визит|центр|программ|мероприят|курс|занят|сбор)", r"(?<!\w)(услуг\w* )?лектор", r"спикер",
    r"информационно-консультационн", r"кадров\w* резерв", r"школ\w* (руководител|управленц|лидер|наставник)",
    r"(?<!\w)(mini-)?mba(?!\w)", r"мини-мва", r"executive", r"акселерационн\w* программ",
    r"инструктаж", r"проверк\w* знаний", r"отработк\w* навык", r"развити\w* навык", r"(?<!\w)интенсив(?!н)", r"пожарно-техническ\w* минимум",
    r"оқыту", r"біліктілікті арттыру", r"окутуу", r"квалификацияны жогорулатуу", r"o[ʻ'’`]?qitish", r"malaka oshirish",
    r"masofaviy ta[ʻ'’`]?lim", r"konferensiya", r"(?<!\w)tadbir",
]

# «Слова», которые в formats есть, но сами по себе отбор не дают (слишком общие).
FORM_STOP = {"тестирование", "аттестация", "модерация", "интенсив", "сессия", "курс", "контента для lms",
             "как отдельный тип", "собирать с флагом grant", "участие представителя заказчика", "экспертная оценка",
             "мотивация", "система мотивации", "грейдирование", "корпоративная культура", "лицензии",
             "обучение", "16/72/144/250 часов"}

# Главный предмет закупки — не обучение (если стоит раньше формы обучения).
EXCL_CORE = [
    r"поставк", r"приобретени", r"(?<!\w)закупк\w* (товар|оборудован|мебел|компьютер|техник)", r"монтаж", r"пусконаладк",
    r"строительств", r"ремонт", r"(?<!\w)аренд", r"питани", r"фуршет", r"банкет", r"кейтеринг", r"проживани",
    r"(?<!\w)трансфер", r"авиабилет", r"сувенир", r"полиграф", r"печат\w* (продукц|издани)", r"выставочн\w* стенд",
    r"застройк", r"неисключительн", r"лицензи\w* (на|для) (по|программ)", r"техническ\w* поддержк", r"сопровождени\w* (по|программ|информационн|систем)",
    r"поверк", r"охран\w* (объект|здани|территор)", r"уборк", r"клининг", r"медицинск\w* (осмотр|освидетельств)",
    r"медосмотр", r"вакцин", r"клиническ", r"доклиническ", r"лекарствен",
    r"прав\w* использовани", r"обновлени\w* (программ|по |сред|систем)", r"очистк", r"вывоз", r"благоустройств",
    r"содержани\w* (здани|помещени|территор|имуществ)", r"(техническ\w* )?обслуживани\w* (здани|помещени|систем|оборудован|инженерн|лифт)",
    r"коммунальн", r"электроэнерги", r"типограф", r"литератур", r"(?<!\w)книг", r"дизайн", r"редактировани", r"(?<!\w)сборник", r"экспозици", r"видеонаблюден", r"(?<!\w)связи(?!\w)",
    r"хранени", r"погрузо", r"кофе-брейк", r"(?<!\w)размещени\w* (слушател|участник|гост)", r"оформлени\w* (форум|мероприят|сцен|площадк|зал)",
    r"оценк\w* (справедлив|рыночн)\w* стоимост", r"онлайн-сервис", r"сервис\w* для проведени", r"техническ\w* обеспечени", r"сопровождени\w* (закупок|экземпляр|концесси)", r"(?<!\w)баз\w* данных",
    r"справочно-правов", r"консультант ?плюс", r"(?<!\w)гарант(?!и)", r"лицензи\w* программн", r"изготовлени", r"брендированн",
    r"чат-бот", r"(?<!\w)ужин", r"(?<!\w)обед", r"брендиров", r"абитуриент", r"техническ\w* сопровождени", r"средств\w* размещени",
    r"участи\w* в выставк", r"выставк", r"проектн\w* документаци", r"юридическ", r"дератизац", r"дезинсекц", r"дезинфекц", r"теплоснабж", r"водоснабж", r"грузоперевоз", r"перевозк", r"страховани",
]
# Исключения, которые отбрасывают лот, где бы ни стояли.
EXCL_HARD = [
    r"машинн\w* обучени", r"machine learning", r"федеративн\w* обучени", r"нейросет\w* обучени",
    r"конференц[\s\-]*зал", r"школьник", r"воспитанник",
    r"обучающихся (школ|общеобразоват)", r"общеобразовательн\w* программ", r"(?<!\w)дши(?!\w)", r"музыкальн\w* инструмент",
    r"вокал", r"хореограф", r"живопис", r"тренировк", r"абонемент", r"автошкол",
    r"водител\w* (категори|транспортн)", r"обучени\w* (вождени|плавани)", r"(?<!\w)вождени", r"(обучени|подготовк)\w* водител", r"обучени\w* (систем|модел)\w* (искусствен|ии|машин|классификац|распознаван|нейрон)", r"обучени\w* модел", r"вычислительн\w* мощност", r"отдых\w* (детей|несовершеннолетн)",
    r"организаци\w* отдыха", r"средств\w* обучения", r"учебн\w* (оборудован|мебел|пособи|литератур)",
    r"музейн", r"ведени\w* бухгалтерск", r"расчет\w* заработн", r"средств\w* защиты информации",
    r"стажировк\w* выпускник", r"отбор\w* получател\w* субсиди", r"уборк\w* здани", r"систем\w* охлаждени",
]

# Мягкие исключения: скрывают лот, только если в нём нет признака обучения взрослых/персонала.
EXCL_SOFT = [r"(?<!\w)детск", r"(?<!\w)дет(ей|и|ям)(?!\w)", r"дошкольн", r"несовершеннолетн", r"спортивн", r"(?<!\w)смен(а|ы)(?!\w)",
             r"учащихся", r"студент", r"обучающихся", r"олимпиадн", r"общеразвивающ"]
ADULT = r"работник|сотрудник|персонал|специалист|педагог|учител|воспитател|служащ|руководител|квалификац|переподготовк|конференц|семинар|форум|тренинг|наставник"
# Консалтинг: в профиле МИСБ, но ниже по приоритету, чем обучение.
CONSULT_CTX = r"управлен|персонал|кадр|обучен|развити|стратег|бизнес|предприниматель|семинар|тренинг|форм[еа] |компетенц|организационн|производственн\w* систем|бережлив|hr"
CONSULT = [r"консалтинг", r"консультационн\w* (услуг|сопровожден)", r"консультировани", r"организационн\w* диагностик",
           r"разработк\w* (стратеги|программ\w* развити|систем\w* (мотивац|оплат|грейд|kpi))", r"кадров\w* аудит",
           r"оценк\w* (эффективност|деятельност)\w* (организац|предприят|подразделен)",
           r"стратеги\w* развития"]

MANDATORY = [r"охран\w* труда", r"электробезопасн", r"перв\w* помощ", r"пожарн\w* безопасн", r"пожарно-техническ",
             r"(?<!\w)го и чс", r"гражданск\w* оборон", r"промышленн\w* безопасн", r"промбезопасн", r"(?<!\w)опо(?!\w)",
             r"работ\w* на высоте", r"антитеррористическ"]
TEK = [r"газпром", r"роснефт", r"лукойл", r"транснефт", r"россет", r"(?<!\w)ржд(?!\w)", r"железн\w* дорог", r"росатом",
       r"русгидро", r"интер ?рао", r"сибур", r"новатэк", r"татнефт", r"сургутнефтегаз", r"зарубежнефт", r"нефт", r"(?<!\w)газ",
       r"энерг", r"электросет", r"теплосет", r"металлург", r"(?<!\w)ммк(?!\w)", r"нлмк", r"северстал", r"евраз", r"норникел",
       r"русал", r"уголь", r"угольн", r"горно", r"(?<!\w)тэк(?!\w)", r"аэрофлот", r"(?<!\w)порт(?!\w)"]
SMP = [r"(?<!\w)смп(?!\w)", r"сонко", r"субъект\w* малого"]
LICENSE = [r"лицензи\w* на (осуществлени\w* )?образовательн"]
GRANT = [r"(?<!\w)грант", r"субсиди\w* (нко|некоммерческ)"]
LEAD_IN = re.compile(r"^\W*((оказание|выполнение|право заключения договора|заключение договора|закупка|услуги?|работы?)\s+"
                     r"((на|по|для)\s+)?(оказани\w+|выполнени\w+)?\s*(услуг\w*)?\s*(по|на)?\s*)+")


ORG_RX = re.compile(r"(?<!\w)(г[абк]?н?[оу]у|фг[аб]?н?оу|мб?оу|мау|ано|ча?оу|оу|нок?у|фгбу|гбу|гау|мбу|мку)\s+(дпо|во|спо|до)(?!\w)(\s+[«\"]?[а-яa-z\-]+[»\"]?)?")


class Matcher:
    def __init__(self, dictionary):
        d = dictionary.get("data", dictionary)
        self.d = d
        forms = list(FORM_CORE)
        for item in d.get("formats", []):
            if re.match(r"\s*обязательное обучение\s*:", item, re.I): continue   # после двоеточия — темы, не формы
            for ph in re.split(r"[,;:()]", item):
                p = norm_text(ph)
                if not p or p in FORM_STOP or len(p) < 5 or re.search(r"\d+/\d+", p): continue
                if " " not in p and len(p) < 7 and not p.isascii(): continue   # одиночные короткие — только из FORM_CORE
                rx = phrase_rx(p)
                if rx: forms.append(rx)
        self.form = re.compile("|".join(f"(?:{x})" for x in forms))
        topics = []
        for item in d.get("topics", []):
            for ph in re.split(r"[,;:()]", item):
                p = norm_text(ph)
                if len(p) >= 5 and not re.search(r"\d", p):
                    rx = phrase_rx(p)
                    if rx: topics.append(rx)
        self.topic = re.compile("|".join(f"(?:{x})" for x in topics)) if topics else None
        excl = list(EXCL_HARD)
        for item in d.get("exclude", []):
            if item.startswith("лоты стран"): continue
            for ph in item.split(","):
                p = norm_text(ph)
                if len(p) >= 5 and p not in ("спорт", "дошкольное", "размещение", "проживание", "питание", "аренда", "дети", "водители", "книги",
                                              "тестирование", "полиграфия", "поставка товаров"):
                    excl.append(phrase_rx(p))
        self.hard = re.compile("|".join(f"(?:{x})" for x in excl if x))
        self.core = re.compile("|".join(f"(?:{x})" for x in EXCL_CORE))
        self.soft = re.compile("|".join(f"(?:{x})" for x in EXCL_SOFT))
        self.adult = re.compile(ADULT)
        self.consult = re.compile("|".join(f"(?:{x})" for x in CONSULT))
        self.okpd = tuple(str(x) for x in d.get("okpd2", []))
        self.flags = {k: re.compile("|".join(v)) for k, v in
                      dict(mandatory=MANDATORY, tek=TEK, smp=SMP, license=LICENSE, grant=GRANT).items()}

    def classify(self, title, okpd2=()):
        """→ (relevant: bool, reason: str, form_terms: list)"""
        t = norm_text(title)
        # названия учреждений («ГАОУ ДПО МЦРПО») — не форма обучения
        t = ORG_RX.sub(" ", t)
        body = LEAD_IN.sub("", t)
        fm = self.form.search(body)
        ok = next((c for c in (okpd2 or ()) if str(c).startswith(self.okpd)), None)
        h = self.hard.search(t)
        if h and re.match(r"водител", h.group(0)) and re.search(r"безработн|дополнительн\w* профессиональн\w* образован", t):
            h = None   # программа для безработных, где водители — лишь одна из профессий (словарь: «обучение безработных» берём)
        if h:
            # «обучение ... для водителей» и т.п. — жёсткое исключение; обязательное обучение не спасает детские/спорт
            return False, f"исключение: {h.group(0)}", []
        sf = self.soft.search(t)
        if sf and not self.adult.search(t):
            return False, f"исключение: {sf.group(0)}", []
        c = self.core.search(body)
        if c and (not fm or c.start() < fm.start()):
            return False, f"предмет не обучение: {c.group(0)}", []
        if fm:
            terms = sorted({m.group(0) for m in self.form.finditer(body)})[:3]
            return True, "форма: " + fm.group(0), terms
        if ok:
            return True, f"ОКПД2 {ok}", []
        cs = self.consult.search(body)
        if cs and (cs.group(0).startswith(("консалтинг", "организационн", "кадров", "разработк")) or re.search(CONSULT_CTX, body)):
            return True, "консалтинг: " + cs.group(0), ["консалтинг"]
        return False, "нет формы обучения", []

    def flag_list(self, title, customer=""):
        s = norm_text(title + " " + (customer or ""))
        return [k for k, rx in self.flags.items() if rx.search(s)]

    def score(self, lead, today):
        """0–100: чем выше, тем ближе к профилю МИСБ и тем срочнее."""
        rel, reason, terms = self.classify(lead.get("title", ""), lead.get("okpd2") or ())
        if not rel: return 0, reason
        s = 30 if reason.startswith("консалтинг") else 40
        t = norm_text(lead.get("title", ""))
        if self.topic:
            s += min(20, 10 * len({m.group(0) for m in self.topic.finditer(t)}))
        fl = set(lead.get("flags") or []) | set(self.flag_list(lead.get("title", ""), lead.get("customer", "")))
        s += 10 if "tek" in fl else 0
        s += 5 if "license" in fl else 0
        s += 5 if "rfq" in fl else 0
        s += 5 if "plan" in fl else 0
        s -= 5 if "mandatory" in fl and not terms else 0
        p = lead.get("price")
        if isinstance(p, (int, float)) and lead.get("currency", "RUB") == "RUB":
            s += 10 if 150_000 <= p <= 10_000_000 else (5 if p > 10_000_000 else 0)
        dl = lead.get("deadline") or ""
        if dl:
            s += 10 if dl >= today else -20
        return max(1, min(100, s)), reason


# ---------- дедуп ----------
class Known:
    def __init__(self):
        self.ids, self.keys = {}, set()

    def add(self, lead, deadline=None):
        dl = lead.get("deadline", "") if deadline is None else deadline
        for i in (lead.get("id"), lead.get("eisNumber")):
            if i: self.ids[str(i)] = dl
        self.keys.add(norm_key(lead.get("title")) + "|" + (lead.get("deadline") or ""))

    def has(self, lead):
        for i in (lead.get("id"), lead.get("eisNumber")):
            if i and str(i) in self.ids: return str(i)
        return None

    def dup(self, lead):
        return norm_key(lead.get("title")) + "|" + (lead.get("deadline") or "") in self.keys

    @classmethod
    def from_db(cls, leadsets_dir, updates_dir=None):
        k = cls()
        for f in glob.glob(os.path.join(leadsets_dir, "*.json")):
            d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
            for l in d.get("leads", []): k.add(l)
        if updates_dir:
            for f in glob.glob(os.path.join(updates_dir, "*.json")):
                d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
                if d.get("id"): k.ids[d["id"]] = d.get("deadline", k.ids.get(d["id"], ""))
        return k


def law_of(i):
    i = str(i)
    if re.fullmatch(r"0\d{18}", i): return "44-ФЗ"
    if re.fullmatch(r"3\d{10}", i): return "223-ФЗ"
    return ""


def build_lead(row, m, source, today, country="RU", currency="RUB"):
    title = re.sub(r"^\s*ОКПД\s*2?:?\s*[\d.]+\s*", "", (row.get("title") or "").strip())
    l = {"id": str(row["id"]).strip(), "title": title, "customer": row.get("customer") or "",
         "region": row.get("region") or "", "price": row.get("price") if isinstance(row.get("price"), (int, float)) and row["price"] > 0 else None,
         "deadline": row.get("deadline") or "", "law": row["law"] if "law" in row else law_of(row["id"]), "url": row.get("url") or "",
         "source": source, "collectedAt": today, "flags": [], "country": row.get("country") or country,
         "currency": row.get("currency") or currency}
    for k in ("customerInn", "contacts", "verify", "platform", "note", "eisNumber", "okpd2", "stage"):
        if row.get(k): l[k] = row[k]
    l["flags"] = sorted(set(row.get("flags") or []) | set(m.flag_list(title, l["customer"])))
    return l


def process_rows(rows, m, known, source, today, max_age_days=30, **kw):
    min_dl = (dt.date.fromisoformat(today) - dt.timedelta(days=max_age_days)).isoformat()
    leads, updates, st = {}, {}, dict(rows=0, matched=0, new=0, known=0, dup=0, old=0)
    for r in rows:
        if not r.get("id") or not r.get("title"): continue
        st["rows"] += 1
        dl = r.get("deadline") or ""
        k = known.has(r)
        if k:
            st["known"] += 1
            if dl and dl != known.ids.get(k, "") and k not in updates:
                updates[k] = {"id": k, "deadline": dl, "prevDeadline": known.ids.get(k, ""), "url": r.get("url") or ""}
            continue
        rel, _, _ = m.classify(r["title"], r.get("okpd2") or ())
        if not rel: continue
        st["matched"] += 1
        if dl and dl < min_dl: st["old"] += 1; continue
        if str(r["id"]) in leads: continue
        if known.dup(r): st["dup"] += 1; continue
        leads[str(r["id"])] = build_lead(r, m, source, today, **kw)
    st["new"] = len(leads)
    return list(leads.values()), list(updates.values()), st


# ---------- встроенные примеры ----------
CASES = [
    (True, "Оказание услуг по обучению работников по охране труда"),
    (True, "Оказание услуг по повышению квалификации Контрактная система в сфере закупок"),
    (True, "Курсы повышения квалификации"),
    (True, "Оказание образовательных услуг по дополнительной профессиональной программе"),
    (True, "Проведение стратегической сессии для руководителей"),
    (True, "Организация и проведение семинара, включая питание и проживание участников"),
    (True, "Оказание услуг по организации и проведению конференции"),
    (True, "информационно-консультационные услуги в формате тренинга"),
    (True, "Оценка персонала и формирование кадрового резерва"),
    (True, "Услуги по проведению профессиональной переподготовки"),
    (True, "Оказание услуг по проведению бизнес-тренинга «Переговоры»"),
    (True, "Мастер-класс по публичным выступлениям"),
    (True, "Xodimlarni o'qitish xizmatlari"),
    (True, "Біліктілікті арттыру курстары бойынша қызметтер"),
    (False, "Оказание услуг по передаче данных"),
    (False, "Поставка накопителей данных"),
    (False, "Оказание услуг по поверке средств измерений и аттестации испытательного оборудования"),
    (False, "Оказание услуг организации питания участников конференции"),
    (False, "Поставка оборудования для учебного класса"),
    (False, "Обучение детей плаванию"),
    (False, "Разработка модели машинного обучения"),
    (False, "Аренда конференц-зала"),
    (False, "Поставка средств обучения"),
    (False, "Маркетинговое исследование рынка"),
    (False, "Предоставление неисключительных прав на программное обеспечение с обучением пользователей"),
    (False, "Оказание услуг по подготовке водителей категории С"),
    (False, "Изготовление полиграфической продукции для форума"),
    (True, "Услуги по организации конференций, семинаров, форумов, спортивных мероприятий"),
    (False, "Оказание услуг по очистке крыш от снега для нужд ГАОУ ДПО МЦРПО"),
    (False, "Защитное вождение, специализированное обучение зимнему вождению"),
    (False, "Предоставление прав использования ПО «Пакет обновления среды электронного обучения»"),
    (True, "Отработка навыков работы на высоте, эвакуации, спасения"),
    (True, "Организация и проведение Интенсива для молодых предпринимателей"),
    (False, "Оказание типографских услуг по печати сборника материалов конференции"),
    (False, "Литература для обучения"),
    (False, "Передача видеопотока с аналоговых видеокамер"),
    (False, "Оказание услуг по разработке дизайна электронного сборника по итогам научно-практической конференции"),
    (True, "Разработка видеокурса по охране труда"),
    (False, "Закупка баз данных справочной системы «Система Охрана труда +»"),
    (False, "Оказание услуги по техническому обеспечению Круглого стола"),
    (False, "Оказание консультационных услуг по контролю технических параметров функционирования объектов"),
    (False, "Оказание юридических и иных консультационных услуг по сопровождению исполнения концессионного соглашения"),
    (False, "Оказание услуг по предоставлению лицензий программного обеспечения для проведения онлайн-мероприятий"),
    (False, "Услуги по проведению интенсивов в рамках олимпиадной подготовки"),
    (False, "Оказание услуг по предоставлению вычислительных мощностей для обучения систем искусственного интеллекта"),
    (True, "Оказание консультационных услуг по развитию производственной системы"),
    (True, "Оказание услуг по организации участия в XXIII Международном банковском форуме"),
    (True, "Оказание информационно-консультационных услуг в форме проведения семинаров"),
    (True, "Разработка и проведение обучающих экспертно-консультационных мероприятий «Культурный код лидера»"),
    (True, "Оказание услуг по специальной подготовке сотрудников органов принудительного исполнения"),
    (False, "Выполнить работы по теме: «Обучение модели классификации маммографических изображений»"),
    (False, "Услуги по организации ужина в рамках международного цифрового форума"),
    (False, "Оказание услуг по брендированию атрибутики для участников тренинг-марафона"),
    (False, "Оказание агентских услуг по привлечению абитуриентов для обучения"),
    (False, "Оказание услуг по обеспечению участия в выставке «Транспорт России»"),
    (True, "Организация участия в мероприятиях Российско-Китайского форума"),
    (False, "Обучение водителям по перевозке опасного груза"),
    (True, "Оказание услуг по организации профессионального обучения, получения дополнительного профессионального образования безработными гражданами, в т.ч. водителей категории С"),
    (False, "Оказание услуг по размещению слушателей курсов повышения квалификации"),
    (False, "Услуга по организации кофе-брейков в рамках форума «Бизнес-СТАРТ»"),
    (False, "Оказание услуг по оформлению Форума «ПроНаставничество»"),
    (False, "Оказания услуг по оценке справедливой стоимости имущества ООО «ЕЭТ»"),
    (False, "Онлайн-сервис для проведения вебинаров и видеоконференций"),
    (True, "Гигиеническая подготовка и аттестация сотрудников детского сада"),
    (False, "Услуги по организации и проведению профильных смен для детей"),
    (True, "Оказание услуг в области управленческого консалтинга"),
    (False, "Оказание услуг по адаптации и сопровождению экземпляров систем КонсультантПлюс"),
    (False, "Услуга по организации трансфера участников семинара"),
]


def run_test(m):
    bad = [(exp, t, m.classify(t)[1]) for exp, t in CASES if m.classify(t)[0] != exp]
    for exp, t, why in bad: print(("ПРОПУЩЕН " if exp else "ЛИШНИЙ  ") + t + "  ← " + why)
    print(f"примеров {len(CASES)}, ошибок {len(bad)}")
    return not bad


def site_key(i):
    """Ключ лида так же, как keyOf() на сайте: id как есть или FNV-1a-хеш."""
    s = str(i or "")
    if re.fullmatch(r"[A-Za-z0-9_\-.~:@+]{1,150}", s): return s
    h = 2166136261
    for ch in s: h ^= ord(ch); h = (h * 16777619) & 0xFFFFFFFF
    d, o = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while True:
        o = d[h % 36] + o; h //= 36
        if not h: break
    return "h" + o + "-" + re.sub(r"[^A-Za-z0-9]", "", s)[:40]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=["test", "audit", "score", "rows", "verdicts"])
    a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--leadsets"); a.add_argument("--updates"); a.add_argument("--in", dest="inp")
    a.add_argument("--known"); a.add_argument("--source", default=""); a.add_argument("--country", default="RU")
    a.add_argument("--currency", default="RUB"); a.add_argument("--date", default=dt.date.today().isoformat())
    a.add_argument("--out", default="out.json")
    x = a.parse_args()
    m = Matcher(json.load(open(x.dict, encoding="utf-8")))
    if x.cmd == "test":
        sys.exit(0 if run_test(m) else 1)
    if x.cmd == "score":
        leads = json.load(open(x.inp, encoding="utf-8"))
        for l in leads: l["score"], l["why"] = m.score(l, x.date)
        json.dump(leads, open(x.out, "w", encoding="utf-8"), ensure_ascii=False); return
    if x.cmd == "rows":
        known = Known()
        if x.known:
            kj = json.load(open(x.known, encoding="utf-8"))
            known.ids = kj.get("ids", {}); known.keys = set(kj.get("keys", []))
        rows = json.load(open(x.inp, encoding="utf-8"))
        if isinstance(rows, dict):                     # вывод pages.py: {"rows": [...], "pages": [...]}
            rows = rows.get("rows", [])
        leads, upd, st = process_rows(rows, m, known, x.source, x.date,
                                      country=x.country, currency=x.currency)
        json.dump({"leads": leads, "updates": upd, "stats": st, "version": VERSION}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
        print(json.dumps(st, ensure_ascii=False)); return
    if x.cmd == "verdicts":
        # meta/collector-verdicts: лиды базы, которые фильтр не пропустил бы; сайт показывает их как «исключено по профилю»
        ex, seen = {}, set()
        for f in sorted(glob.glob(os.path.join(x.leadsets, "*.json"))):
            d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
            for l in d.get("leads", []):
                i = l.get("id")
                if not i or i in seen: continue
                seen.add(i)
                if str(l.get("source", "")).startswith(("gosplan:forecast", "Запросы на спикеров", "speakers")): continue
                rel, why, _ = m.classify(l.get("title", ""), l.get("okpd2") or ())
                if not rel: ex[site_key(i)] = why[:60]
        doc = {"version": "collector " + VERSION, "date": x.date, "excluded": ex,
               "note": "Лиды, которые фильтр сбора (config/collectorlib) не пропустил бы. Сайт показывает их как «исключено по профилю» (по умолчанию скрыты), кроме лидов в работе. Ничего не удалено."}
        json.dump(doc, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        if os.path.getsize(x.out) > 240_000:   # лимит документа базы 256 КБ: сокращаем причины до категории
            doc["excluded"] = {k: v.split(":")[0][:20] for k, v in ex.items()}
            json.dump(doc, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"лидов {len(seen)}, исключено {len(ex)}, размер {os.path.getsize(x.out)} байт"); return
    # audit: оценка всей базы; ничего не удаляет, только список скрытых и баллы
    rows, seen = [], set()
    for f in sorted(glob.glob(os.path.join(x.leadsets, "*.json"))):
        d = json.load(open(f, encoding="utf-8")); d = d.get("data", d); doc = os.path.basename(f)[:-5]
        for l in d.get("leads", []):
            if l.get("id") in seen: continue
            seen.add(l.get("id"))
            sc, why = m.score(l, x.date)
            rows.append({"id": l.get("id"), "doc": doc, "source": l.get("source", ""), "title": l.get("title", "")[:200],
                         "deadline": l.get("deadline", ""), "score": sc, "why": why})
    json.dump({"date": x.date, "version": VERSION, "rows": rows}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    hid = sum(1 for r in rows if r["score"] == 0)
    print(f"лидов {len(rows)}, скрыть {hid} ({100 * hid // max(1, len(rows))}%)")


if __name__ == "__main__":
    main()
