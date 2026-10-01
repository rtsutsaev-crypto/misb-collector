#!/usr/bin/env python3
"""Fresh demand package (MISB_fresh_demand, 01.10.2026: 126 channels, 131 observations, 54 extra addresses) -> the site database.

The package is a fresh-demand pass over channels of the registry: exchanges of training orders, Telegram channels of
HR/L&D and speakers, training providers, associations, events, BY/KZ. It brings two kinds of records:
  sources (FD-S…)       channels with the result of the check (access, window coverage, review outcome);
                        123 of them point to the v3 registry (legacy_source_id), the link is kept in match.v3Docs;
  observations (FD-O…)  concrete requests, expert searches, provider invitations, subcontracts, standing pools,
                        early signals and reference routes, each with the publication date and its basis.
Channels become registry documents `misb-fd--FD-S001` (collection srcreg, like import_cu.py); observations become
documents of the collection `demand` (doc id = observation_id), shown on the site tab «Запросы и сигналы» next to the
signals the collector finds. Nothing is dropped (do_not_drop): closed, undated and duplicate records are kept.

Commands (PKG = unpacked package dir, W = work dir as for import_v2.py):
  dry-run PKG W [--probe probe.json]
  build   PKG W --date ГГГГ-ММ-ДД [--probe probe.json]   write W/out/srcreg-fd/*.json, _meta-fd.json, W/out/demand/*.json
Idempotent: ids come from the package (FD-S…, FD-O…), never from order or name.
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
from import_registry import catalog_keys, load, site_index  # noqa: E402

DATASET = "misb-fd"
ORIGIN = "fd-2026-10-01"

ACCESS_LABEL = {"feed_read": "лента прочитана", "page_read": "страница прочитана", "profile_only": "открыт только профиль",
                "unavailable": "недоступно", "blocked": "заблокировано", "login_required": "нужен вход", "timeout": "таймаут",
                "identity_conflict": "конфликт идентичности"}
OUTCOME_LABEL = {"dated_relevant_found": "найдены датированные потребности", "undated_relevant_found": "найдены потребности без даты",
                 "standing_invitation_found": "постоянное приглашение / пул", "only_early_signals": "только ранние сигналы",
                 "no_relevant_in_viewed_sample": "в просмотренной выборке нет", "not_assessable": "спрос не оценить"}
PKG_KEEP = ("access_result", "window_coverage", "date_evidence", "review_outcome", "limitations", "access_cost_note",
            "evidence_url", "observation_ids", "research_group", "legacy_source_id")
OBS_KEEP = ("observation_id", "source_id", "source_name", "title", "summary", "url", "evidence_url", "published_at",
            "published_at_basis", "publication_date_confidence", "deadline", "deadline_original", "deadline_expired",
            "event_date_original", "signal_type", "signal_type_label", "record_class", "queue", "requester_organization",
            "requester_role", "country", "audience", "misb_fit", "fit_label", "fit_reason", "paid_status", "paid_label",
            "budget_original", "commercial_status", "status_label", "status_basis", "contact_route", "priority",
            "next_action", "limitations", "freshness_bucket", "freshness_label", "qualification_status", "checked_at")


def rd(pkg, name):
    return [json.loads(x) for x in open(os.path.join(pkg, name), encoding="utf-8") if x.strip()]


def reconcile(pkg: str, W: str, probe: dict, today: str):
    S = rd(pkg, "sources.jsonl")
    ids = [s["source_id"] for s in S]
    assert len(ids) == len(set(ids)), "source_id must be unique"
    plan, docs, by_url, by_host = site_index(W)
    v1 = v2.v1_index(W)
    keys = {s["key"] for s in plan.get("sources", [])}
    out = []
    for s in S:
        rr = {"source_id": s["source_id"], "source_url": s["url"], "priority_wave": s.get("priority"),
              "source_identity_status": "identity_conflict" if s.get("access_result") == "identity_conflict" else None,
              "metadata_poll_target_hours": 24 if s.get("priority") == 1 else 168, "next_action": s.get("next_action")}
        m = v2.match(rr, keys, by_url, by_host, v1)
        if m["status"] == "candidate" and all(k.startswith(v2.ROTATION) for k in m["siteKeys"]):
            m = {**m, "status": "new", "how": None, "siteKeys": [], "note": "Тот же сайт читается ротацией по другому адресу (" + ", ".join(m["siteKeys"]) + "); этот адрес проверяется отдельно."}
        m["catalog"] = catalog_keys({"url": s["url"]})
        m["v3Docs"] = ["misb-v3--" + s["legacy_source_id"]] if s.get("legacy_source_id") else []
        p = probe.get(s["source_id"])
        acc, acc_note = v2.access_of(p)
        q = v2.queue_of(rr, m, plan, acc, today, docs)
        out.append((s, m, p, acc, acc_note, q))
    return out


def summary(rows, obs) -> dict:
    c = lambda xs, f: {k: sum(1 for x in xs if f(x) == k) for k in sorted({str(f(x)) for x in xs})}  # noqa: E731
    return {"channels": len(rows), "observations": len(obs), "match": c(rows, lambda x: x[1]["status"]),
            "queue": c(rows, lambda x: x[5]["state"]), "access": c(rows, lambda x: x[3]),
            "packageAccess": c(rows, lambda x: x[0].get("access_result")), "outcome": c(rows, lambda x: x[0].get("review_outcome")),
            "recordClass": c(obs, lambda o: o.get("record_class")), "freshness": c(obs, lambda o: o.get("freshness_bucket")),
            "obsQueue": c(obs, lambda o: o.get("queue")), "linkedToV3": sum(1 for x in rows if x[1]["v3Docs"])}


def build(pkg: str, W: str, today: str, probe: dict) -> dict:
    rows = reconcile(pkg, W, probe, today)
    obs = rd(pkg, "observations.jsonl")
    man = json.load(open(os.path.join(pkg, "manifest.json"), encoding="utf-8"))
    out = os.path.join(W, "out", "srcreg-fd")
    dem = os.path.join(W, "out", "demand")
    os.makedirs(out, exist_ok=True)
    os.makedirs(dem, exist_ok=True)
    for s, m, p, acc, acc_note, q in rows:
        sid = s["source_id"]
        doc = {
            "key": f"{DATASET}:{sid}", "datasetId": DATASET, "sourceId": sid, "legacyRowId": int(re.sub(r"\D", "", sid) or 0),
            "name": s.get("name"), "url": s.get("url"), "readUrl": v2.read_url(s.get("url")), "countries": s.get("countries") or ["—"],
            "type": s.get("channel_family"), "cls": s.get("research_group"), "clsLabel": s.get("channel_family"), "signals": [],
            "surface": None, "identity": "identity_conflict" if s.get("access_result") == "identity_conflict" else None, "route": None,
            "priority": s.get("priority"), "generation": "fd_v1", "state": "cataloged", "verification": s.get("verification"),
            "evidenceScope": s.get("window_coverage"),
            "research": {"page_status_text": ACCESS_LABEL.get(s.get("access_result"), s.get("access_result")) + " · " + OUTCOME_LABEL.get(s.get("review_outcome"), s.get("review_outcome") or ""),
                         "page_result_text": s.get("actual_scope"), "checked_on": s.get("checked_at"),
                         "next_action_text": q["nextStep"] or s.get("next_action")},
            "match": m, "access": {"state": acc, "note": acc_note},
            "probe": {"checkedOn": p.get("checkedOn"), "url": {k: (p.get("url") or {}).get(k) for k in ("url", "status", "gate", "robots", "size", "procWords", "dates")}} if p else None,
            "queue": q, "pkg": {k: s.get(k) for k in PKG_KEEP},  # full record: source_registry/handoff_fd/sources.jsonl
            "packageVersion": man.get("version"), "importedAt": today,
        }
        json.dump(doc, open(os.path.join(out, re.sub(r"[^A-Za-z0-9_.-]", "-", f"{DATASET}--{sid}") + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    for o in obs:
        d = {k: o.get(k) for k in OBS_KEEP}
        d.update({"origin": ORIGIN, "asOf": man.get("as_of"), "importedAt": today})
        json.dump(d, open(os.path.join(dem, o["observation_id"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    s = summary(rows, obs)
    meta = {"datasetId": DATASET, "packageVersion": man.get("version"), "asOf": man.get("as_of"), "sourceCount": len(rows),
            "importedAt": today, "counts": s, "packageCounts": man.get("counts"),
            "discovery": sum(1 for _ in rd(pkg, "discovery.jsonl")),
            "note": "Свежие потребности: каналы с результатом проверки на 01.10.2026 и наблюдения (запросы, поиск экспертов, "
                    "приглашения, пулы, ранние сигналы). Свежесть не означает открытый или оплачиваемый заказ; с покупателями не связывались."}
    json.dump(meta, open(os.path.join(out, "_meta-fd.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return s


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 3 or a[0] not in ("dry-run", "build"):
        sys.exit(__doc__)
    day = a[a.index("--date") + 1] if "--date" in a else dt.date.today().isoformat()
    pr = load(a[a.index("--probe") + 1], {}) if "--probe" in a else {}
    if a[0] == "dry-run":
        print(json.dumps(summary(reconcile(a[1], a[2], pr or {}, day), rd(a[1], "observations.jsonl")), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(build(a[1], a[2], day, pr or {}), ensure_ascii=False, indent=1))
