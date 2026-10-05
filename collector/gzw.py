"""gzw.py — региональные электронные магазины малых закупок на движке «WEB-Торги-КС» (Кейсистемс): Самарская, Смоленская,
Мурманская, Свердловская области и другие внедрения. Реестр извещений открыт без входа: страница NoticesGrid отдаёт форму
с токеном, строки приходят POST JSON на NoticesJson (тот же запрос, что делает браузер). Капча и вход не нужны и не обходятся.

Запуск: python3 gzw.py --site <ключ>=<база>|<путь> [--site …] --dict dictionary.json --known known.json --date ГГГГ-ММ-ДД
        --out gzw_out.json [--days 4] [--max-pages 60]
  пример: --site zmo-samara=https://webtorgi.samregion.ru|/smallpurchases/GzwSP --site zmo-murman=https://gz-murman.ru|/site/GzwSP
Строки за последние --days дней публикации проходят отбор collector.py (Matcher, Known). Выход {leads, updates, stats:{<ключ>: …}}:
лиды с source = ключ сайта, id = реестровый номер (ИМЗ-…), url — реестр сайта (карточка открывается только после входа).
"""
import argparse, datetime as dt, http.cookiejar, json, os, re, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
REGION = {"samregion": "Самарская область", "admin-smolensk": "Смоленская область", "gz-murman": "Мурманская область", "egov66": "Свердловская область"}


def fetch_site(base, path, since, max_pages, st):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "ru,en;q=0.8")]
    h = op.open(base + path + "/NoticesGrid", timeout=60).read().decode("utf-8", "ignore"); st["requests"] += 1
    if re.search(r"(?i)captcha|проверка браузера|ddos-guard", h[:20000]) and "NoticesJson" not in h:
        raise RuntimeError("проверка на бота — не обходится")
    m = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', h)
    hdr = {"Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest", "Referer": base + path + "/NoticesGrid",
           "Accept": "application/json, text/javascript, */*; q=0.01"}
    if m: hdr["RequestVerificationToken"] = m.group(1)
    out = []
    for page in range(max_pages):
        settings = {"rp": 30, "page": page, "totalRows": 0, "rpList": [10, 20, 30], "sortDir": "desc",
                    "sortList": {"pub_date": "Дата публикации"}, "sortField": "pub_date",
                    "filter": {"dtDatePubBegin": {"PName": "dtDatePubBegin", "PValue": since.strftime("%d.%m.%Y"), "PType": "Date"}}, "localFilter": []}
        req = urllib.request.Request(base + path + "/NoticesJson", data=json.dumps({"settings": settings}).encode(), method="POST", headers=hdr)
        j = None
        for t in range(3):
            try:
                j = json.loads(op.open(req, timeout=60).read().decode("utf-8", "ignore")); st["requests"] += 1; break
            except urllib.error.HTTPError as e:
                st["requests"] += 1
                if e.code in (401, 403): raise RuntimeError(f"HTTP {e.code}")
                time.sleep(2 + 2 * t)
            except Exception:
                time.sleep(2 + 2 * t)
        items = (j or {}).get("items") or []
        out += items
        if len(items) < 30: break
        time.sleep(0.4)
    return out


def ddmmyyyy(s):
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--site", action="append", required=True); a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--known"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--days", type=int, default=4); a.add_argument("--max-pages", type=int, default=60)
    x = a.parse_args()
    today = dt.date.fromisoformat(x.date)
    since = today - dt.timedelta(days=x.days)
    m = C.Matcher(json.load(open(x.dict, encoding="utf-8")))
    K = C.Known()
    if x.known and os.path.exists(x.known):
        kj = json.load(open(x.known, encoding="utf-8")); K.ids = kj.get("ids", {}); K.keys = set(kj.get("keys", []))
    leads, updates, stats = [], [], {}
    for spec in x.site:
        key, _, rest = spec.partition("="); base, _, path = rest.partition("|")
        st = {"requests": 0}
        try:
            items = fetch_site(base.rstrip("/"), path.rstrip("/"), since, x.max_pages, st)
        except Exception as e:
            stats[key] = dict(st, status="failed", note=str(e)[:200]); continue
        region = next((v for k, v in REGION.items() if k in base), "")
        rows = []
        for it in items:
            if it.get("status") in ("Публикация отменена", "Не состоялась", "Контракт заключен", "Итоги подведены"): continue
            rows.append({"id": it.get("number") or "", "title": it.get("name") or "", "customer": it.get("uchr_sname") or "",
                         "region": region, "price": float(it["summa"]) if str(it.get("summa") or "").replace(".", "", 1).isdigit() else None,
                         "deadline": ddmmyyyy(it.get("collecting_enddate")), "law": "44-ФЗ" if str(it.get("reestr_type")) == "44" else "223-ФЗ" if str(it.get("reestr_type")) == "223" else "",
                         "url": f"{base.rstrip('/')}{path.rstrip('/')}/NoticesGrid", "okpd2": [c.strip() for c in str(it.get("okpd2_codes") or "").split(",") if c.strip()],
                         "flags": ["zmo"], "note": "Малая закупка в электронном магазине региона; найти по номеру в реестре " + (it.get("number") or ""),
                         "publishedAt": ddmmyyyy(it.get("pub_date"))})
        L, U, s = C.process_rows(rows, m, K, key, x.date)
        for l in L: K.add(l)
        leads += L; updates += U
        stats[key] = dict(st, status="ok", rows=len(items), matched=s["matched"], new=len(L), updates=len(U))
    json.dump({"leads": leads, "updates": updates, "stats": stats}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
