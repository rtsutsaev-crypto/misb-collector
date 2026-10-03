"""tg_preview.py — публичное превью Telegram-каналов (t.me/s/<канал>) без WebFetch: id поста, дата, текст, листание назад.

Запуск: python3 tg_preview.py --urls https://t.me/s/practicum_experts [t.me/канал …] [--max-age-days 60] [--max-pages 5] [--pause 3] --out rows.json
rows.json: {"rows": [{id: "канал/номер", title, customer: "", region: "", price, deadline: "", url: "https://t.me/канал/номер",
            law: "", topic, note: "опубл. ДД.ММ.ГГГГ"}], "pages": [{url, status: ok|empty|failed, rows, posts, note}]}
Строка — пост за последние max-age-days дней, где есть сигнальная фраза (ищем / нужен / требуется / приглашаем / запрос … спикер, тренер, эксперт,
преподаватель, лектор, ведущий, автор курса); topic = «<вид>: <начало поста>» — по нему collector.py rows отбирает посты, у которых в заголовке нет слова «обучение».
Канал без превью постов (закрытый, только шапка) — status failed, note «закрытый канал: превью не отдаёт посты»: такой адрес не перечитывать каждый круг.
Листание: ?before=<наименьший номер страницы> до границы по дате или max-pages. Проверки на бота не обходятся; пауза между запросами — pause секунд.
"""
import argparse, datetime as dt, html, json, re, subprocess, time

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
WHO = r"спикер\w*|тренер\w*|эксперт\w*|преподавател\w*|лектор\w*|ведущ\w*|автор\w* курс\w*|фасилитатор\w*|коуч\w*|методолог\w*"
SIGNAL = re.compile(r"(ищем|ищу|нужен|нужна|нужны|требуется|требуются|приглашаем|приглашение|запрос|в поиске|подбираем|набираем|открыт набор)[^.\n]{0,80}?(" + WHO + r")|(" + WHO + r")[^.\n]{0,60}?(требуется|нужен|нужны|приглашаем|в команду)", re.I)
KIND = [(r"спикер|выступлени", "приглашение спикеров"), (r"тренер|преподавател|лектор|коуч|фасилитатор", "запрос на обучение"), (r"эксперт|автор|методолог", "приглашение экспертов")]


def fetch(url):
    r = subprocess.run(["curl", "-s", "-L", "-m", "60", "-A", UA, "-w", "\n%{http_code}", url], capture_output=True, text=True, errors="ignore")
    body, _, code = r.stdout.rpartition("\n")
    return code, body


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>", "\n", s or "")))).strip()


def channel_of(u):
    m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,})", u)
    return m.group(1) if m else ""


def posts_of(page):
    out = []
    for part in page.split('tgme_widget_message_wrap')[1:]:
        dp = re.search(r'data-post="([^"]+)"', part)
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', part)
        tx = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', part, re.S)
        if not dp or not tm: continue
        out.append({"post": dp.group(1), "at": tm.group(1)[:10], "text": clean(tx.group(1)) if tx else ""})
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--urls", nargs="+", required=True)
    a.add_argument("--max-age-days", type=int, default=60)
    a.add_argument("--max-pages", type=int, default=5)
    a.add_argument("--pause", type=float, default=3)
    a.add_argument("--today", default=dt.date.today().isoformat())
    a.add_argument("--out", default="rows.json")
    x = a.parse_args()
    edge = (dt.date.fromisoformat(x.today) - dt.timedelta(days=x.max_age_days)).isoformat()
    rows, pages, seen = [], [], set()
    for ui, u in enumerate(x.urls):
        ch = channel_of(u)
        if not ch:
            pages.append({"url": u, "status": "failed", "rows": 0, "posts": 0, "note": "не адрес канала t.me"}); continue
        url, got, hits, note, status = f"https://t.me/s/{ch}", 0, 0, "", "ok"
        for pg in range(x.max_pages):
            code, body = fetch(url)
            if code != "200":
                status, note = ("failed" if pg == 0 else "ok"), f"HTTP {code}"; break
            ps = posts_of(body)
            if not ps:
                if pg == 0: status, note = "failed", "закрытый канал: превью не отдаёт посты"
                break
            got += len(ps)
            for p in ps:
                if p["at"] < edge: continue
                m = SIGNAL.search(p["text"])
                pid = p["post"]
                if not m or pid in seen: continue
                seen.add(pid); hits += 1
                kind = next((k for rx, k in KIND if re.search(rx, m.group(0), re.I)), "запрос")
                d = p["at"]
                rows.append({"id": pid, "title": p["text"][:200], "customer": "", "region": "", "price": None, "deadline": "",
                             "url": f"https://t.me/{pid}", "law": "", "topic": f"{kind}: {p['text'][:160]}",
                             "note": f"опубл. {d[8:10]}.{d[5:7]}.{d[:4]}"})
            old = min(p["at"] for p in ps)
            nums = [int(p["post"].rsplit("/", 1)[1]) for p in ps if p["post"].rsplit("/", 1)[1].isdigit()]
            if old < edge or not nums: break
            url = f"https://t.me/s/{ch}?before={min(nums)}"
            time.sleep(x.pause)
        pages.append({"url": u, "status": status if status == "failed" else ("ok" if got else "empty"), "rows": hits, "posts": got,
                      "note": note or ("" if got else "постов не найдено")})
        if ui < len(x.urls) - 1: time.sleep(x.pause)
    json.dump({"rows": rows, "pages": pages}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"rows": len(rows), "pages": [(p["status"], p["posts"], p["rows"]) for p in pages]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
