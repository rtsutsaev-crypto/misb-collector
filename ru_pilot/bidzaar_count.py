#!/usr/bin/env python3
"""Measurement: how many OPEN purchase requests on Bidzaar match the words of the dictionary (public JSON list, no login).

The list is the one the site's own public page loads (GET .../procedures/available, search by one word). Bidzaar shows a captcha to
addresses outside Russia, so this is run from a Russian address. robots.txt is read first; one request at a time, 2 s pause; honest
User-Agent; TLS verification on. If three answers in a row are not the expected JSON (captcha, 403, 429) the run stops: nothing is bypassed.
Uses fetch() and robots_allows() of pilot.py (same folder).

  python3 bidzaar_count.py --terms dictionary_terms.json   # formats: up to 4 pages of 25 per word, topics: up to 2 pages
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
import urllib.parse

try:
    from pilot import fetch, robots_allows
except ImportError:  # run from the repository
    from probe_ru import fetch, robots_allows

BASE = "https://bidzaar.com/api/process/light/procedures/available"
DEFAULT = {"formats": ["обучение", "повышение квалификации", "профессиональная переподготовка", "образовательные услуги", "семинар", "тренинг",
                       "вебинар", "конференция", "форум", "стратегическая сессия", "коучинг", "оценка персонала", "ассессмент", "наставничество"],
           "topics": []}
TRAIN = re.compile(r"(обучен|курс[аыов ]|программ[аыу] (обуч|повыш|подготов)|повышени\w* квалификац|переподготовк|семинар|тренинг|вебинар|мастер-класс|"
                   r"конференц|форум|образовательн|стратегическ\w* сесси|коучинг|наставнич|менторинг|ассессмент|оценк\w* (персонал|компетенц)|"
                   r"деловая игра|консультационн|тимбилдинг|дополнительн\w* профессиональн|профессиональн\w* подготовк|аттестаци|подготовк\w* (кадр|специалист|персонал))", re.I)
NOISE = re.compile(r"(поставк|ремонт|аренд|уборк|питани|лекарств|строительств|монтаж|изготовлен|приобретен|техническ\w+ обслуживан|"
                   r"спецодежд|оборудован|запасн|материал|канцеляр|бумаг|мебел)", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--terms", default="")
    ap.add_argument("--pause", type=float, default=2.0)
    ap.add_argument("--format-pages", type=int, default=4)
    ap.add_argument("--topic-pages", type=int, default=2)
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--out", default="bidzaar_out.txt")
    ap.add_argument("--items", default="bidzaar_items.json")
    a = ap.parse_args()
    terms = json.load(open(a.terms, encoding="utf-8")) if a.terms else DEFAULT
    plan = [(w, "f", a.format_pages) for w in terms["formats"]] + [(w, "t", a.topic_pages) for w in terms["topics"]]
    ok, why = robots_allows(a.base, 30)
    if not ok:
        print("robots.txt запрещает:", a.base)
        return 1
    now = dt.datetime.now(dt.timezone.utc)
    per_word, items, status_cnt = [], {}, {}
    fails = 0
    stopped = ""

    def is_open(it):
        try:
            return dt.datetime.fromisoformat(it["acceptanceEndDate"].replace("Z", "+00:00")) > now
        except Exception:  # noqa: BLE001
            return False

    def save():
        json.dump({"at": now.isoformat(timespec="seconds"), "stopped": stopped, "words": per_word, "items": list(items.values())},
                  open(a.items, "w", encoding="utf-8"), ensure_ascii=False)

    for n, (w, kind, maxp) in enumerate(plan, 1):
        got, total = 0, None
        for p in range(1, maxp + 1):
            q = urllib.parse.urlencode({"paging.page": p, "paging.size": a.size, "sorting.key": "searchRank", "sorting.direction": "desc", "search": w})
            time.sleep(a.pause)
            st, _, text, err = fetch(f"{a.base}?{q}", 40)
            j = None
            if st == 200:
                try:
                    j = json.loads(text)
                except ValueError:
                    j = None
            if j is None:
                fails += 1
                print(f"[{n}/{len(plan)}] {w} p{p}: HTTP {st} {err} (не JSON), подряд {fails}", flush=True)
                if fails >= 3:
                    stopped = f"остановлено на слове «{w}»: три ответа подряд без JSON (HTTP {st})"
                break
            fails = 0
            rows = next((v for v in j.values() if isinstance(v, list)), [])
            if total is None:
                total = j.get("totalCount")
            for r in rows:
                got += 1
                status_cnt[str(r.get("status"))] = status_cnt.get(str(r.get("status")), 0) + 1
                it = items.setdefault(r["id"], {"words": []})
                it.update({k: r.get(k) for k in ("id", "number", "name", "companyName", "publishDate", "acceptanceEndDate", "finishDate", "status",
                                                 "procedureType", "tradingType", "openType")})
                addr = (r.get("deliveryAddresses") or [{}])[0]
                it["region"] = addr.get("region") or addr.get("city") or ""
                if w not in it["words"]:
                    it["words"].append(w)
            if len(rows) < a.size:
                break
        per_word.append((w, kind, total, got))
        print(f"[{n}/{len(plan)}] {kind} {w}: totalCount={total} fetched={got}", flush=True)
        if n % 25 == 0:
            save()
        if stopped:
            break
    save()

    opened = [it for it in items.values() if is_open(it)]
    opened.sort(key=lambda it: it["acceptanceEndDate"])
    train = [it for it in opened if TRAIN.search(str(it["name"])) and not NOISE.search(str(it["name"]))]
    other = [it for it in opened if it not in train]
    L = [f"Bidzaar, публичный список, {now:%Y-%m-%d %H:%M} UTC. robots.txt: {why}. Словарь версия {terms.get('dictionaryVersion', '-')}",
         f"Слов: {len(plan)} (форматы {len(terms['formats'])}, темы {len(terms['topics'])}). {stopped}", "",
         f"Уникальных закупок: {len(items)}. С приёмом заявок после сегодняшнего дня: {len(opened)}.",
         f"Из открытых похожи на обучение по названию: {len(train)}; остальные: {len(other)}. Значения status: {status_cnt}", "",
         "== Открытые, похожие на обучение (срок заявок | заказчик | название | № | слова) =="]
    for it in train[:250]:
        L.append(f"{it['acceptanceEndDate'][:10]} | {str(it['companyName'])[:30]} | {str(it['name'])[:110]} | {it['number']} | {','.join(it['words'])[:50]}")
    L += ["", f"== Открытые, не похожие на обучение (первые 40 из {len(other)}) =="]
    for it in other[:40]:
        L.append(f"{it['acceptanceEndDate'][:10]} | {str(it['companyName'])[:30]} | {str(it['name'])[:90]} | {','.join(it['words'])[:40]}")
    L += ["", "== Слова: вид | слово | всего по данным сайта | получено =="]
    L += [f"{k} | {w} | {t} | {g}" for w, k, t, g in per_word]
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\nГОТОВО: {a.out} (и {a.items}). Уникальных {len(items)}, открытых {len(opened)}, похожих на обучение {len(train)}. {stopped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
