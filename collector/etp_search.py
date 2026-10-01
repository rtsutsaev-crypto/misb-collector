#!/usr/bin/env python3
"""Search the public JSON lists of four electronic trading platforms by the words of the MISB dictionary.

The sites' own pages load these lists without login (checked 29.09.2026, robots.txt of the hosts has no rules for them); the script asks the
same addresses like a visitor: one request per word and page, a pause between requests, TLS verification on, no login, nothing bypassed.

  python3 etp_search.py --terms dictionary.json --out leads.json [--sources mts,rest,rftorgi,fedtorgi]
                        [--pause 1.2] [--topics] [--limit-words 0 --offset 0] [--date 2026-09-29] [--known ids.txt] [--since 2025-09-29]

Sources (key of the plan -> address):
  mts      МТС Закупки          GET  tenders.mts.ru/api/v2/tender?searchQuery=<слово>&pageSize=50&page=N
  rest     ЭТП РЭСТ             GET  etp.r-est.ru/searchServlet?query={"types":[...],"title":<слово>}&limit={"min":N,"max":N+50}
  rftorgi  Торги РФ             POST lk.rftorgi.ru/api/v1/procedures/search?page=N&limit=10   тело {"name":<слово>,...}
  fedtorgi Торги Федерации      POST lk.fedtorgi.ru/api/v1/procedures/search?page=N&limit=10  (та же форма)
  rzdm     РЖД-Медицина         GET  zakupki.rzd-medicine.ru/api/purchase/orders/compressed/main?limit=15&page=N&search=<слово>
  lsr      ЭТП Группы ЛСР       POST zakupki.lsr.ru/ajax (форма action=get-tenders&subject=<слово>&offset=N&limit=10)
  mosreg   Электронный магазин МО POST api.market.mosreg.ru/api/Trade/GetTradesForParticipantOrAnonymous, полный обход открытых закупок (90 страниц по 10);
                                только российский адрес (из облака обрыв соединения)
  setonline СЭТ                 GET  etp.setonline.ru/searchServlet (та же платформа, что РЭСТ; из облака не открывается: цепочка Минцифры, нужен --cafile;
                                проверено только по форме запроса из разведки 29.09.2026)

--terms takes the dictionary document of the site (config/dictionary: formats, topics, exclude; groups written as comma-separated strings) or the
flat dictionary_terms.json of ru_pilot.
A record becomes a lead when its title contains a dictionary term (formats or topics; prefix match of every word of the term). Training is read as widely as the
dictionary reads it (decision of 29.09.2026): no extra requirement of a training word in the title; the site's fit and the profile decision sort the noise. Closed procedures are kept: the site
shows them as "завершён (сигнал потребности)". Output follows the leadsets of the site (see bidzaar_to_leads.py).
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import ssl

CTX = None  # set by --cafile: an extra root certificate (PEM) added to the trust store; verification stays on
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 misb-collector"
STAT = {}


def http(url, body=None, tries=4):
    """One request with retries on network errors (a reset or TLS EOF is common when a site throttles); HTTP 4xx is returned as an error."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        hdr["Content-Type"] = "application/json"
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdr), timeout=45, context=CTX) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(4 * (i + 1)); continue
            raise RuntimeError(f"HTTP {e.code}") from e
        except Exception as e:  # noqa: BLE001
            err = e; time.sleep(3 * (i + 1))
    raise RuntimeError(f"network: {str(err)[:80]}")


def stems(term):
    """(stem, longest allowed word) per token: the title word may extend the stem by at most 4 letters ("интенсив" must not match "интенсивности")."""
    return [(w[: max(4, len(w) - 2)], max(4, len(w) - 2) + 4) for w in re.findall(r"[\wё-]+", term.lower())]


class Dict:
    def __init__(self, terms):
        self.terms = [(t, stems(t)) for t in terms]

    def hit(self, title):
        words = re.sub(r"[^\wё-]+", " ", title.lower()).split()
        for t, st in self.terms:
            if st and all(any(w.startswith(s) and len(w) <= mx for w in words) for s, mx in st):
                return t
        return None


