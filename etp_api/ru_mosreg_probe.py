#!/usr/bin/env python3
"""Probe of the public list of the Moscow-region market (market.mosreg.ru) from a Russian server: what categories exist and how many open trades each has.

The site's own page posts to https://api.market.mosreg.ru/api/Trade/GetTradesForParticipantOrAnonymous (no login). The body (recon of 29.09.2026) has no text filter:
only tradeState, TradeCategory, classificatorCodes and customer/price fields. This script asks the same address like a visitor (one request, a pause of 1.5 s between them,
TLS on) for every TradeCategory id in a range and prints: id, total open trades, keys of the first record, the first two titles - to find the id of "Образовательные услуги".
   python3 ru_mosreg_probe.py [--from 1100001 --to 1100040] [--cafile russian_root.pem]
"""
import argparse, json, ssl, sys, time, urllib.request

URL = "https://api.market.mosreg.ru/api/Trade/GetTradesForParticipantOrAnonymous"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 misb-collector"
BASE = {"page": 1, "itemsPerPage": 10, "tradeState": "15", "OnlyTradesWithMyApplications": False, "sortingParams": [], "filterPriceMin": "", "filterPriceMax": "",
        "filterDateFrom": None, "filterDateTo": None, "filterFillingApplicationEndDateFrom": None, "FilterFillingApplicationEndDateTo": None, "filterTradeEasuzNumber": "",
        "showOnlyOwnTrades": False, "showApprovementTrades": False, "IsImmediate": False, "UsedClassificatorType": 20, "classificatorCodes": [], "CustomerFullNameOrInn": "",
        "CustomerAddress": "", "Koz2Value": "", "ParticipantHasApplicationsOnTrade": "", "ProductPriceMin": "", "ProductPriceMax": ""}


def post(body, ctx):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", type=int, default=1100001)
    ap.add_argument("--to", dest="hi", type=int, default=1100040)
    ap.add_argument("--cafile", default="")
    a = ap.parse_args()
    ctx = ssl.create_default_context()
    if a.cafile:
        ctx.load_verify_locations(cafile=a.cafile)
    j = post(BASE, ctx)
    print("ВСЕГО ОТКРЫТЫХ", {k: v for k, v in j.items() if not isinstance(v, (list, dict))})
    print("КЛЮЧИ ОТВЕТА", list(j.keys()))
    items = next((v for v in j.values() if isinstance(v, list)), [])
    if items:
        print("КЛЮЧИ ЗАПИСИ", list(items[0].keys()) if isinstance(items[0], dict) else items[0])
        print("ПЕРВАЯ ЗАПИСЬ", json.dumps(items[0], ensure_ascii=False)[:700])
    for cid in range(a.lo, a.hi + 1):
        time.sleep(1.5)
        try:
            j = post(dict(BASE, TradeCategory=cid), ctx)
        except Exception as e:  # noqa: BLE001
            print(cid, "ОШИБКА", str(e)[:80]); continue
        tot = j.get("totalrecords", j.get("totalRecords", "?"))
        its = next((v for v in j.values() if isinstance(v, list)), [])
        names = [str((x.get("TradeName") or x.get("tradeName") or x.get("Name") or ""))[:70] for x in its[:2] if isinstance(x, dict)]
        print(cid, "всего", tot, "|", " || ".join(names))


if __name__ == "__main__":
    main()
