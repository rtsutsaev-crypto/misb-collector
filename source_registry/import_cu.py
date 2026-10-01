#!/usr/bin/env python3
"""Corporate universities package (MISB_corporate_universities, 01.10.2026: 247 institutions, 474 channels) -> the site database.

The package lists corporate universities, academies and training centres of companies in RU / BY / KZ (main sample
200 + reserve 47) and their public channels: site, news, programmes, purchases, partnership, contacts, vacancies,
social networks. A channel is research, not a working integration: as for the v2/v3 registry (import_v2.py), its
runtime state comes from the site's own sources through `match.siteKeys`, and every channel is kept (duplicates,
reserve and candidates included; policy of the package: recall first, types are not unified).

Each channel becomes one registry document `misb-cu--CU-SRC-0001` (collection srcreg) with the institution
embedded (`inst`): type, group, parent company, industry, city, selection, fit and need hypotheses. The links to
the v3 registry given by the package (existing_misb_source_ids) are kept in `match.v3Docs`; a channel whose address
is already read by a v3 rotation (reg2-*) gets that rotation as its collector source and is not read twice.

Commands (PKG = unpacked claude_package dir, W = work dir as for import_v2.py):
  dry-run PKG W [--probe probe.json]                         print the reconciliation summary, write nothing
  build   PKG W --date ГГГГ-ММ-ДД [--probe probe.json] [--relay-probe relay.json]
          write W/out/srcreg-cu/<doc>.json, _map-cu.json and _meta-cu.json
Idempotent: document id = "misb-cu--" + source_id; source_id is never derived from order or name.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import import_v2 as v2  # noqa: E402
from import_registry import catalog_keys, load, norm_url, site_index  # noqa: E402

DATASET = "misb-cu"
v2.DATASET = DATASET

# Institution fields kept in the database document; the full record stays in source_registry/handoff_cu/.
INST_KEEP = {"institution_id": "id", "name": "name", "alias_of_name": "alias", "institution_type": "type",
             "institution_type_label": "typeLabel", "country": "country", "city": "city", "corporate_group": "group",
             "parent_company": "parent", "industry": "industry", "primary_url": "url", "selection": "selection",
             "priority": "priority", "verified": "verified"}
# Long texts (fit, need hypotheses, buyer role, risks, commercial route, collection proposal) repeat for every channel
# of an institution: they stay in the repository copy only, the database keeps what the site shows.
PKG_KEEP = ("evidence_url", "paid_order_evidence_status", "collection_status", "checked_at")
TASK_KEEP = ("task_id", "status", "last_result")


def records(pkg: str):
    rd = lambda n: [json.loads(x) for x in open(os.path.join(pkg, n), encoding="utf-8") if x.strip()]  # noqa: E731
    S, I = rd("sources.jsonl"), {i["institution_id"]: i for i in rd("institutions.jsonl")}
    T = {t["source_id"]: t for t in rd("connector_tasks.jsonl")}
    R = {}
    for rel in rd("source_relationships.jsonl"):
        for k in ("from_id", "to_id"):
            if rel.get(k):
                R.setdefault(rel[k], []).append(rel)
    ids = [r["source_id"] for r in S]
    assert len(ids) == len(set(ids)), "source_id must be unique"
    assert all(r["institution_id"] in I for r in S), "every channel must point to an institution"
    return S, I, T, R


def inst_doc(i: dict) -> dict:
    return {short: i.get(k) for k, short in INST_KEEP.items()}


def reconcile(pkg: str, W: str, probe: dict, today: str, relay: dict | None = None):
    S, I, T, R = records(pkg)
    plan, docs, by_url, by_host = site_index(W)
    v1 = v2.v1_index(W)
    keys = {s["key"] for s in plan.get("sources", [])}
    out = []
    for r in S:
        rr = {**r, "source_identity_status": None, "metadata_poll_target_hours": 24 if r.get("priority_wave") == 1 else 168}
        m = v2.match(rr, keys, by_url, by_host, v1)
        if m["status"] == "candidate" and all(k.startswith(v2.ROTATION) for k in m["siteKeys"]):
            # the same site is read in a rotation at another address: a different page, checked on its own
            m = {**m, "status": "new", "how": None, "siteKeys": [], "note": "Тот же сайт читается ротацией по другому адресу (" + ", ".join(m["siteKeys"]) + "); этот адрес проверяется отдельно."}
        m["catalog"] = catalog_keys({"url": r.get("source_url")})
        m["v3Docs"] = ["misb-v3--" + x for x in r.get("existing_misb_source_ids") or []]
        p = probe.get(r["source_id"])
        acc, acc_note = v2.access_of(p)
        rl = (relay or {}).get(r["source_id"])
        if rl is not None and acc not in ("list_visible", "page_open"):
            acc_note += "; через российский релей: " + (f"HTTP {rl['status']}" if rl.get("status") else "нет ответа за 60 с")
        q = v2.queue_of(rr, m, plan, acc, today, docs)
        out.append((r, I[r["institution_id"]], T.get(r["source_id"]), R.get(r["source_id"], []), m, p, acc, acc_note, q))
    return out


def summary(rows) -> dict:
    c = lambda f: {k: sum(1 for x in rows if f(x) == k) for k in sorted({f(x) for x in rows})}  # noqa: E731
    inst = {}
    for x in rows:
        inst.setdefault(x[1]["institution_id"], x[1])
    ci = lambda f: {k: sum(1 for i in inst.values() if f(i) == k) for k in sorted({f(i) for i in inst.values()})}  # noqa: E731
    return {"channels": len(rows), "institutions": len(inst), "selection": ci(lambda i: i.get("selection")),
            "countries": ci(lambda i: i.get("country")), "types": ci(lambda i: i.get("institution_type_label")),
            "channelTypes": c(lambda x: x[0].get("channel_type_label")), "match": c(lambda x: x[4]["status"]),
            "queue": c(lambda x: x[8]["state"]), "access": c(lambda x: x[6]),
            "linkedToV3": sum(1 for x in rows if x[4]["v3Docs"])}


def build(pkg: str, W: str, today: str, probe: dict, relay: dict | None = None) -> dict:
    rows = reconcile(pkg, W, probe, today, relay)
    man = json.load(open(os.path.join(pkg, "manifest.json"), encoding="utf-8"))
    out = os.path.join(W, "out", "srcreg-cu")
    os.makedirs(out, exist_ok=True)
    mp = {}
    for r, inst, task, rels, m, p, acc, acc_note, q in rows:
        sid = r["source_id"]
        did = v2.doc_id(sid)
        mp[sid] = {"doc": did, "institution": inst["institution_id"], "siteKeys": m["siteKeys"] if m["status"] == "matched" else [], "v3Docs": m["v3Docs"]}
        doc = {
            "key": f"{DATASET}:{sid}", "datasetId": DATASET, "sourceId": sid, "legacyRowId": int(re.sub(r"\D", "", sid) or 0),
            "name": r.get("source_name"), "url": r.get("source_url"), "readUrl": v2.read_url(r.get("source_url")),
            "countries": [r.get("country") or "—"], "type": inst.get("institution_type_label"),
            "cls": r.get("channel_type"), "clsLabel": r.get("channel_type_label"), "signals": [], "surface": None,
            "identity": None, "route": None, "priority": r.get("priority_wave"), "generation": "cu_v1",
            "state": r.get("source_state"), "verification": r.get("verification"), "evidenceScope": r.get("evidence_scope"),
            "research": {"page_status_text": v2.VERIFY_LABEL.get(r.get("verification"), r.get("verification")),
                         "page_result_text": "Что отслеживать: " + (r.get("what_to_monitor") or ""), "checked_on": r.get("checked_at"),
                         "next_action_text": q["nextStep"] or r.get("next_action")},
            "match": m, "access": {"state": acc, "note": acc_note},
            "probe": {"checkedOn": p.get("checkedOn"), "url": {k: (p.get("url") or {}).get(k) for k in ("url", "status", "gate", "robots", "size", "procWords", "dates")}} if p else None,
            "queue": q,
            "task": {k: (task or {}).get(k) for k in TASK_KEEP},
            "relations": [{k: x.get(k) for k in ("relationship_id", "from_id", "to_id", "relation", "action")} for x in rels],
            "inst": inst_doc(inst),
            "pkg": {k: r.get(k) for k in PKG_KEEP},  # full record: source_registry/handoff_cu/sources.jsonl
            "packageVersion": man.get("package_version"), "importedAt": today,
        }
        json.dump(doc, open(os.path.join(out, did + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"map": mp, "note": "source_id пакета корпоративных университетов -> документ реестра, организация, ключи источников сбора и записи v3."},
              open(os.path.join(out, "_map-cu.json"), "w", encoding="utf-8"), ensure_ascii=False)
    s = summary(rows)
    meta = {"datasetId": DATASET, "packageVersion": man.get("package_version"), "sourceCount": len(rows), "importedAt": today,
            "counts": s, "searchQueries": sum(1 for x in open(os.path.join(pkg, "search_queries.jsonl"), encoding="utf-8") if x.strip()),
            "discoveryRecipes": sum(1 for x in open(os.path.join(pkg, "discovery_recipes.jsonl"), encoding="utf-8") if x.strip()),
            "note": "Корпоративные университеты, академии и учебные центры компаний RU/BY/KZ: организации и их каналы. Подтверждение "
                    "(content_read, search_index, candidate) — как найден канал, не работа сборщика и не подтверждённый спрос."}
    json.dump(meta, open(os.path.join(out, "_meta-cu.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return s


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 3 or a[0] not in ("dry-run", "build"):
        sys.exit(__doc__)
    day = a[a.index("--date") + 1] if "--date" in a else dt.date.today().isoformat()
    pr = load(a[a.index("--probe") + 1], {}) if "--probe" in a else {}
    rl = load(a[a.index("--relay-probe") + 1], {}) if "--relay-probe" in a else {}
    if a[0] == "dry-run":
        print(json.dumps(summary(reconcile(a[1], a[2], pr or {}, day)), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(build(a[1], a[2], day, pr or {}, rl or {}), ensure_ascii=False, indent=1))