def num(s):
    m = re.search(r"[\d\s]+[\d](?:[.,]\d+)?", str(s or "").replace("\xa0", " "))
    if not m:
        return None
    try:
        v = float(m.group(0).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return v if v > 0 else None


def dmy(s):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def lead(**k):
    base = {"collectedAt": None, "country": "RU", "currency": "RUB", "customer": "", "deadline": None, "flags": [], "id": "", "law": "", "note": "",
            "platform": "", "price": None, "region": "", "source": "", "title": "", "url": ""}
    base.update(k)
    return base


# ---------------------------------------------------------------- МТС
def mts(word, page):
    j = http("https://tenders.mts.ru/api/v2/tender?" + urllib.parse.urlencode(
        {"searchQuery": word, "pageSize": 50, "page": page, "attributesForSort": "tenders_publication_date,desc"}))
    rows = []
    for x in j.get("data", []):
        if x.get("status") == "Отменена":
            continue
        rows.append(lead(id="MTS-" + str(x["id"]), title=(x.get("name") or "").strip(), customer=x.get("organizer") or "МТС",
                         region=re.sub(r"\s*\(\+\d+\)", "", x.get("regions") or ""), deadline=x.get("endDateAcceptingOffers"),
                         law="Коммерческий", platform="МТС Закупки", source="МТС Закупки", url="https://tenders.mts.ru/tenders/" + str(x["id"]),
                         note=f"МТС Закупки, торги № {x.get('number')}, статус: {x.get('status')}; категории: {x.get('categories')}",
                         verify="Ссылка собрана по шаблону /tenders/<id> сайта; адрес карточки проверен только на ответ 200."))
    return rows, j.get("totalPages", 1)


# ---------------------------------------------------------------- РЭСТ
def _servlet(host, plat, prefix):
    """The tender servlet of the platform behind ЭТП РЭСТ and СЭТ (etp.setonline.ru): GET /searchServlet?query={"types":[...],"title":<word>}&..."""
    def run(word, page):
        q = {"types": ["BUYING", "RFI", "SMALL_PURCHASE"], "title": word}
        lim = {"min": page * 50, "max": page * 50 + 50, "updateTotalCount": True}
        j = http(f"https://{host}/searchServlet?" + urllib.parse.urlencode(
            {"query": json.dumps(q, ensure_ascii=False), "filter": json.dumps({"state": ["ALL"]}), "sort": json.dumps({"placementDate": False}),
             "limit": json.dumps(lim)}))
        rows = []
        for x in j.get("list", []):
            ident = str(x.get("identifier") or "")
            cust = x.get("customer")
            cust = (cust[0] if cust else {}) if isinstance(cust, list) else (cust if isinstance(cust, dict) else {})  # a list on РЭСТ, may be an object elsewhere
            org = x.get("organizer") if isinstance(x.get("organizer"), dict) else {}
            eis = bool(re.fullmatch(r"3\d{10}", ident))
            rows.append(lead(id=ident if eis else f"{prefix}-" + str(x.get("uuid") or x.get("id") or ident), title=(x.get("title") or "").strip(),
                             customer=cust.get("title") or org.get("title") or "", customerInn=cust.get("inn") or org.get("inn"),
                             deadline=dmy(x.get("gdEndDate")), price=num(re.sub("<[^>]+>", " ", x.get("price") or "")),
                             law="223-ФЗ" if eis else "Коммерческий", platform=plat, source=plat, url=x.get("lotLink") or f"https://{host}/",
                             note=", ".join(v for v in (f"{plat} {ident}", x.get("placementType") or x.get("type"), (x.get("state") or {}).get("title")) if v)))
            pub = dmy(x.get("placementDate"))
            if not pub and eis:  # 223-ФЗ number 3YYNNNNNNNN: YY is the year of the notice
                pub = f"20{ident[1:3]}-01-01"
            rows[-1]["_pub"] = pub
            if pub:
                rows[-1]["note"] += f", размещено {pub[8:10]}.{pub[5:7]}.{pub[:4]}" if len(pub) == 10 and not pub.endswith("-01-01") else f", год размещения {pub[:4]}"
        return rows, -(-int(j.get("totalCount") or 0) // 50)
    return run


rest = _servlet("etp.r-est.ru", "ЭТП РЭСТ", "REST")
setonline = _servlet("etp.setonline.ru", "СЭТ", "SET")


# ---------------------------------------------------------------- Торги РФ / Торги Федерации
def _fed(host, plat, key, prefix):
    def run(word, page):
        body = {"name": word, "section_guid": "", "customer": {"query_name": ""}, "sections": ["SECTION_223_FZ", "SECTION_COMMERCIAL_PROCEDURES"],
                "method_guid": [], "sort": "SORT_START_BID_DATE", "statuses": None}
        j = http(f"https://{host}/api/v1/procedures/search?page={page + 1}&limit=10", body)
        rows = []
        for x in j.get("data", []):
            reg = x.get("registry_number")
            rows.append(lead(id=str(reg) if reg else f"{prefix}-{x['number']}", title=(x.get("title") or "").strip(),
                             customer=x.get("customer_short_title") or x.get("organizer_short_title") or "", customerInn=x.get("organizer_inn"),
                             deadline=(x.get("close_bid_date") or "")[:10] or None, price=num(x.get("price_localized")),
                             law="223-ФЗ" if "223" in (x.get("platform_type") or "") else "Коммерческий", platform=plat, source=plat,
                             url="https://etpp.ru/tenders",
                             note=f"{plat} № {x['number']}, {x.get('type_localized')}, {x.get('status_localized')}",
                             verify="Прямой адрес карточки не найден (страницы сайта одностраничные); ссылка ведёт на общий реестр процедур, найти по номеру."))
        return rows, -(-int(j.get("total_count") or 0) // 10)
    return run



# ---------------------------------------------------------------- РЖД-Медицина, электронный магазин
def rzdm(word, page):
    j = http("https://zakupki.rzd-medicine.ru/api/purchase/orders/compressed/main?" + urllib.parse.urlencode({"limit": 15, "page": page + 1, "search": word}))
    rows = []
    for x in j.get("data", []):
        if (x.get("status") or {}).get("name") == "Отмена":
            continue
        co = x.get("company") or {}
        name = co.get("name") or co.get("title") or (co.get("legal_detail") or {}).get("name") or "" if isinstance(co, dict) else ""
        rows.append(lead(id="RZDM-" + str(x["id"]), title=(x.get("name") or "").strip(), customer=name or "РЖД-Медицина, заказчик не указан в списке",
                         region=(x.get("regions") or {}).get("name") or "", deadline=(x.get("application_deadline") or "")[:10] or None,
                         price=num(x.get("average_price")), law="Коммерческий", platform="РЖД-Медицина", source="РЖД-Медицина",
                         url="https://zakupki.rzd-medicine.ru/", note=f"РЖД-Медицина, электронный магазин, № {x['id']}, статус: {(x.get('status') or {}).get('name')}",
                         verify="Прямой адрес карточки не найден (одностраничный сайт); ссылка ведёт на главную, найти закупку по номеру."))
    return rows, (j.get("meta") or {}).get("last_page", 1)


# ---------------------------------------------------------------- ЛСР, закупки группы
def lsr(word, page):
    form = {"action": "get-tenders", "offset": page * 10, "limit": 10, "sortColumn": "startDate", "sortAsc": "false", "subject": word, "status": "", "region": "",
            "method": "", "format": "", "startDate": "", "endDate": ""}
    req = urllib.request.Request("https://zakupki.lsr.ru/ajax", data=urllib.parse.urlencode(form).encode(), headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})
    j = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                j = json.loads(r.read().decode("utf-8", "replace")); break
        except Exception as e:  # noqa: BLE001
            if i == 2:
                raise RuntimeError(f"network: {str(e)[:80]}") from e
            time.sleep(3 * (i + 1))
    strip = lambda s: re.sub(r"<[^>]+>", " ", s or "").strip()
    rows = []
    for x in j.get("Rows", []):
        m = re.search(r'href="([^"]+)"', x.get("subject") or "")
        rows.append(lead(id="LSR-" + strip(x.get("number")).replace("/", "-"), title=re.sub(r"\s+", " ", strip(x.get("subject"))), customer=x.get("customer") or "ЛСР",
                         deadline=dmy(strip(x.get("endDate"))), law="Коммерческий", platform="ЭТП Группы ЛСР", source="ЭТП Группы ЛСР",
                         url="https://zakupki.lsr.ru/" + (m.group(1) if m else "tenders"), note=f"ЭТП Группы ЛСР № {strip(x.get('number'))}, {x.get('method')}, {x.get('status')}"))
    return rows, -(-int((j.get("Paging") or {}).get("Total") or 0) // 10)


# ---------------------------------------------------------------- Электронный магазин Московской области (только российский адрес)
MOSREG_BODY = {"page": 1, "itemsPerPage": 10, "tradeState": "15", "OnlyTradesWithMyApplications": False, "sortingParams": [], "filterPriceMin": "", "filterPriceMax": "",
               "filterDateFrom": None, "filterDateTo": None, "filterFillingApplicationEndDateFrom": None, "FilterFillingApplicationEndDateTo": None,
               "filterTradeEasuzNumber": "", "showOnlyOwnTrades": False, "showApprovementTrades": False, "IsImmediate": False, "UsedClassificatorType": 20,
               "classificatorCodes": [], "CustomerFullNameOrInn": "", "CustomerAddress": "", "Koz2Value": "", "ParticipantHasApplicationsOnTrade": "",
               "ProductPriceMin": "", "ProductPriceMax": ""}


def mosreg(word, page):
    """Full scan of the open trades (the site has no text filter, only categories): the word is ignored, every page is read once, the dictionary is applied here.
    Body from the recon of 29.09.2026 (Russian server): POST api.market.mosreg.ru/api/Trade/GetTradesForParticipantOrAnonymous, 900 open trades = 90 pages of 10."""
    j = http("https://api.market.mosreg.ru/api/Trade/GetTradesForParticipantOrAnonymous", dict(MOSREG_BODY, page=page + 1))
    rows = []
    for x in j.get("invdata", []):
        pub = (x.get("PublicationDate") or "")[:10]
        rows.append(lead(id="MOSREG-" + str(x["Id"]), title=(x.get("TradeName") or "").strip(), customer=(x.get("CustomerFullName") or "").strip(),
                         region="Московская область", deadline=(x.get("FillingApplicationEndDate") or "")[:10] or None,
                         price=num(x.get("InitialPrice")) if x.get("IsInitialPriceDefined", True) else None, law="Коммерческий",
                         platform="Электронный магазин Московской области", source="Электронный магазин МО", url="https://market.mosreg.ru/",
                         note=f"Электронный магазин МО, № {x['Id']}, {x.get('TradeStateName')}, опубликовано {pub}, заявок {x.get('ApplicationsCount')}",
                         verify="Прямой адрес карточки не найден (одностраничный сайт); ссылка ведёт на главную, найти закупку по номеру."))
    return rows, int(j.get("totalpages") or 1)


SOURCES = {"mts": (mts, 3), "rest": (rest, 3), "rftorgi": (_fed("lk.rftorgi.ru", "Торги РФ", "rftorgi", "RFT"), 5),
           "fedtorgi": (_fed("lk.fedtorgi.ru", "Торги Федерации", "fedtorgi", "FT"), 5), "rzdm": (rzdm, 4), "lsr": (lsr, 3), "setonline": (setonline, 3), "mosreg": (mosreg, 100)}
FULLSCAN = {"mosreg"}  # sources without a text filter: one pass over all pages, the word list is not used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", required=True)
    ap.add_argument("--out", default="leads.json")
    ap.add_argument("--sources", default="mts,rest,rftorgi,fedtorgi,rzdm,lsr")
    ap.add_argument("--cafile", default="", help="extra root certificate (PEM), e.g. Russian Trusted Root CA for setonline; TLS verification stays on")
    ap.add_argument("--pause", type=float, default=1.2)
    ap.add_argument("--topics", action="store_true", help="add the topic words of the dictionary to the format words")
    ap.add_argument("--limit-words", type=int, default=0, help="take only this many words (a rotation batch)")
    ap.add_argument("--offset", type=int, default=0, help="start of the batch in the word list; wraps around (cursor of the rotation)")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=365)).isoformat(), help="drop procedures whose deadline is older (default: a year)")
    ap.add_argument("--known", default="", help="file with lead ids (one per line) that are already in the base")
    a = ap.parse_args()
    if a.cafile:
        global CTX
        CTX = ssl.create_default_context()
        CTX.load_verify_locations(cafile=a.cafile)
    d = json.load(open(a.terms, encoding="utf-8"))
    d = d.get("data", d)

    def flat(key):  # the dictionary document keeps groups as one comma-separated string; a plain list of words works too
        return [w.strip() for g in d.get(key, []) for w in (g.split(",") if isinstance(g, str) else [g]) if w.strip()]
    formats, topics = flat("formats"), flat("topics")
    words = formats + (topics if a.topics else [])
    exclude = [w.lower() for w in flat("exclude") if len(w) >= 5]
    words = [w for w in dict.fromkeys(words) if len(w) >= 4 and not re.search(r"[\d/]", w)]
    total_words = len(words)
    if a.limit_words and total_words:
        words = [words[(a.offset + i) % total_words] for i in range(min(a.limit_words, total_words))]
    dic = Dict(formats + topics)
    known = set(open(a.known, encoding="utf-8").read().split()) if a.known else set()
    out, seen = [], set()
    for key in [s for s in a.sources.split(",") if s]:
        fn, maxpages = SOURCES[key]
        src_words = [""] if key in FULLSCAN else words
        st = STAT[key] = {"words": 0, "requests": 0, "records": 0, "matched": 0, "new": 0, "errors": [], "stopped": ""}
        fails = 0
        for w in src_words:
            st["words"] += 1
            for page in range(maxpages):
                time.sleep(a.pause)
                try:
                    rows, pages = fn(w, page)
                    fails = 0
                except RuntimeError as e:
                    fails += 1; st["errors"].append(f"{w}: {e}")
                    break
                finally:
                    st["requests"] += 1
                st["records"] += len(rows)
                for r in rows:
                    t = dic.hit(r["title"])
                    if not t or any(x in r["title"].lower() for x in exclude):
                        continue
                    ref = r["deadline"] or r.get("_pub")  # no deadline (e.g. a single-supplier purchase): the placement date decides
                    if ref and ref < a.since:
                        continue
                    st["matched"] += 1
                    if r["id"] in seen or r["id"] in known:
                        continue
                    seen.add(r["id"]); r["collectedAt"] = a.date; r.pop("_pub", None)
                    r["note"] += (f"; найден по слову «{w}», в названии «{t}»" if w else f"; полный обход списка, в названии «{t}»")
                    out.append(r); st["new"] += 1
                if page + 1 >= pages:
                    break
            if fails >= 5:
                st["stopped"] = "5 ошибок подряд, источник остановлен"; break
        print(key, {k: v for k, v in st.items() if k != "errors"}, "errors:", len(st["errors"]), st["errors"][:2], file=sys.stderr)
    json.dump({"collectedAt": a.date, "leads": out, "stats": STAT, "source": "etp_search.py", "totalWords": total_words,
               "nextOffset": (a.offset + len(words)) % total_words if total_words else 0}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(len(out), "leads ->", a.out, file=sys.stderr)


if __name__ == "__main__":
    main()
