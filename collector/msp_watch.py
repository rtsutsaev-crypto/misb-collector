"""msp_watch.py — список наблюдения «инфраструктура поддержки МСП» из реестра Корпорации МСП (monitoring.corpmsp.ru/reestroi.html):
центры «Мой бизнес», фонды поддержки, центры поддержки предпринимательства и экспорта, инкубаторы, технопарки, МФЦ для бизнеса.
Они регулярно закупают тренинги, акселерационные программы, «Школу предпринимательства» — заказчики МИСБ.

Запуск: python3 msp_watch.py --html reestroi.html --date ГГГГ-ММ-ДД --out watchlist-extra.json   (или --url вместо --html)
Выход — документ meta/watchlist-extra: {inns: {ИНН: {level: "инфраструктура МСП", type, name, region}}, updatedAt, source, total}.
gosplan_delta.py читает его вторым --watch (туда же contracts_build.py добавляет gph_buyers) вместе с meta/watchlist (расширенный отбор: «акселер», «образоват», «развитие кадров» и др.).
Исключённые из реестра записи не берутся.
"""
import argparse, html, json, re, urllib.request

URL = "https://monitoring.corpmsp.ru/reestroi.html"


def cells(row):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row)]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--html"); a.add_argument("--url", default=""); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    x = a.parse_args()
    if x.html:
        s = open(x.html, encoding="utf-8", errors="ignore").read()
    else:
        s = urllib.request.urlopen(urllib.request.Request(x.url or URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read().decode("utf-8", "ignore")
    inns = {}
    for r in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", s):
        c = cells(r)
        if len(c) < 16 or not re.fullmatch(r"\d{10}", c[6] or ""): continue
        if c[15].lower().startswith("да"): continue                         # исключена из реестра
        if re.search(r"(?i)микрофинанс|гарантийн|лизинг", c[8]) and not re.search(r"(?i)центр|инкубатор|технопарк|палат", c[8]): continue   # финансовые — обучение не закупают
        addr = c[9]
        reg = re.search(r"(ОБЛАСТЬ [А-ЯЁ\-]+|[А-ЯЁ\-]+ ОБЛАСТЬ|КРАЙ [А-ЯЁ\-]+|[А-ЯЁ\-]+ КРАЙ|РЕСПУБЛИКА [А-ЯЁ\- ]+?(?=,)|Г(ОРОД)?\.? МОСКВА|Г(ОРОД)?\.? САНКТ-ПЕТЕРБУРГ|[А-ЯЁ\-]+ АВТОНОМНЫЙ ОКРУГ)", addr.upper())
        inns[c[6]] = {"level": "инфраструктура МСП", "type": c[8][:120], "name": (c[5] if c[5] and c[5] != "-" else c[4])[:150],
                      "region": reg.group(0).title() if reg else ""}
    doc = {"inns": inns, "total": len(inns), "updatedAt": x.date, "source": URL,
           "note": "Организации инфраструктуры поддержки МСП (реестр Корпорации МСП): заказчики тренингов и акселерационных программ"}
    json.dump(doc, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"total": len(inns), "bytes": len(json.dumps(doc, ensure_ascii=False).encode())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
