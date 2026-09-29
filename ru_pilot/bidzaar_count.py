#!/usr/bin/env python3
"""Measurement: how many OPEN purchase requests on Bidzaar match the training words (public JSON list, no login).

The list is the one the site's own public page loads (GET .../procedures/available, search by one word). Bidzaar shows a captcha to
addresses outside Russia, so this is run from a Russian address. robots.txt is read first; one request at a time, 2 s pause; honest
User-Agent; TLS verification on. Uses fetch() and robots_allows() of pilot.py (same folder).

  python3 bidzaar_count.py                 # writes bidzaar_items.json and bidzaar_out.txt
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.parse

try:
    from pilot import fetch, robots_allows
except ImportError:  # run from the repository
    from probe_ru import fetch, robots_allows

BASE = "https://bidzaar.com/api/process/light/procedures/available"
WORDS = ["обучение", "повышение квалификации", "профессиональная переподготовка", "образовательные услуги", "семинар", "тренинг",
         "вебинар", "конференция", "форум", "стратегическая сессия", "коучинг", "оценка персонала", "ассессмент", "наставничество",
         "консультационные услуги", "мастер-класс", "корпоративное обучение", "командообразование"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--pause", type=float, default=2.0)
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--out", default="bidzaar_out.txt")
    ap.add_argument("--items", default="bidzaar_items.json")
    a = ap.parse_args()
    ok, why = robots_allows(a.base, 30)
    if not ok:
        print("robots.txt запрещает:", a.base)
        return 1
    now = dt.datetime.now(dt.timezone.utc)
    per_word, items, status_cnt = [], {}, {}
    for w in WORDS:
        got, total = 0, None
        for p in range(1, a.pages + 1):
            q = urllib.parse.urlencode({"paging.page": p, "paging.size": a.size, "sorting.key": "searchRank", "sorting.direction": "desc", "search": w})
            time.sleep(a.pause)
            st, _, text, err = fetch(f"{a.base}?{q}", 40)
            if st != 200:
                print(f"[{w}] p{p}: HTTP {st} {err}")
                break
            try:
                j = json.loads(text)
            except ValueError:
                print(f"[{w}] p{p}: not json")
                break
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
        per_word.append((w, total, got))
        print(f"[{w}] totalCount={total} fetched={got}", flush=True)

    def is_open(it):
        try:
            return dt.datetime.fromisoformat(it["acceptanceEndDate"].replace("Z", "+00:00")) > now
        except Exception:  # noqa: BLE001
            return False

    opened = [it for it in items.values() if is_open(it)]
    opened.sort(key=lambda it: it["acceptanceEndDate"])
    json.dump({"at": now.isoformat(timespec="seconds"), "words": per_word, "items": list(items.values())}, open(a.items, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = [f"Bidzaar, публичный список, {now:%Y-%m-%d %H:%M} UTC. robots.txt: {why}", "",
         "Слово | всего по данным сайта | получено", *[f"{w} | {t} | {g}" for w, t, g in per_word], "",
         f"Уникальных закупок: {len(items)}, из них с приёмом заявок после сегодняшнего дня: {len(opened)}. Значения status: {status_cnt}", "",
         "Срок заявок | Заказчик | Название | № | слова"]
    for it in opened[:150]:
        L.append(f"{it['acceptanceEndDate'][:10]} | {str(it['companyName'])[:32]} | {str(it['name'])[:110]} | {it['number']} | {','.join(it['words'])[:40]}")
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\nГОТОВО: {a.out} (и {a.items}). Уникальных {len(items)}, открытых {len(opened)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
