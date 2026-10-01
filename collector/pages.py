"""pages.py — чтение списков РосТендера, Комтендера, goszakup.kz, MITWORK (eep.mitwork.kz) и подборок B2B-Center (один движок) без WebFetch: все строки страницы, дословно.

Запуск: python3 pages.py --site rostender|komtender|goszakup|mitwork|b2b --urls URL [URL …] --pause 3 --out rows.json [--relay URL --relay-token TOKEN]
rows.json: {"rows": [{id, title, customer, region, price, deadline, url, law}], "pages": [{url, status, rows, note}]}
Дальше строки идут в collector.py rows. Проверки «подождите»/капчи не обходятся: такая страница — status "blocked".
"""
import argparse, html, json, re, subprocess, time

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
HOST = {"rostender": "https://rostender.info", "komtender": "https://www.komtender.ru", "goszakup": "https://old.goszakup.gov.kz", "b2b": "https://www.b2b-center.ru",
        "mitwork": "https://eep.mitwork.kz"}
RELAY = {"url": "", "token": ""}   # --relay/--relay-token: чтение через российский сервер (см. config/collector); токен только из текста запуска


def fetch(url):
    if RELAY["url"]:                       # через релей: не-ASCII в адресе кодируем, код ответа — из заголовка X-Upstream-Status
        import urllib.parse
        u = "".join(c if ord(c) < 128 else urllib.parse.quote(c) for c in url)
        r = subprocess.run(["curl", "-s", "-m", "90", "-H", "X-Relay-Token: " + RELAY["token"], "-G", "--data-urlencode", "url=" + u,
                            "-D", "-", RELAY["url"].rstrip("/") + "/fetch"], capture_output=True, text=True, errors="ignore")
        m = re.search(r"\r?\n\r?\n", r.stdout); head, body = (r.stdout[:m.start()], r.stdout[m.end():]) if m else (r.stdout, "")
        while body.startswith("HTTP/"):                                   # ещё один блок заголовков (редкий случай)
            m = re.search(r"\r?\n\r?\n", body); head, body = (head + body[:m.start()], body[m.end():]) if m else (head + body, "")
        m = re.search(r"(?im)^x-upstream-status:\s*(\d+)", head)
        return (m.group(1) if m else "0"), body
    r = subprocess.run(["curl", "-s", "-L", "-m", "60", "-A", UA, "-w", "\n%{http_code}", url], capture_output=True, text=True, errors="ignore")
    body, _, code = r.stdout.rpartition("\n")
    return code, body


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def price(s):
    s = clean(s).replace("₽", "").replace(" ", "").replace("\xa0", "").replace(",", ".")
    try: v = float(re.sub(r"[^\d.]", "", s)); return v if v > 0 else None
    except ValueError: return None


def parse_goszakup(page):
    """Реестр лотов goszakup.gov.kz (Казахстан): № лота, объявление, наименование, заказчик, сумма, способ, статус.
    Срок в списке не показан — deadline пустой. Берём только статусы «Опубликован…» (приём идёт)."""
    t = re.search(r'<table[^>]*id="search-result"[^>]*>(.*?)</table>', page, re.S)
    rows = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t.group(1) if t else "", re.S)[1:]:
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        ann = re.search(r'href="/ru/announce/index/(\d+)"', r)
        if len(cells) < 6 or not ann: continue
        head = cells[0]
        mm = re.match(r"^(\d+-\S+)\s+(\d+-\d+)\s+(.*?)\s*Заказчик:\s*(.*)$", head)
        if not mm: continue
        lot, title, cust_name = mm.group(1), mm.group(3), mm.group(4)
        enstru = cells[1] if len(cells) > 1 else ""
        if len(title) < 8 and enstru: title = f"{title} ({enstru})"          # «Ус лу га» — берём ещё наименование ЕНС ТРУ
        status = cells[-1]
        if not status.startswith("Опубликован"): continue
        amount = next((price(c) for c in cells if re.fullmatch(r"[\d\s]+\.\d\d", c)), None)
        method = next((c for c in cells if re.search(r"конкурс|аукцион|ценовых|источника|предложени", c, re.I)), "")
        rows.append({"id": lot, "title": title, "customer": cust_name,
                     "region": "Казахстан", "price": amount, "deadline": "", "url": f"{HOST['goszakup']}/ru/announce/index/{ann.group(1)}",
                     "law": "", "country": "KZ", "currency": "KZT", "note": f"goszakup.gov.kz, лот {lot}; {method}; {status}"})
    return rows


