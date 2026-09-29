#!/usr/bin/env python3
"""Requests for speakers and business trainers from the public boards -> leads (direct demand without a tender).

Boards (all WordPress; robots.txt closes only /wp-admin/): BestSpeakers, ТРЕБУЕТСЯ, Планета тренингов, Найти спикера. The script reads the public REST list of posts
(/wp-json/wp/v2/posts), keeps the posts that are requests (they have the fields «Мероприятие:» and «Выступление спикера:»), and turns them into leads.

  python3 speaker_boards.py --terms dictionary.json --out leads.json [--max-age-days 60] [--date 2026-09-29] [--known ids.txt] [--pause 2]

Rules for a request without a tender number and, usually, without a deadline:
  * id = SP-<12 hex of the text fingerprint>. The same request is posted on several boards (and reposted by the board «BestSpeakers» with its own number), the
    fingerprint is taken from the text of the request, not from the title or the address, so one request gives one lead. The board number stays in note.
  * age: a request without a date of the event is kept for --max-age-days days after publication (default 15), then dropped; deadline is empty («срок не указан»).
  * deadline = the date of the event when the request names it («Дата: 14.07.2026», «Ориентировочные даты»), never invented.
  * customer is empty unless a name is written (requests are mostly anonymous: «один из банков России»); the description goes to note.
  * price = the honorarium only when a number is written («Оплата 500 000 руб.», «до 500 000 руб.»); note says it is the fee of the speaker, not a contract price.
  * relevance: none by topic (any speaker, any topic, any event, as decided 29.09.2026); job-like requests («нужен директор по качеству») are dropped.
  * flags ["rfq"]; law «Коммерческий»; region = the first city; no contacts are collected: the boards show them only after registration or subscription.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 misb-collector"
# kind "wp": public REST list of posts; kind "html": list pages + one page per request (these two boards keep requests in HivePress, its REST API is closed, so only
# the public pages are read: the list page, then each card once, with a pause)
BOARDS = [("BestSpeakers", "wp", "https://bestspeakers.ru"), ("Найти спикера", "wp", "https://find-speaker.ru"),
          ("ТРЕБУЕТСЯ", "html", "https://trebuetsya.ru", "/zaprosy/", r"https://trebuetsya\.ru/zapros/[^\"'#?\s<>]+"),
          ("Планета тренингов", "html", "https://planetatreningov.ru", "/kakie-treningi/zaprosy-na-treningi/", r"https://planetatreningov\.ru/trening/[^\"'#?\s<>]+")]
JOB = re.compile(r"^\s*((поиск|ищем|нужен) спикер\w* №\d+\.\s*)?(нужен|нужна|ищем|требуется|требуются)\s+(директор|менеджер|руководитель|специалист|главный|инженер|бухгалтер|юрист)", re.I)


class Dict:
    def __init__(self, terms):
        self.terms = []
        for t in terms:
            st = [(w[: max(4, len(w) - 2)], max(4, len(w) - 2) + 4) for w in re.findall(r"[\wё-]+", t.lower())]
            if st:
                self.terms.append((t, st))

    def hit(self, text):
        words = re.sub(r"[^\wё-]+", " ", text.lower()).split()
        for t, st in self.terms:
            if all(any(w.startswith(s) and len(w) <= mx for w in words) for s, mx in st):
                return t
        return None


def get_html(url, tries=3):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(str(err)[:100])


def get(url, tries=3):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=45) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 400:  # past the last page
                return []
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(str(err)[:100])


def text_of(h):
    h = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", h)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def field(t, *labels):
    """Value after «Метка:» up to the next label-like start («Слово(а):») or a line end."""
    for lab in labels:
        m = re.search(lab + r"\s*:\s*(.+?)(?=\s+(?:[А-ЯЁ][а-яё]+(?: [а-яё]+){0,3}):\s|\n|$)", t, re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def fingerprint(t):
    m = re.search(r"Мероприятие\s*:\s*(.+)", t, re.S)
    body = (m.group(1) if m else t)[:400].lower()
    return re.sub(r"[^0-9a-zа-яё]+", "", body)[:220]


def dmy(s):
    m = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def money(t):
    m = re.search(r"(?:оплат\w*[^.\n]{0,40}?|гонорар[^.\n]{0,30}?)(?:до\s+)?(\d[\d\s\xa0]{2,}\d)\s*(?:руб|₽|р\b)", t, re.I)
    if not m:
        return None
    v = int(re.sub(r"\D", "", m.group(1)))
    return float(v) if v >= 1000 else None


def city(t):
    for lab in (r"Города?", r"Место проведения", r"Где"):
        v = field(t, lab)
        if v:
            v = re.sub(r"^(?:г\.|город)\s*", "", v.strip(), flags=re.I)
            return re.split(r"[,;—-]\s*", v)[0].strip()[:40]
    if re.search(r"онлайн", t[:600], re.I):
        return "Онлайн"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="")
    ap.add_argument("--out", default="leads.json")
    ap.add_argument("--max-age-days", type=int, default=15)
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--known", default="")
    ap.add_argument("--pause", type=float, default=2.0)
    ap.add_argument("--pages", type=int, default=3, help="pages per board (50 posts of the REST list, or list pages of the HTML boards)")
    ap.add_argument("--cards", type=int, default=40, help="HTML boards: at most this many request pages per run")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date)
    known = set(open(a.known, encoding="utf-8").read().split()) if a.known else set()
    terms = []
    if a.terms:
        d = json.load(open(a.terms, encoding="utf-8")); d = d.get("data", d)
        terms = [w.strip() for k in ("formats", "topics") for g in d.get(k, []) for w in (g.split(",") if isinstance(g, str) else [g]) if len(w.strip()) >= 4 and not re.search(r"[\d/]", w)]
    stat = {}
    groups = {}
    dic = Dict(terms)

    def take(name, st, link, pub, title, t):
        """One request: filter, fingerprint, group with the same request from other boards."""
        if not (re.search(r"Мероприятие\s*:", t) and re.search(r"Выступление (спикера|эксперта)\s*:", t)):
            return
        st["requests"] += 1
        if (today - pub).days > a.max_age_days:
            st["old"] += 1
            return
        # any speaker, any topic, any event: only job-like posts («нужен директор по качеству») are not speaker requests and are dropped;
        # --terms is optional: with it a request that has a dictionary term is counted in stats.by_dictionary (information only)
        if JOB.search(title):
            st["not_relevant"] += 1
            return
        if terms and dic.hit(" ".join([title, field(t, "Мероприятие")])):
            st["by_dictionary"] = st.get("by_dictionary", 0) + 1
        g = groups.setdefault(fingerprint(t), {"items": []})
        g["items"].append({"board": name, "link": link, "pub": pub, "title": title, "text": t})

    for spec in BOARDS:
        name, kind, base = spec[0], spec[1], spec[2]
        st = stat[name] = {"posts": 0, "requests": 0, "old": 0, "not_relevant": 0, "errors": []}
        if kind == "wp":
            for page in range(1, a.pages + 1):
                time.sleep(a.pause)
                try:
                    posts = get(f"{base}/wp-json/wp/v2/posts?per_page=50&page={page}&orderby=date&order=desc&_fields=id,date,link,title,content")
                except RuntimeError as e:
                    st["errors"].append(str(e)); break
                if not posts:
                    break
                oldest = None
                for p_ in posts:
                    st["posts"] += 1
                    pub = dt.date.fromisoformat(p_["date"][:10]); oldest = pub
                    take(name, st, p_["link"], pub, re.sub(r"\s+", " ", text_of((p_.get("title") or {}).get("rendered"))).strip(),
                         text_of((p_.get("content") or {}).get("rendered")))
                if oldest and (today - oldest).days > a.max_age_days:
                    break
        else:
            links = []
            for page in range(1, a.pages + 1):
                time.sleep(a.pause)
                try:
                    h = get_html(base + spec[3] + ("" if page == 1 else f"page/{page}/"))
                except RuntimeError as e:
                    st["errors"].append(str(e)); break
                got = [x.rstrip("/") + "/" for x in re.findall(spec[4], h)]
                new_links = [x for x in dict.fromkeys(got) if x not in links]
                if not new_links:
                    break
                links += new_links
            for link in links[: a.cards]:
                time.sleep(a.pause)
                try:
                    h = get_html(link)
                except RuntimeError as e:
                    st["errors"].append(f"{link}: {e}"); continue
                st["posts"] += 1
                t = text_of(re.sub(r"(?is)<(header|footer|nav|aside).*?</\1>", " ", h))
                m = re.search(r"Добавлено\s+(\d{2})\.(\d{2})\.(20\d{2})", t)
                if not m:
                    continue
                tm = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", h)
                title = re.sub(r"\s+", " ", text_of(tm.group(1))).strip() if tm else ""
                t = t[t.find("Мероприятие"):] if "Мероприятие" in t else t
                take(name, st, link, dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))), title, t)

    leads = []
    order = {b[0]: i for i, b in enumerate(BOARDS)}
    for fp, g in groups.items():
        its = sorted(g["items"], key=lambda x: (order[x["board"]], x["pub"]))
        first = its[0]
        lid = "SP-" + hashlib.sha1(fp.encode()).hexdigest()[:12]
        if lid in known:
            continue
        t = first["text"]
        title = re.sub(r"^(?:Поиск|Ищем|Нужен)\s+спикер\w*\s*№\s*\d+\.?\s*", "", first["title"]).strip(" .")
        num = next((m.group(1) for x in its for m in [re.search(r"№\s*(\d{3,5})", x["title"]) or re.search(r"-(\d{3,5})-", x["link"])] if m), "")
        event = dmy(field(t, r"Дата", r"Ориентировочные даты(?: проведения)?", r"Даты? проведения")) or dmy(field(t, "Мероприятие"))
        cust = re.search(r"Заказчик\s*[—:-]\s*([^.\n]{5,140})", t)
        price = money(t)
        also = ", ".join(sorted({x["board"] for x in its}))
        note = (f"Запрос на спикера или бизнес-тренера, опубликован {first['pub'].strftime('%d.%m.%Y')}; площадки: {also}"
                + (f"; № BestSpeakers {num}" if num else "")
                + (f"; заказчик: {cust.group(1).strip()}" if cust else "")
                + (f"; участники: {field(t, 'Участники')[:120]}" if field(t, "Участники") else "")
                + ("; сумма — гонорар спикеру, не цена договора" if price else "")
                + ("; срок — дата мероприятия" if event else "; срок не указан"))
        leads.append({"collectedAt": a.date, "country": "RU", "currency": "RUB", "customer": "", "deadline": event or "", "flags": ["rfq"], "id": lid, "law": "Коммерческий",
                      "note": note, "platform": "Запросы на спикеров", "price": price, "region": city(t), "source": "Запросы на спикеров · " + first["board"],
                      "title": title, "url": first["link"],
                      "verify": "Запрос с площадки спикеров: заказчик обычно не назван, контакты организатора открываются после регистрации или подписки на площадке, контакты не собираются."})
    json.dump({"collectedAt": a.date, "leads": leads, "stats": stat, "source": "speaker_boards.py"}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    for n, s in stat.items():
        print(n, {k: v for k, v in s.items() if k != "errors"}, "errors:", s["errors"], file=sys.stderr)
    print(len(leads), "leads ->", a.out, file=sys.stderr)


if __name__ == "__main__":
    main()
