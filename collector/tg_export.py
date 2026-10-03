#!/usr/bin/env python3
"""tg_export.py — запросы на спикеров, тренеров и обучение из официального экспорта чата Telegram Desktop -> лиды.

Экспорт делает участник чата сам (Telegram Desktop: чат → ⋮ → «Экспорт истории чата» → формат JSON, без медиа). Скрипт ничего не скачивает из Telegram, не входит в чаты и не пишет в них;
обход доступа к закрытым чатам не предусмотрен. Читает result.json одного чата (поле messages) или выгрузки всего аккаунта (chats.list[].messages).

  python3 tg_export.py --in result.json [--in other.json ...] --source-name "Digital Learning" --out leads.json [--max-age-days 30] [--date 2026-10-04]

Правила (как у speaker_boards.py): сообщение — запрос, если в нём есть слово спроса («ищем», «нужен», «требуется», «подскажите», «посоветуйте», «кто проводит», «организуем») И предмет
(спикер, тренер, ведущий, преподаватель, лектор, модератор, обучение, тренинг, семинар, курс, воркшоп, интенсив, мастер-класс, конференц). Вакансии («в штат», «вакансия», «резюме», «зарплата»)
и предложения услуг («предлагаю», «провожу», «приглашаю на») отбрасываются. id = TG-<12 hex отпечатка текста>: один запрос, пересланный в несколько чатов, даёт один лид.
Срок не придумывается: deadline пустой, пока в тексте нет даты вида ДД.ММ.ГГГГ. Заказчик пустой, контакты не собираются (автор сообщения в лид не пишется) — ссылка на запрос только у публичных чатов.
Выход: {"leads": [...], "stats": {...}} для save_leads.py (--key tg-export).
"""
import argparse, datetime as dt, hashlib, json, re

ASK = re.compile(r"(?<![а-яё])(ищем|ищу|нужен|нужна|нужны|нужно найти|требуется|требуются|подскажите|посоветуйте|порекомендуйте|кто проводит|кто может провести|организуем|организуем|подбираем)", re.I)
THING = re.compile(r"(?<![а-яё])(спикер|тренер|ведущ|преподавател|лектор|модератор|фасилитатор|обучени|тренинг|семинар|курс|воркшоп|интенсив|мастер-класс|конференц|программ\w* развития)", re.I)
NOISE = re.compile(r"(?<![а-яё])(в штат|вакансия|резюме|зарплат|оклад|удалённая работа|предлагаю|провожу|приглашаю на|запись на|скидк|промокод|набор в группу)", re.I)
DATE = re.compile(r"\b(\d{1,2})\.(\d{2})\.(20\d{2})\b")


def flat(t):
    return "".join(x if isinstance(x, str) else x.get("text", "") for x in t) if isinstance(t, list) else (t or "")


def chats(j):
    if "messages" in j: return [(j.get("name") or "", j.get("id"), j["messages"], j.get("type", ""))]
    return [(c.get("name") or "", c.get("id"), c.get("messages") or [], c.get("type", "")) for c in (j.get("chats") or {}).get("list", [])]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--in", dest="inp", action="append", required=True); a.add_argument("--source-name", default="")
    a.add_argument("--out", required=True); a.add_argument("--max-age-days", type=int, default=30)
    a.add_argument("--date", default=dt.date.today().isoformat())
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date); lo = today - dt.timedelta(days=x.max_age_days)
    leads, seen, st = [], set(), {"messages": 0, "old": 0, "noise": 0, "notask": 0, "dup": 0, "leads": 0}
    for f in x.inp:
        for name, cid, msgs, ctype in chats(json.load(open(f, encoding="utf-8"))):
            for m in msgs:
                if m.get("type") != "message": continue
                text = re.sub(r"\s+", " ", flat(m.get("text"))).strip()
                st["messages"] += 1
                try: d = dt.datetime.fromisoformat(m.get("date", "")).date()
                except ValueError: continue
                if d < lo: st["old"] += 1; continue
                if NOISE.search(text): st["noise"] += 1; continue
                if not (ASK.search(text) and THING.search(text)) or len(text) < 40: st["notask"] += 1; continue
                fp = hashlib.sha1(re.sub(r"[^а-яa-z0-9]+", "", text.lower())[:300].encode()).hexdigest()[:12]
                if fp in seen: st["dup"] += 1; continue
                seen.add(fp)
                dl = ""
                for dd, mm, yy in DATE.findall(text):
                    try:
                        c = dt.date(int(yy), int(mm), int(dd))
                        if c >= today: dl = c.isoformat(); break
                    except ValueError: pass
                leads.append({"id": "TG-" + fp, "title": text[:200], "customer": "", "region": "", "price": None, "deadline": dl, "law": "Коммерческий",
                              "url": "", "source": "tg-export", "collectedAt": x.date, "flags": ["rfq"], "country": "RU", "currency": "RUB", "publishedAt": d.isoformat(),
                              "note": f"Запрос из чата Telegram «{x.source_name or name}» от {d.isoformat()} (официальный экспорт участника): {text[:600]}"})
    st["leads"] = len(leads)
    json.dump({"leads": leads, "updates": {}, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
