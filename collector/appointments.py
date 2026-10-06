"""appointments.py — назначения руководителей в ТЭК (лента «Кадры» energyland.info, открыта без входа).

Новый директор станции, НПЗ, дочернего общества в первые месяцы заказывает оценку команды, стратсессию, программы развития.
ЕГРЮЛ (head_watch.py) видит только первых лиц юрлиц; директора филиалов (ГРЭС, ТЭЦ, НПЗ в составе группы) там не появляются.
Сигнал sig-appt-<номер новости> (source sig-appointment, флаг early, validUntil = дата новости + 90 дней).
Имена людей не сохраняются: из заголовка вырезаются имя и фамилия, остаются должность и организация; подробности — по ссылке.

Запуск: python3 appointments.py --known known.json --date ГГГГ-ММ-ДД --out appt_out.json [--days 14] [--pages 3]
Выход: {leads, stats}.
"""
import argparse, datetime as dt, html, json, re, time, urllib.request

BASE = "https://energyland.info"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
EVENT = re.compile(r"(?i)назнач|возглав|избран|сменил[аи]?с[ья]|сменил\w* (ген\w*)?директор|гендиректор|кадров\w* (изменени|перестанов)|"
                   r"новы[йм] (генеральн\w+ )?(директор|руководител|глав)|"
                   r"покида\w* пост|освобожд\w* от должност|уход\w* с поста|переизбран")
NOT_EVENT = re.compile(r"(?i)надежные люди|наградил|награжд|юбилей|памят|скончал|умер|герой|конкурс|рейтинг")
NAME = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
COUNTRY = {"Россия": "RU", "Казахстан": "KZ", "Беларусь": "BY", "Узбекистан": "UZ", "Кыргызстан": "KG", "Армения": "AM", "Азербайджан": "AZ"}


def get(url):
    for t in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"}), timeout=40) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(2 + 3 * t)
    return ""


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def no_names(t):
    """Должность и организация без имени и фамилии человека."""
    t = re.sub(rf"((?:назначен|избран|переизбран|утвержден)[аы]?|стал[аи]?|возглавил[аи]?|покидает|покинул[аи]?)\s+{NAME}", r"\1 новый руководитель", t)
    t = re.sub(rf"^{NAME}\s+(?=(назначен|возглавил|избран|стал|покидает|покинул|уходит|освобожд))", "", t)
    t = re.sub(rf",?\s+{NAME}(?=,|$)", "", t)
    return t[:1].upper() + t[1:] if t else t


def org(t):
    m = re.search(r"[«\"]([^»\"]{3,80})[»\"]", t)
    return m.group(1) if m else ""


def parse(page):
    out = []
    for blk in page.split('class="white_block"')[1:]:
        a = re.search(r'<a style="color:#000" href="(/news-show-[^"]+?-(\d+))" title="([^"]+)"', blk)
        d = re.search(r"</b>,\s*(\d{1,2})\.(\d{1,2})\.(\d{2})", blk)
        if not a or not d: continue
        loc = re.search(r'href="/news-location-\d+" title="([^"]+)"', blk)
        cty = re.search(r'href="/news-country-\d+" title="([^"]+)"', blk)
        out.append({"nid": a.group(2), "url": BASE + a.group(1), "title": clean(a.group(3)),
                    "date": f"20{d.group(3)}-{int(d.group(2)):02d}-{int(d.group(1)):02d}",
                    "region": clean(loc.group(1)) if loc else "", "country": COUNTRY.get(clean(cty.group(1)) if cty else "", "RU")})
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--known"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--days", type=int, default=14); a.add_argument("--pages", type=int, default=3)
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    since = (today - dt.timedelta(days=x.days)).isoformat()
    known = set()
    if x.known:
        known = set((json.load(open(x.known, encoding="utf-8")).get("ids") or {}).keys())
    st = {"pages": 0, "items": 0, "fresh": 0, "events": 0, "leads": 0}
    leads, seen = [], set()
    for p in range(x.pages):
        page = get(BASE + ("/news-type-25" if p == 0 else f"/news-type-25-{p}")); st["pages"] += 1
        items = parse(page)
        if not items: st.setdefault("note", f"страница {p + 1}: нет новостей (разметка или доступ)"); break
        for it in items:
            st["items"] += 1
            if it["date"] < since or it["nid"] in seen: continue
            seen.add(it["nid"]); st["fresh"] += 1
            if not EVENT.search(it["title"]) or NOT_EVENT.search(it["title"]): continue
            st["events"] += 1
            sid = "sig-appt-" + it["nid"]
            if sid in known: continue
            t = no_names(it["title"])
            leads.append({"id": sid, "title": "Смена руководителя: " + t, "customer": org(it["title"]) or t,
                          "region": it["region"], "price": None, "deadline": "",
                          "validUntil": (dt.date.fromisoformat(it["date"]) + dt.timedelta(days=90)).isoformat(), "law": "",
                          "url": it["url"], "source": "sig-appointment", "collectedAt": x.date, "country": it["country"],
                          "currency": "RUB", "flags": ["early"], "publishedAt": it["date"],
                          "note": f"Новость energyland.info от {it['date']}. В первые месяцы новый руководитель станции или предприятия "
                                  "заказывает оценку команды, стратсессию, программы развития — предложить знакомство."})
        if items and min(i["date"] for i in items) < since: break
        time.sleep(1)
    st["leads"] = len(leads)
    json.dump({"leads": leads, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
