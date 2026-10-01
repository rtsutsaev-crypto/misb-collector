#!/usr/bin/env python3
"""Registry v2 (package MISB_Claude_handoff_v2, 01.10.2026: 282 records MISB-001…MISB-282) -> the site database.

The package is recall-first: every record is kept, duplicates and candidates included (policy R01–R06). A record
is research, not a working integration: its runtime state comes from the site's own sources (collection `sources`)
through `match.siteKeys`, exactly as for the 29.09 registry (import_registry.py). The 29.09 documents stay in the
database; a v2 record that has the same address points to them in `match.v1Docs`.

Commands (PKG = unpacked package dir, W = work dir: W/db/{sources,orgdir,srcreg}/*.json dumps and
W/db/config/sources-plan.json = collector/sources-plan.json of the repository):
  dry-run PKG W [--probe probe.json]                print the reconciliation summary, write nothing
  build   PKG W --date ГГГГ-ММ-ДД [--probe probe.json] [--relay-probe relay.json]  write W/out/srcreg-v2/<doc>.json, _map-v2.json, _meta.json

Idempotent: document id = "misb-v2--" + source_id; a second build with the same package and date gives the same
documents (only `probe`, `queue` and `match` can change, when the site or the probe changed). source_id is never
derived from order or name.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from import_registry import catalog_keys, host_of, load, norm_url, site_index  # noqa: E402

DATASET = "misb-v2"

# Hand-checked links of v2 records to the site's sources, beyond address equality (same notation as MANUAL in
# import_registry.py: direct — the same site; via_api — data reaches the site through an API or aggregator that is
# already connected; partial — only part of the source).
MANUAL = {
    "MISB-001": ("via_api", ["gosplan", "gosplan-delta", "gosplan-plan44", "gosplan-plan223"],
                 "Извещения и планы ЕИС идут через ГосПлан API; прямой поиск ЕИС из облака не открывается."),
    "MISB-011": ("direct", ["speaker-boards"], "Запросы ТРЕБУЕТСЯ читает источник speaker-boards (запись x-trebuetsya-ru заменена им 29.09.2026)."),
    "MISB-204": ("direct", ["speaker-boards"], "Запросы Планеты тренингов читает источник speaker-boards (запись x-planetatreningov-ru заменена им 29.09.2026)."),
    "MISB-186": ("direct", ["kz-mpkz"], "Категория 33 (обучение) MP.kz добавлена вторым адресом источника kz-mpkz 01.10.2026."),
    "MISB-171": ("partial", ["corp-rusal-services"], "Тот же список РУСАЛ «Прочие услуги»; источник читает первую страницу, архивные страницы (PAGEN_1) не читаются."),
}

COUNTRY = [("Казахстан", "KZ"), ("Беларусь", "BY"), ("Белорусс", "BY"), ("Кыргыз", "KG"), ("Киргиз", "KG"),
           ("Узбекистан", "UZ"), ("Армени", "AM"), ("Азербайджан", "AZ"), ("РФ", "RU"), ("Росси", "RU"),
           ("Москв", "RU"), ("СНГ", "CIS"), ("ЕАЭС", "CIS"), ("Междунар", "INT"), ("Глобал", "INT")]

# Package fields kept in the database document (the site shows them); the full record with legacy_record and every
# other field stays in the repository copy of the package (source_registry/handoff_v2/), which is the provenance.
PKG_KEEP = ("next_action", "collection_proposal", "access_note", "cost_note", "risk_note", "evidence_url",
            "parent_hint", "relation_hint", "automation_hint", "metadata_poll_target_hours", "checked_at")
TASK_KEEP = ("task_id", "status", "stage", "missing_access")

VERIFY_LABEL = {"content_read": "содержание страницы прочитано", "search_index": "подтверждено поисковой выдачей",
                "candidate": "кандидат: адрес и применимость уточнить"}


def countries(geo: str | None) -> list:
    g = geo or ""
    out = []
    for k, c in COUNTRY:
        if k in g and c not in out:
            out.append(c)
    return out or ["—"]


def doc_id(sid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", f"{DATASET}--{sid}")


def v1_index(W: str) -> dict:
    """Normalised URL of a 29.09 registry record -> (doc id, its match)."""
    idx = {}
    for p in sorted(glob.glob(os.path.join(W, "db", "srcreg", "*.json"))):
        d = load(p, {}) or {}
        if not d.get("sourceId") or not d.get("url"):
            continue
        idx.setdefault(norm_url(d["url"]), []).append((os.path.basename(p)[:-5], d.get("match") or {}))
    return idx


def match(r: dict, plan_keys: set, by_url, by_host, v1) -> dict:
    urls = [r.get("source_url"), (r.get("legacy_record") or {}).get("url")]
    v1docs, v1m = [], None
    for u in urls:
        for did, m in v1.get(norm_url(u), []) if u else []:
            if did not in v1docs:
                v1docs.append(did)
            if m.get("status") == "matched" and v1m is None:
                v1m = m
    base = {"v1Docs": v1docs}
    if r["source_id"] in MANUAL:
        rel, keys, note = MANUAL[r["source_id"]]
        return {**base, "status": "matched", "how": "manual", "relation": rel, "siteKeys": keys, "note": note}
    if v1m:
        return {**base, "status": "matched", "how": "v1-registry", "relation": v1m.get("relation"),
                "siteKeys": v1m.get("siteKeys", []), "note": v1m.get("note", "")}
    exact = set()
    for u in urls:
        exact |= by_url.get(norm_url(u), set()) if u else set()
    if exact:
        return {**base, "status": "matched", "how": "canonical-url", "relation": "direct", "siteKeys": sorted(exact), "note": ""}
    cand = by_host.get(host_of(r.get("source_url")), set())
    if cand:
        return {**base, "status": "candidate", "how": "same-host", "relation": None, "siteKeys": sorted(cand),
                "note": "Совпал домен, но не адрес раздела: возможно, другой раздел или канал; не склеено."}
    return {**base, "status": "new", "how": None, "relation": None, "siteKeys": [], "note": ""}


def access_of(p: dict | None) -> tuple[str, str]:
    """Probe result of the source URL -> (access state, human note). An error is never 'empty'."""
    if not p or not p.get("url"):
        return "not_tested", "адрес из облака не проверялся"
    x = p["url"]
    st, gate = x.get("status") or 0, x.get("gate") or ""
    if x.get("robots") == "disallowed":
        return "robots_disallowed", "robots.txt запрещает автоматическое чтение — не обходится"
    if gate == "captcha":
        return "captcha", "капча — не обходится; нужен другой маршрут (уведомления, экспорт, ручной импорт)"
    if gate == "login":
        return "login_required", "переадресация на вход — нужен доступ"
    if st == 429:
        return "rate_limited", "HTTP 429 — повторить позже"
    if st in (401, 403):
        return "forbidden", f"HTTP {st} из облака — проверить с сервера сбора (релей) или другой маршрут"
    if st == 0:
        return "unreachable", "нет ответа из облака: " + (x.get("title") or "")[:100]
    if st >= 400:
        return "http_error", f"HTTP {st}"
    if (x.get("procWords") or 0) >= 5 and (x.get("dates") or 0) >= 3:
        return "list_visible", "страница открыта, видны слова закупок и даты — можно подключать чтение списка"
    if (x.get("size") or 0) < 2000:
        return "empty_page", "страница открылась почти пустой (вероятно, список грузится скриптом) — нужен браузер или API"
    return "page_open", "страница открыта, но списка закупок на ней не видно — искать раздел, уведомления или документы"


def queue_of(r: dict, m: dict, plan: dict, access: str, today: str, docs: dict | None = None) -> dict:
    hours = r.get("metadata_poll_target_hours") or (24 if r.get("priority_wave") == 1 else 168)
    due = (dt.date.fromisoformat(today) + dt.timedelta(hours=hours)).isoformat()
    srcs = {s["key"]: s for s in plan.get("sources", [])}
    keys = [k for k in m.get("siteKeys", []) if k in srcs] if m["status"] == "matched" else []
    live = [k for k in keys if srcs[k].get("type") != "skip"]
    if live and all(k.startswith("reg2-") for k in live):
        s = srcs[live[0]]
        return {"state": "rotation", "nextDueAt": due,
                "reason": f"проверяется по кругу источником {live[0]}: {s.get('batch')} адресов за запуск из {len(s.get('urls', []))}",
                "nextStep": "Дождаться проверки в сборе; результат — в «Проверка в сборе». Строк нет — empty_success, ошибка — задача доступа."}
    if live:
        return {"state": "collecting", "nextDueAt": due, "reason": "обслуживается источниками сбора: " + ", ".join(live),
                "nextStep": "Сверить охват разделов и пагинацию; сохранить контрольный результат запуска."}
    covered = [k for k in keys if ((docs or {}).get(k) or {}).get("status") == "covered"]
    if covered:
        via = sorted({str((docs[k] or {}).get("coveredBy") or "агрегатор") for k in covered})
        return {"state": "collecting_indirect", "nextDueAt": due,
                "reason": "прямое чтение выключено, лоты приходят через " + ", ".join(via) + " (" + ", ".join(covered) + ")",
                "nextStep": "Проверить, что косвенный маршрут покрывает все разделы; прямое чтение — при появлении доступа."}
    if keys:
        why = "; ".join((srcs[k].get("note") or "")[:140] for k in keys)
        return {"state": "blocked_existing", "nextDueAt": due, "reason": "источник сбора выключен: " + why,
                "nextStep": "Снять блокировку (доступ, релей, ключ) и вернуть источник в план."}
    if m["status"] == "matched":
        return {"state": "collecting_indirect", "nextDueAt": due, "reason": m.get("note") or "данные приходят через другой источник",
                "nextStep": "Проверить, что косвенный маршрут покрывает этот канал."}
    if r.get("source_state") == "candidate":
        return {"state": "candidate_verify", "nextDueAt": due, "reason": "кандидат пакета", "nextStep": r.get("next_action")}
    if m["status"] == "candidate":
        return {"state": "reconcile", "nextDueAt": due, "reason": m["note"], "nextStep": "Сравнить раздел с подключённым; при отличии — отдельный источник."}
    step = {"list_visible": "Подключить чтение списка (pages/discover) и сохранить контрольный результат.",
            "page_open": "Найти раздел закупок/новостей, уведомления, документы; затем подключить.",
            "empty_page": "Проверить в браузере или найти API/экспорт.",
            "not_tested": "Проверить адрес."}.get(access, "Зафиксировать ограничение; искать другой маршрут (уведомления, email, экспорт, ручной импорт).")
    return {"state": "adapter_needed" if access in ("list_visible", "page_open", "empty_page", "not_tested") else "access_task",
            "nextDueAt": today if access == "list_visible" else due, "reason": access, "nextStep": step}


def records(pkg: str):
    S = [json.loads(x) for x in open(os.path.join(pkg, "sources.jsonl"), encoding="utf-8") if x.strip()]
    T = {t["source_id"]: t for t in (json.loads(x) for x in open(os.path.join(pkg, "connector_tasks.jsonl"), encoding="utf-8") if x.strip())}
    R = {}
    for x in open(os.path.join(pkg, "source_relationships.jsonl"), encoding="utf-8"):
        if x.strip():
            rel = json.loads(x)
            for k in ("from_source_id", "to_source_id", "source_id", "related_source_id"):
                if rel.get(k):
                    R.setdefault(rel[k], []).append(rel)
    ids = [r["source_id"] for r in S]
    assert len(ids) == len(set(ids)), "source_id must be unique"
    return S, T, R


def reconcile(pkg: str, W: str, probe: dict, today: str, relay: dict | None = None):
    S, T, R = records(pkg)
    plan, docs, by_url, by_host = site_index(W)
    v1 = v1_index(W)
    probe_url = {}
    for sid, pr in probe.items():
        for kind in ("url", "evidence"):
            if (pr or {}).get(kind, {}).get("url"):
                probe_url.setdefault(norm_url(pr[kind]["url"]), {"checkedOn": pr.get("checkedOn"), "from": pr.get("from")})[kind if kind == "url" else "evidence"] = pr[kind]
    out = []
    for r in S:
        m = match(r, {s["key"] for s in plan.get("sources", [])}, by_url, by_host, v1)
        m["catalog"] = catalog_keys({"url": r.get("source_url")})
        p = probe.get(r["source_id"])
        if not p and r.get("source_url"):  # the same address was probed once for another record
            hit = probe_url.get(norm_url(r["source_url"]))
            p = {"checkedOn": hit["checkedOn"], "from": hit["from"], "url": hit.get("url") or hit.get("evidence")} if hit else None
        acc, acc_note = access_of(p)
        rl = (relay or {}).get(r["source_id"])
        if rl is not None and acc not in ("list_visible", "page_open"):
            acc_note += ("; через российский релей: " + (f"HTTP {rl['status']}" if rl.get("status") else "нет ответа за 60 с"))
        q = queue_of(r, m, plan, acc, today, docs)
        out.append((r, T.get(r["source_id"]), R.get(r["source_id"], []), m, p, acc, acc_note, q))
    return out


def summary(rows) -> dict:
    c = lambda f: {k: sum(1 for x in rows if f(x) == k) for k in sorted({f(x) for x in rows})}  # noqa: E731
    return {"records": len(rows), "match": c(lambda x: x[3]["status"]), "how": c(lambda x: str(x[3]["how"])),
            "queue": c(lambda x: x[7]["state"]), "access": c(lambda x: x[5]),
            "generation": c(lambda x: x[0]["source_generation"]), "linkedToV1": sum(1 for x in rows if x[3]["v1Docs"])}


def build(pkg: str, W: str, today: str, probe: dict, relay: dict | None = None) -> dict:
    rows = reconcile(pkg, W, probe, today, relay)
    man = json.load(open(os.path.join(pkg, "manifest.json"), encoding="utf-8"))
    out = os.path.join(W, "out", "srcreg-v2")
    os.makedirs(out, exist_ok=True)
    mp = {}
    for r, task, rels, m, p, acc, acc_note, q in rows:
        sid = r["source_id"]
        did = doc_id(sid)
        mp[sid] = {"doc": did, "siteKeys": m["siteKeys"] if m["status"] == "matched" else [], "v1Docs": m["v1Docs"]}
        doc = {
            "key": f"{DATASET}:{sid}", "datasetId": DATASET, "sourceId": sid, "legacyRowId": int(re.sub(r"\D", "", sid) or 0),
            "name": r.get("source_name"), "url": r.get("source_url"), "countries": countries(r.get("geography")),
            "type": r.get("source_category"), "cls": r.get("source_class"), "signals": r.get("signal_types") or [],
            "priority": r.get("priority_wave"), "generation": r.get("source_generation"), "state": r.get("source_state"),
            "verification": r.get("verification_status"), "evidenceScope": r.get("evidence_scope"),
            # columns shared with the 29.09 registry view
            "research": {"page_status_text": VERIFY_LABEL.get(r.get("verification_status"), r.get("verification_status")),
                         "page_result_text": r.get("evidence_summary"), "checked_on": r.get("checked_at"),
                         "next_action_text": q["nextStep"] or r.get("next_action")},
            "match": m, "access": {"state": acc, "note": acc_note}, "probe": p, "queue": q,
            "task": {k: (task or {}).get(k) for k in TASK_KEEP}, "relations": [{k: x.get(k) for k in ("relationship_id", "from_source_id", "to_source_id", "relation", "confidence", "note", "action")} for x in rels],
            "pkg": {k: r.get(k) for k in PKG_KEEP},  # full record: source_registry/handoff_v2/sources.jsonl
            "packageVersion": man.get("package_version"), "importedAt": today,
        }
        json.dump(doc, open(os.path.join(out, did + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"map": mp, "note": "source_id пакета v2 -> документ реестра, ключи источников сбора и документы реестра 29.09 с тем же адресом."},
              open(os.path.join(out, "_map-v2.json"), "w", encoding="utf-8"), ensure_ascii=False)
    s = summary(rows)
    meta = {"datasetId": DATASET, "packageVersion": man.get("package_version"), "sourceCount": len(rows), "importedAt": today,
            "counts": s, "previous": {"datasetId": "misb-research-20260929", "note": "100 записей пакета 29.09 остаются в базе; совпадающие адреса связаны через match.v1Docs."},
            "note": "Реестр полноты: все записи сохраняются (кандидаты, дубли, закрытые). Статусы пакета (content_read, search_index, candidate) — "
                    "подтверждение источника, не работа сборщика; состояние подключения берётся из источников сайта."}
    json.dump(meta, open(os.path.join(out, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
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
