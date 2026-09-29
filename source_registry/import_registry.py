#!/usr/bin/env python3
"""Registry of 100 procurement sources (package MISB_Claude_Code_sources, 29.09.2026) -> the site database.

The registry is research: candidates and entry points, not working integrations. It is kept apart from the
site's own runtime state (collection `sources`, plan `config/sources-plan`): a registry record only *points* to
the site source that already serves it, and the site shows both states side by side.

Commands (PKG = unpacked package dir, W = work dir with the database dump):
  dry-run PKG W    match the 100 records to the site and print/write the report (nothing is written to the database)
  build   PKG W    write W/out/srcreg/<doc>.json (one document per record), _map.json and _meta.json;
                   idempotent: the document id is derived from dataset_id + source_id, a second run only
                   refreshes the research fields
  probe   PKG W    robots-aware reachability check of the record URL, its RSS and API reference URL from this
                   machine (no logins, no captcha bypass); writes W/out/registry-probe.json

Needs in W: db/sources/*.json, db/config/sources-plan.json, db/orgdir/catalog.json (ArtifactData dumps).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "sources_probe"))

# Relations set by hand: the record is served by an existing site source (its factual status is shown as is).
# relation: direct — the same site; via_api — the data reaches the site through an API or an aggregator
# that is already connected; partial — only a part of the source (a filter, a section).
MANUAL = {
    1: ("via_api", ["gosplan"], "Данные ЕИС (44-ФЗ, 223-ФЗ) идут через ГосПлан API; прямой RSS/поиск ЕИС не подключён и из облака не открывается."),
    2: ("direct", ["b2b"], "Открытые подборки B2B-Center."),
    3: ("direct", ["tenderpro"], "Проба доступности площадки."),
    4: ("direct", ["tektorg"], "Проба доступности площадки."),
    5: ("direct", ["etpgpb"], "Проба доступности; лоты частично идут через TenderGuru (фильтр площадки e46)."),
    6: ("direct", ["roseltorg"], "Проба доступности; лоты частично идут через TenderGuru (e2, e31)."),
    7: ("partial", ["tenderguru-etp"], "Лоты идут через TenderGuru с фильтром площадки (e1, e26, e47); сама площадка не подключена."),
    8: ("partial", ["tenderguru-etp"], "Лоты идут через TenderGuru с фильтром площадки (e76)."),
    9: ("direct", ["fabrikant"], "Проба доступности; лоты частично идут через TenderGuru (e12)."),
    10: ("partial", ["tenderguru-etp"], "Лоты идут через TenderGuru с фильтром площадки (e3)."),
    12: ("partial", ["tenderguru-etp"], "Лоты идут через TenderGuru с фильтром площадки (e37)."),
    13: ("direct", ["otc"], "Проба доступности; лоты частично идут через TenderGuru (e36)."),
    14: ("direct", ["bidzaar"], "Открытый список запросов; нужен отдельный разбор."),
    18: ("direct", ["mos"], "Портал поставщиков Москвы: проба доступности."),
    21: ("direct", ["kz-goszakup"], "Казахстан: портал госзакупок, discover по словам обучения."),
    22: ("direct", ["kz-samruk"], "Казахстан: закупки Самрук-Казына."),
    23: ("direct", ["kz-mitwork"], "Казахстан: MITWORK."),
    25: ("direct", ["kz-mpkz"], "Казахстан: MP.kz, лента тендеров коммерческих компаний (первая страница)."),
    26: ("direct", ["by-icetrade"], "Беларусь: icetrade.by."),
    28: ("direct", ["by-goszakupki"], "Беларусь: goszakupki.by."),
    30: ("direct", ["tenderguru", "tenderguru-more", "tenderguru-etp", "tenderguru-quotes"], "Платный API TenderGuru: работает, тариф отдаёт 10 карточек на запрос."),
    31: ("direct", ["gosplan", "gosplan-topics", "gosplan-quotes", "gosplan-plan44", "gosplan-plan223"], "ГосПлан API: работает."),
    32: ("direct", ["rt-treningi", "rt-seminary", "rt-pk", "rt-obr", "rt-obuch", "rt-event"], "РосТендер: разделы по темам, страницы читаются."),
    33: ("direct", ["kontur"], "Платный API, подписки нет."),
    35: ("direct", ["tenderplan"], "Платный API, подписки нет."),
    36: ("direct", ["bico"], "Бикотендер: теги."),
    42: ("direct", ["energybase", "energybase-cos"], "Energybase: раздел «Подготовка персонала», порциями."),
    43: ("direct", ["rosneft"], "Проба доступности."),
    44: ("direct", ["gazprom"], "Проба доступности."),
    45: ("direct", ["rosatom"], "Проба доступности."),
    46: ("direct", ["rosseti"], "Проба доступности."),
    98: ("direct", ["kz-qazaqgaz"], "Казахстан: объявления о закупках QazaqGaz (первая страница)."),
    50: ("direct", ["corp-lukoil"], "Таблица тендеров группы: только первая страница."),
    51: ("direct", ["corp-novatek"], "Таблица закупок."),
    52: ("direct", ["tatneft"], "Проба доступности."),
    53: ("direct", ["gpn"], "Проба доступности."),
    54: ("direct", ["corp-sibur"], "Таблица закупок: только первая страница."),
    57: ("direct", ["corp-severstal"], "Таблица закупок."),
    58: ("direct", ["corp-rusal-services"], "Раздел «Прочие услуги»."),
    66: ("direct", ["corp-akron"], "Список приёма предложений группы (первая страница)."),
    69: ("direct", ["corp-sakhalin"], "Подряды и конкурсы."),
    77: ("direct", ["corp-magnit"], "Некоммерческие закупки."),
    80: ("direct", ["rzd"], "Проба доступности."),
}


def norm_url(u) -> str:
    u = str(u or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u).split("#")[0]
    return u.rstrip("/")


def host_of(u) -> str:
    h = urlparse(u if "://" in str(u) else "http://" + str(u)).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def load(p, default=None):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return d.get("data", d) if isinstance(d, dict) and "data" in d and "id" in d else d


def site_index(W: str):
    """URL -> site source keys, host -> site source keys, and the runtime docs by key."""
    plan = load(os.path.join(W, "db", "config", "sources-plan.json"), {}) or {}
    docs = {}
    for p in glob.glob(os.path.join(W, "db", "sources", "*.json")):
        d = load(p, {})
        docs[os.path.basename(p)[:-5]] = d
    by_url, by_host = {}, {}
    for s in plan.get("sources", []):
        urls = list(s.get("urls", []) or []) + [s.get("from"), s.get("base")]
        for u in urls:
            if u:
                by_url.setdefault(norm_url(u), set()).add(s["key"])
                by_host.setdefault(host_of(u), set()).add(s["key"])
    for k, d in docs.items():
        if d.get("url"):
            by_url.setdefault(norm_url(d["url"]), set()).add(k)
            by_host.setdefault(host_of(d["url"]), set()).add(k)
    cat = load(os.path.join(W, "db", "orgdir", "catalog.json"), {}) or {}
    global CATALOG
    CATALOG = {"url": {}, "host": {}}
    for row in cat.get("rows", []):
        k = row.get("source_key") or row.get("source_name")
        if row.get("source_url"):
            CATALOG["url"].setdefault(norm_url(row["source_url"]), set()).add(k)
        if row.get("normalized_host"):
            CATALOG["host"].setdefault(host_of(row["normalized_host"]), set()).add(k)
    for pth in sorted(glob.glob(os.path.join(W, "db", "orgdir", "orgs-[0-9]*.json"))):
        for o in (load(pth, {}) or {}).get("rows", []):
            if o.get("url"):
                CATALOG["url"].setdefault(norm_url(o["url"]), set()).add("orgdir:" + str(o.get("g") or o.get("n")))
                CATALOG["host"].setdefault(host_of(o["url"]), set()).add("orgdir:" + str(o.get("g") or o.get("n")))
    for p100 in (load(os.path.join(W, "db", "orgdir", "misc.json"), {}) or {}).get("priority_100", []):
        if p100.get("procurement_url"):
            CATALOG["url"].setdefault(norm_url(p100["procurement_url"]), set()).add("priority100:" + str(p100.get("group_name")))
            CATALOG["host"].setdefault(host_of(p100["procurement_url"]), set()).add("priority100:" + str(p100.get("group_name")))
    return plan, docs, by_url, by_host


CATALOG = {"url": {}, "host": {}}


def catalog_keys(r: dict) -> list:
    hit = CATALOG["url"].get(norm_url(r.get("url")), set()) or CATALOG["host"].get(host_of(r.get("url")), set())
    return sorted(str(x) for x in hit)


def doc_id(dataset: str, source_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", f"{dataset}--{source_id}")


def match(r: dict, by_url, by_host) -> dict:
    lid = r["legacy_row_id"]
    if lid in MANUAL:
        rel, keys, note = MANUAL[lid]
        return {"status": "matched", "how": "manual", "relation": rel, "siteKeys": keys, "note": note}
    exact = set()
    for u in [r.get("url"), (r.get("rss") or {}).get("url"), (r.get("api") or {}).get("reference_url")]:
        exact |= by_url.get(norm_url(u), set()) if u else set()
    if exact:
        return {"status": "matched", "how": "canonical-url", "relation": "direct", "siteKeys": sorted(exact), "note": ""}
    cand = by_host.get(host_of(r.get("url")), set())
    if cand:
        return {"status": "candidate", "how": "same-host", "relation": None, "siteKeys": sorted(cand),
                "note": "Совпал домен, но не адрес раздела: возможно другой раздел; не склеено."}
    return {"status": "new", "how": None, "relation": None, "siteKeys": [], "note": ""}


def dry_run(pkg: str, W: str) -> dict:
    S = json.load(open(os.path.join(pkg, "sources.json"), encoding="utf-8"))
    plan, docs, by_url, by_host = site_index(W)
    rows = S["sources"]
    res = {"input": len(rows), "matched": 0, "candidate": 0, "new": 0, "byRelation": {}, "records": []}
    for r in rows:
        m = match(r, by_url, by_host)
        res[m["status"]] += 1
        if m["relation"]:
            res["byRelation"][m["relation"]] = res["byRelation"].get(m["relation"], 0) + 1
        ck = catalog_keys(r)
        res["inCatalog"] = res.get("inCatalog", 0) + bool(ck)
        res["records"].append({"legacy_row_id": r["legacy_row_id"], "name": r["name"], "catalog": ck, **{k: m[k] for k in ("status", "how", "relation", "siteKeys")}})
    missing = [k for r in res["records"] for k in r["siteKeys"] if k not in {s["key"] for s in plan.get("sources", [])} and k not in docs]
    res["siteKeysNotInPlan"] = sorted(set(missing))
    return res


def build(pkg: str, W: str, today: str) -> dict:
    S = json.load(open(os.path.join(pkg, "sources.json"), encoding="utf-8"))
    P = json.load(open(os.path.join(pkg, "manifest.json"), encoding="utf-8"))
    plan, docs, by_url, by_host = site_index(W)
    probe = load(os.path.join(W, "out", "registry-probe.json"), {}) or {}
    out = os.path.join(W, "out", "srcreg")
    os.makedirs(out, exist_ok=True)
    mp, counts = {}, {"matched": 0, "candidate": 0, "new": 0}
    for r in S["sources"]:
        m = match(r, by_url, by_host)
        counts[m["status"]] += 1
        did = doc_id(S["dataset_id"], r["source_id"])
        mp[f"{S['dataset_id']}:{r['source_id']}"] = {"doc": did, "legacy": r["legacy_row_id"], "siteKeys": m["siteKeys"] if m["status"] == "matched" else []}
        doc = {"key": f"{S['dataset_id']}:{r['source_id']}", "datasetId": S["dataset_id"], "sourceId": r["source_id"], "legacyRowId": r["legacy_row_id"],
               "name": r["name"], "countries": r["countries"], "type": r["source_type"], "priority": r["priority"], "url": r["url"],
               "channelsText": r.get("suggested_channels_text"), "costText": r.get("information_cost_text"), "accessText": r.get("access_conditions_text"),
               "relevanceText": r.get("relevance_text"), "limitsText": r.get("limitations_text"),
               "rss": r.get("rss"), "api": r.get("api"), "research": r.get("research"),
               "proposal": r.get("integration_proposal"),  # values for NEW records only; the site's runtime state is never taken from here
               "match": {**{k: m[k] for k in ("status", "how", "relation", "siteKeys", "note")}, "catalog": catalog_keys(r)},
               "probe": probe.get(str(r["legacy_row_id"])),
               "packageVersion": P["version"], "importedAt": today}
        json.dump(doc, open(os.path.join(out, did + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"map": mp, "note": "Таблица соответствий: внешний ключ пакета (dataset_id:source_id) -> документ реестра и ключи источников сайта. Внутренние идентификаторы сайта внешними не заменяются."},
              open(os.path.join(out, "_map.json"), "w", encoding="utf-8"), ensure_ascii=False)
    meta = {"datasetId": S["dataset_id"], "packageVersion": P["version"], "sourceCount": S["source_count"], "importedAt": today, "counts": counts,
            "scope": S.get("scope"), "profileVersion": json.load(open(os.path.join(pkg, "misb_profile.json"), encoding="utf-8"))["version"],
            "note": "Исследовательский реестр: кандидаты и точки входа, не работающие интеграции. Состояние подключения берётся из источников сайта, а не из пакета."}
    json.dump(meta, open(os.path.join(out, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return {"docs": len(S["sources"]), **counts}


def run_probe(pkg: str, W: str, pause: float, workers: int) -> dict:
    import probe_urls as pu
    S = json.load(open(os.path.join(pkg, "sources.json"), encoding="utf-8"))
    urls, owner = [], {}
    for r in S["sources"]:
        for kind, u in (("url", r.get("url")), ("rss", (r.get("rss") or {}).get("url")), ("api", (r.get("api") or {}).get("reference_url"))):
            if u and u not in owner:
                owner[u] = (r["legacy_row_id"], kind)
                urls.append(u)
    res = pu.run(urls, pause, workers)
    today = dt.date.today().isoformat()
    byrow = {}
    for x in res:
        rid, kind = owner[x["url"]]
        byrow.setdefault(str(rid), {"checkedOn": today, "from": "облако сеанса Claude Code, без входа, robots.txt соблюдён"})[kind] = {
            "url": x["url"], "status": x["status"], "final": x["final"], "robots": x["robots"], "gate": x["gate"],
            "size": x["size"], "procWords": x["proc"], "dates": x["dates"], "trainWords": x["train"], "title": x["title"]}
    json.dump(byrow, open(os.path.join(W, "out", "registry-probe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"urls": len(urls)}


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 3 or a[0] not in ("dry-run", "build", "probe"):
        sys.exit(__doc__)
    os.makedirs(os.path.join(a[2], "out"), exist_ok=True)
    day = a[a.index("--date") + 1] if "--date" in a else dt.date.today().isoformat()
    if a[0] == "dry-run":
        rep = dry_run(a[1], a[2])
        json.dump(rep, open(os.path.join(a[2], "out", "registry-dryrun.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({k: v for k, v in rep.items() if k != "records"}, ensure_ascii=False, indent=1))
    elif a[0] == "build":
        print(json.dumps(build(a[1], a[2], day), ensure_ascii=False))
    else:
        print(json.dumps(run_probe(a[1], a[2], float(a[a.index("--pause") + 1]) if "--pause" in a else 1.0,
                                   int(a[a.index("--workers") + 1]) if "--workers" in a else 6), ensure_ascii=False))