def parse_b2b(page):
    """Тематические подборки B2B-Center /search/industry/<тема>/ (агрегатор: РТС-тендер и его ЗМО, ЕИС, ЕАТ «Берёзка»).
    Карточка — schema.org: name, endDate, url, price; организатор, регион, ссылка на площадку-источник, метки (закон, площадка, способ).
    id: номер ЕИС, если карточка ведёт на zakupki.gov.ru (дедуп с ГосПланом), иначе rts-<номер>/eat-<номер>/номер подборки."""
    rows = []
    for c in page.split('<div class="cards" itemscope')[1:]:
        def g(p):
            m = re.search(p, c, re.S); return clean(m.group(1)) if m else ""
        title = g(r'itemprop="name" content="([^"]*)"'); b2b_url = g(r'itemprop="url" content="([^"]*)"')
        if not title or not b2b_url: continue
        ext = re.search(r'card-item__about">\s*(?:<noindex>)?\s*<a target="_blank" href="([^"]+)"', c, re.S)
        ext_url = html.unescape(ext.group(1)) if ext else ""
        tags = [clean(t) for t in re.findall(r'<span class="card-item__tag-item">(.*?)</span>\s*(?=<span class="card-item__tag-item">|</div>)', c, re.S)]
        law = next((t for t in tags if re.search(r"ФЗ", t)), "")                       # law — только 44-ФЗ/223-ФЗ, как у остальных источников
        zmo = "малый объём" if any("малого объема" in t for t in tags) else ""
        etp = next((t for t in tags if re.search(r"РТС|ЕАТ|B2B|ЭТП|Сбер|Росэлторг|Фабрикант|ГПБ|ТЭК", t)), "")
        method = tags[-1] if tags and tags[-1] not in (law, etp, "Закупка") and "малого объема" not in tags[-1] else ""
        reg = re.search(r"regNumber=(\d+)", ext_url); num = re.search(r"Закупка №(\d+)", c)
        host = re.sub(r"^https?://(www\.)?|/.*$", "", ext_url)
        pre = next((p for h, p in (("zakupki.gov.ru", ""), ("agregatoreat.ru", "eat-"), ("rts-tender.ru", "rts-"), ("otc.ru", "otc-"), ("b2b-center.ru", "b2b-")) if host.endswith(h)), None)
        if reg and pre == "": rid = reg.group(1)                       # номер ЕИС — дедуп с ГосПланом
        elif num and pre: rid = pre + num.group(1)
        else: rid = re.sub(r"^.*?/number/|/$", "", b2b_url)
        end = g(r'itemprop="endDate" content="(\d{4}-\d\d-\d\d)')
        rows.append({"id": rid, "title": title, "customer": g(r'card-item__organization-name">(.*?)</div>'),
                     "region": g(r'itemprop="location" content="([^"]*)"').replace("-", " "), "price": price(g(r'itemprop="price" content="([^"]*)"')),
                     "deadline": end, "url": ext_url or b2b_url, "urls": [u for u in (ext_url, b2b_url) if u], "law": law,
                     "note": "; ".join(x for x in (etp, zmo, method) if x)})
    return rows


def parse_mitwork(page, today):
    """Объявления Евразийского электронного портала (eep.mitwork.kz, закупки квазигосударственного сектора РК): таблица grid-view —
    номер, наименование, сумма в тенге без НДС, способ, начало и окончание приёма заявок, организатор, статус. Берём объявления,
    у которых окончание приёма сегодня или позже (проверено 01.10.2026: поиск «обучение» — 1 000 объявлений, «тренинг» — 245)."""
    rows = []
    for r in re.findall(r'<tr class="item" data-key="(\d+)">(.*?)</tr>', page, re.S):
        num, body = r
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
        if len(cells) < 8: continue
        a = re.search(r'href="(https://eep\.mitwork\.kz/ru/publics/buy/\d+)"', body)
        org = re.search(r'publics/subject/\d+"[^>]*title="([^"]+)"', body)
        cust = clean(org.group(1)) if org else cells[6]
        end = re.match(r"(\d{4}-\d\d-\d\d)", cells[5])
        deadline = end.group(1) if end else ""
        if deadline and deadline < today: continue
        rows.append({"id": "mitwork-" + num, "title": cells[1], "customer": cust, "region": "Казахстан",
                     "price": price(cells[2]), "deadline": deadline, "url": a.group(1) if a else f"{HOST['mitwork']}/ru/publics/buy/{num}",
                     "law": "", "country": "KZ", "currency": "KZT", "note": f"MITWORK ЕЭП, объявление {num}; {cells[3]}; {cells[7]}"})
    return rows


