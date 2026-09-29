#!/usr/bin/env python3
"""Turn bidzaar_items.json (from bidzaar_count.py, run on a Russian server) into a leadset document.

  python3 bidzaar_to_leads.py bidzaar_items.json --list                 # open records that look like training, for review
  python3 bidzaar_to_leads.py bidzaar_items.json --numbers 378-313,379-392 --out leadset.json --date 2026-09-29

The site's classifier (classify_titles.cjs) only excludes goods, holidays and the like; on a search result it labels almost everything
"relevant", so the list is reviewed by a person: the training-like test here is a title check, the numbers of the accepted records are passed to --numbers.
Lead fields follow the leadsets of the site: id BZ-<number>, law "Коммерческий", flag rfq, price null, url /app/process/light/<id>.
"""
import argparse
import datetime as dt
import json
import re

TRAIN = re.compile(r"(обучен|курс[аыов ]|программ[аыу] (обуч|повыш|подготов)|повышени\w* квалификац|переподготовк|семинар|тренинг|вебинар|мастер-класс|"
                   r"конференц|форум|образовательн|стратегическ\w* сесси|коучинг|наставнич|менторинг|ассессмент|оценк\w* (персонал|компетенц)|"
                   r"деловая игра|тимбилдинг|дополнительн\w* профессиональн|профессиональн\w* подготовк|аттестаци)", re.I)
NOISE = re.compile(r"(поставк|ремонт|аренд|уборк|питани|лекарств|строительств|монтаж|изготовлен|приобретен|спецодежд|оборудован|запасн|материал|канцеляр|бумаг|мебел|продаж|реализаци)", re.I)
REGION = {"г Москва": "Москва", "г Санкт-Петербург": "Санкт-Петербург", "Калужская обл": "Калужская область",
          "Ханты-Мансийский Автономный округ - Югра": "Ханты-Мансийский автономный округ — Югра"}


def opened(it, now):
    try:
        return dt.datetime.fromisoformat(it["acceptanceEndDate"].replace("Z", "+00:00")) > now
    except Exception:  # noqa: BLE001
        return False


ap = argparse.ArgumentParser()
ap.add_argument("items")
ap.add_argument("--list", action="store_true")
ap.add_argument("--numbers", default="")
ap.add_argument("--out", default="leadset.json")
ap.add_argument("--date", default=dt.date.today().isoformat())
a = ap.parse_args()
d = json.load(open(a.items, encoding="utf-8"))
now = dt.datetime.now(dt.timezone.utc)
if a.list:
    for it in sorted((x for x in d["items"] if opened(x, now) and TRAIN.search(x["name"] or "") and not NOISE.search(x["name"] or "")), key=lambda x: x["acceptanceEndDate"]):
        print(it["number"], it["acceptanceEndDate"][:10], it["companyName"], "|", it["name"][:110])
else:
    by = {it["number"]: it for it in d["items"]}
    leads = []
    for n in [x.strip() for x in a.numbers.split(",") if x.strip()]:
        it = by[n]
        pub = it["publishDate"][:10]
        leads.append({"collectedAt": a.date, "country": "RU", "currency": "RUB", "customer": it["companyName"], "deadline": it["acceptanceEndDate"][:10],
                      "flags": ["rfq"], "id": "BZ-" + n, "law": "Коммерческий",
                      "note": f"Bidzaar, публичный запрос № {n}, опубликован {pub[8:10]}.{pub[5:7]}.{pub[:4]}; найден по словам словаря: {', '.join(it['words'][:6])}",
                      "platform": "Bidzaar", "price": None, "region": REGION.get(it["region"], it["region"]), "source": "bidzaar", "title": it["name"].strip(),
                      "url": "https://bidzaar.com/app/process/light/" + it["id"],
                      "verify": "Ссылка собрана по шаблону страниц списка Bidzaar (/app/process/light/<id>); из облака не проверялась: сайт показывает капчу. Полное описание закупки может требовать входа."})
    json.dump({"collectedAt": a.date, "leads": leads, "source": "Bidzaar: ручной сбор с российского сервера"}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(len(leads), "leads ->", a.out)