def parse(site, page):
    if site == "goszakup": return parse_goszakup(page)
    if site == "mitwork": return parse_mitwork(page, time.strftime("%Y-%m-%d"))
    if site == "b2b": return parse_b2b(page)
    rows = []
    for part in page.split('class="tender-row row"')[1:]:
        rid = re.match(r'\s*id="(\d+)"', part)
        if site == "rostender":
            a = re.search(r'class="description tender-info__description tender-info__link"\s*href="([^"]+)"[^>]*>(.*?)</a>', part, re.S)
            if not a or not rid: continue
            tid, href, title = rid.group(1), a.group(1), a.group(2)
            url = f"{HOST[site]}/tender/{tid}"
            reg = re.search(r'data-id="address\d+">(.*?)</div>', part, re.S)
        else:
            a = re.search(r'class="information__link"\s*href="(/tender/(\d+))"[^>]*>(.*?)</a>', part, re.S)
            if not a: continue
            href, tid, title = a.group(1), a.group(2), a.group(3)
            url = HOST[site] + href
            reg = re.search(r'data-id="places-\d+">(.*?)</span>', part, re.S)
        d = re.search(r"Окончание[^<]*<span[^>]*>\s*(\d\d)\.(\d\d)\.(\d{4})", part)
        p = re.search(r'starting-price--price">([^<]*)<', part) or re.search(r'class="[^"]*price[^"]*">\s*([\d\s\xa0.,]+(?:&#8381;|₽))', part)
        cust = re.search(r'(?:Заказчик|customer__name)[^>]*>\s*<[^>]*>([^<]{3,})<', part)
        rows.append({"id": tid, "title": clean(title), "customer": clean(cust.group(1)) if cust else "",
                     "region": clean(reg.group(1)) if reg else "", "price": price(p.group(1)) if p else None,
                     "deadline": f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else "", "url": url, "law": ""})
    return rows


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--site", required=True, choices=list(HOST)); a.add_argument("--urls", nargs="*", default=[])
    a.add_argument("--pause", type=float, default=3); a.add_argument("--out", default="rows.json")
    a.add_argument("--words", nargs="*", default=[], help="goszakup, mitwork: слова поиска (каждое — отдельный запрос по наименованию лота)")
    a.add_argument("--relay", default="", help="адрес релея (российский сервер), например https://host/; без него — напрямую")
    a.add_argument("--relay-token", default="")
    x = a.parse_args()
    RELAY.update(url=x.relay, token=x.relay_token)
    out, pages, seen = [], [], set()
    if x.site == "goszakup":
        import urllib.parse
        x.urls = [f"{HOST['goszakup']}/ru/search/lots?" + urllib.parse.urlencode({"filter[name]": w, "count_record": 100}) for w in x.words] or x.urls
    if x.site == "mitwork":
        import urllib.parse
        x.urls = [f"{HOST['mitwork']}/ru/publics/buys?" + urllib.parse.urlencode({"filter[search]": w}) for w in x.words] or x.urls
    for i, u in enumerate(x.urls):
        code, body = fetch(u)
        if code != "200":
            pages.append({"url": u, "status": "failed", "rows": 0, "note": f"HTTP {code}"})
        elif re.search(r"Подождите|выполняется проверка|captcha|challenge", body[:3000], re.I):
            pages.append({"url": u, "status": "blocked", "rows": 0, "note": "проверка на бота — не обходим"})
        else:
            rows = parse(x.site, body)
            new = [r for r in rows if r["id"] not in seen]
            for r in new: seen.add(r["id"])
            out += new
            table = bool(rows) or (x.site == "mitwork" and 'class="grid-view"' in body)   # у mitwork все строки могут быть с закрытым приёмом
            pages.append({"url": u, "status": "ok" if table else "empty", "rows": len(rows),
                          "note": "" if rows else ("открытых объявлений нет" if table else "строк не найдено: изменилась вёрстка?")})
        if i < len(x.urls) - 1: time.sleep(x.pause)
    json.dump({"rows": out, "pages": pages}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"rows": len(out), "pages": [(p["status"], p["rows"]) for p in pages]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
