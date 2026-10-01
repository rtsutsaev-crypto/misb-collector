#!/usr/bin/env python3
"""Master package v7 (MISB_Sources_v7_Complete, 02.10.2026) -> the site database.

v7 adds a search expansion on top of the consolidated v6/v5 archive (which keeps every earlier package byte for byte):
  data/source_registry.jsonl     1026 source keys found by 120 executed searches (V7-S…), 100 of them read and reviewed;
  data/observed_routes.jsonl     20 addresses followed from the reviewed pages (channels, partner pages, attachments);
  data/commercial_examples.jsonl 21 example invitations and requests (V7-X…) — not fresh leads;
  data/buyer_language_queries    360 search templates in buyer language (RU/BY/KZ × 10 roles);
  v6/v5/data/endpoints.jsonl     4156 addresses of all earlier files (E-…); the routes the site does not read yet
                                 (not in the plan, the registry or the organizations directory) are added here.
Fresh demand v2 (FD-S127…136, FD-O132…196) is imported by import_fd.py from the growth v4 package inside v6/v5/legacy/F02,
and the v6 opportunity layer (need status, buying intent, budget, review note) by --needs of import_fd.py.

Documents: srcreg `misb-v7--<id>` (one per address, compact; the planner reads them), packed for the site into
`_v7-part-NN` (rows of the same documents, under 230 KB each), `_meta-v7`; demand `<V7-X…>` (collection demand).
  queries PKG OUT.jsonl   one ordered file of search templates for the v7-search source (v7 360, v6 144, v4 324)
Commands (PKG = unpacked v7 root, W = work dir as for import_v2.py; W/out/srcreg-{v3,cu,fd} must be built first):
  build PKG W --date ГГГГ-ММ-ДД [--probe probe.json] [--relay-probe relay.json]
Idempotent for the same package, work dir and probe: ids come from the package, never from order or name.
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
import import_v2 as v2  # noqa: E402
from import_registry import load, norm_url, site_index  # noqa: E402

DATASET = "misb-v7"
TRACK_LABEL = {"regional_hr": "Региональные HR-сообщества", "functional": "Руководители функций", "industry": "Отраслевые объединения",
               "providers": "Провайдеры и бизнес-школы", "lms": "LMS и студии курсов", "cu": "КУ и образовательные партнерства",
               "events": "Организаторы деловых мероприятий", "development": "Палаты и центры развития", "business": "Бизнес-клубы",
               "projects": "Проектная работа и эксперты"}
REVIEW_LABEL = {"content_read_partial": "прочитано частично (ревизия v7)", "preview_only": "только карточка (ревизия v7)",
                "fetch_failed": "ошибка чтения (ревизия v7)", "empty_extraction": "пустое извлечение (ревизия v7)",
                "not_checked": "найдено поиском, не прочитано"}
COUNTRY = {"Россия": "RU", "Беларусь": "BY", "Казахстан": "KZ"}
# commercial example kind -> (record class, signal type, commercial status, queue)
KIND = {"archived_project": ("Справочно", "Справочный маршрут", "closed", "История / перепроверить"),
        "course_methodologist_request": ("Конкретная потребность", "Субподряд", "unknown", "Установить дату"),
        "course_development_examples": ("Справочно", "Справочный маршрут", "unknown", "Исследовать"),
        "direct_training_examples": ("Справочно", "Справочный маршрут", "unknown", "Исследовать")}
POOL = ("Пул / сотрудничество", "Приглашение провайдеру", "standing_pool", "Развивать партнерство")
CLOSED_CHAT = re.compile(r"t\.me/(%2B|\+|joinchat)|max\.ru/join|vk\.com/", re.I)


def rd(path):
    return [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]


def known_urls(W: str) -> set:
    """Addresses the site already has: plan sources, collector source docs, registry datasets, organizations directory."""
    plan, docs, by_url, _ = site_index(W)
    # the v7 rotations themselves do not make an address "known" (a rebuild after the plan was written gives the same set)
    own = {norm_url(u) for s in plan.get("sources", []) if s["key"].startswith("v7-") for u in s.get("urls") or []}
    other = {norm_url(u) for s in plan.get("sources", []) if not s["key"].startswith("v7-") for u in s.get("urls") or []}
    known = {u for u in by_url if u not in own or u in other}
    for f in glob.glob(os.path.join(W, "db", "srcreg", "*.json")) + [f for f in glob.glob(os.path.join(W, "out", "srcreg-*", "misb-*--*.json")) if "/srcreg-v7/" not in f]:
        d = load(f, {}) or {}
        for u in (d.get("url"), d.get("readUrl")):
            if u:
                known.add(norm_url(u))
    for f in glob.glob(os.path.join(W, "db", "orgdir", "*.json")):
        for u in re.findall(r'https?://[^"\s\\]+', open(f, encoding="utf-8").read()):
            known.add(norm_url(u))
    return known


def records(pkg: str, W: str):
    """v7 registry, followed routes and the earlier routes the site does not have, in a stable order."""
    R = rd(os.path.join(pkg, "data", "source_registry.jsonl"))
    reviews = {r["review_id"]: r for r in rd(os.path.join(pkg, "data", "priority_reviews.jsonl"))}
    out, seen = [], set()
    for r in R:
        rv = reviews.get(r.get("review_id")) or {}
        out.append({"id": r["source_id"], "url": r["url"], "name": r["name"], "gen": "v7_reviewed" if r.get("review_id") else "v7_candidate",
                    "country": r.get("country_hint"), "platform": r.get("platform"), "track": r.get("research_track"),
                    "kind": rv.get("source_kind") or r.get("source_kind"), "access7": r.get("access_state"), "note": rv.get("note") or r.get("note") or "",
                    "next": rv.get("next_action"), "review": r.get("review_id"), "prio": 1 if r.get("priority") == "resolve_invitation" else 2 if r.get("review_id") else 3,
                    "invitation": rv.get("example_invitation_confirmed"), "requests": rv.get("buyer_request_examples"),
                    "contact": rv.get("public_contact"), "legacy": r.get("legacy_endpoint_ids") or []})
        seen.add(norm_url(r["url"]))
    for o in rd(os.path.join(pkg, "data", "observed_routes.jsonl")):
        if norm_url(o["url"]) in seen:
            continue
        seen.add(norm_url(o["url"]))
        out.append({"id": o["route_id"], "url": o["url"], "name": o.get("label") or o["url"], "gen": "v7_route", "country": None,
                    "platform": "Telegram" if "t.me/" in o["url"] else "Сайт", "track": None, "kind": o.get("relationship"),
                    "access7": o.get("read_state"), "note": f"Адрес найден со страницы {o.get('parent_url')} ({o.get('relationship')}).",
                    "next": None, "review": None, "prio": 1, "invitation": None, "requests": None, "contact": None, "legacy": [], "parent": o.get("source_id")})
    known = known_urls(W)
    for e in rd(os.path.join(pkg, "v6", "v5", "data", "endpoints.jsonl")):
        u = norm_url(e["url"])
        if e.get("role") != "cataloged_route" or u in known or u in seen:
            continue
        seen.add(u)
        out.append({"id": e["endpoint_id"], "url": e["url"], "name": e.get("display_name") or e["url"], "gen": "v5_route",
                    "country": (e.get("country_claims") or [None])[0], "platform": e.get("family"), "track": None,
                    "kind": (e.get("category_claims") or [None])[0], "access7": "not_checked",
                    "note": "Адрес из прежних файлов (" + ", ".join(e.get("file_ids") or []) + "), в сборе сайта его не было.",
                    "next": None, "review": None, "prio": 3, "invitation": None, "requests": None, "contact": None, "legacy": [e["endpoint_id"]]})
    return out


def country_codes(c):
    c = str(c or "")
    codes = [x for x in ("RU", "BY", "KZ", "KG", "UZ") if x in c.upper()] or [COUNTRY[k] for k in COUNTRY if k in c]
    return codes or ["—"]


def build(pkg: str, W: str, today: str, probe: dict, relay: dict | None = None) -> dict:
    v2.DATASET = DATASET
    rows = records(pkg, W)
    plan, docs, by_url, by_host = site_index(W)
    v1 = v2.v1_index(W)
    keys = {s["key"] for s in plan.get("sources", [])}
    out = os.path.join(W, "out", "srcreg-v7")
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "*.json")):
        os.remove(f)
    counts = {}
    for n, r in enumerate(rows, 1):
        rr = {"source_id": r["id"], "source_url": r["url"], "priority_wave": r["prio"], "next_action": r["next"],
              "source_identity_status": None, "metadata_poll_target_hours": 168}
        m = v2.match(rr, keys, by_url, by_host, v1)
        if m["status"] == "candidate" and all(k.startswith(v2.ROTATION) for k in m["siteKeys"]):
            m = {**m, "status": "new", "how": None, "siteKeys": [], "note": "Тот же сайт читается ротацией по другому адресу; этот адрес проверяется отдельно."}
        m.pop("v1Docs", None) if not m.get("v1Docs") else None
        p = probe.get(r["id"])
        acc, acc_note = v2.access_of(p)
        if CLOSED_CHAT.search(r["url"]):
            acc, acc_note = "login_required", "закрытый чат или группа — только разрешённый экспорт владельцем; вход не обходится"
        rl = (relay or {}).get(r["id"])
        if rl is not None and acc not in ("list_visible", "page_open"):
            acc_note += "; через российский релей: " + (f"HTTP {rl['status']}" if rl.get("status") else "нет ответа за 60 с")
        q = v2.queue_of(rr, m, plan, acc, today, docs)
        doc = {"datasetId": DATASET, "sourceId": r["id"], "legacyRowId": n,
               "name": r["name"][:160], "url": r["url"], "countries": country_codes(r["country"]),
               "type": TRACK_LABEL.get(r["track"]) or r["platform"], "cls": r["gen"], "clsLabel": r["platform"], "signals": [],
               "priority": r["prio"], "generation": r["gen"], "kind": r["kind"],
               "research": {"page_status_text": REVIEW_LABEL.get(r["access7"], r["access7"]), "page_result_text": r["note"][:300],
                            "checked_on": "2026-10-02" if r["gen"] != "v5_route" else None, "next_action_text": q["nextStep"] or r["next"]},
               "match": m, "access": {"state": acc, "note": acc_note},
               "probe": {"checkedOn": p.get("checkedOn"), "url": {k: (p.get("url") or {}).get(k) for k in ("status", "gate", "size")}} if p else None,
               "queue": q, "importedAt": today}
        if v2.read_url(r["url"]) != r["url"]:
            doc["readUrl"] = v2.read_url(r["url"])
        if not m.get("note"):
            m.pop("note", None)
        for k in ("review", "invitation", "requests", "contact", "parent"):
            if r.get(k) not in (None, False, ""):
                doc[k] = r[k]
        if r["legacy"]:
            doc["legacyEndpoints"] = r["legacy"][:5]
        json.dump(doc, open(os.path.join(out, re.sub(r"[^A-Za-z0-9_.-]", "-", f"{DATASET}--{r['id']}") + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
        for key, val in (("generation", r["gen"]), ("access", acc), ("queue", q["state"])):
            counts.setdefault(key, {}).setdefault(val, 0)
            counts[key][val] += 1
    # commercial examples -> demand
    dem = os.path.join(W, "out", "demand-v7")
    os.makedirs(dem, exist_ok=True)
    ex = rd(os.path.join(pkg, "data", "commercial_examples.jsonl"))
    for x in ex:
        cls, typ, cst, queue = KIND.get(x["kind"], POOL)
        pub = x.get("publication_date") or (x["observed_date"] if x.get("date_kind") == "publication" else None)
        d = {"observation_id": x["example_id"], "source_id": x["source_id"], "source_name": "Расширение v7 (" + x["kind"] + ")",
             "title": x["summary"].split(".")[0][:120], "summary": x["summary"], "url": x["url"], "published_at": pub,
             "published_at_basis": "дата публикации на странице" if pub else "дата публикации не установлена",
             "publication_date_confidence": "exact" if pub else "unknown", "deadline": None,
             "event_date_original": x["observed_date"] if x.get("date_kind") == "event_start" else None,
             "signal_type_label": typ, "record_class": cls, "queue": queue, "requester_organization": "", "country": None,
             "misb_fit": "uncertain", "fit_label": "Уточнить", "paid_status": "unknown", "commercial_status": cst,
             "contact_route": x.get("public_contact") or "", "next_action": x.get("next_action") or "", "limitations": "Пример из ревизии источника, не свежий лид; открытость и оплата не проверены.",
             "freshness_bucket": "undated" if not pub else None, "qualification_status": "human_not_contacted", "checked_at": "2026-10-02",
             "origin": "v7-2026-10-02", "asOf": "2026-10-02", "importedAt": today}
        json.dump(d, open(os.path.join(dem, x["example_id"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    # the site reads the v7 registry from a few part documents (srcreg/_v7-part-NN, under 230 KB each), not 2000+ documents
    parts_dir = os.path.join(W, "out", "srcreg-v7-parts")
    os.makedirs(parts_dir, exist_ok=True)
    for f in glob.glob(os.path.join(parts_dir, "*.json")):
        os.remove(f)
    parts, cur, size = [], [], 0
    for f in sorted(glob.glob(os.path.join(out, "misb-v7--*.json")), key=lambda f: json.load(open(f, encoding="utf-8"))["legacyRowId"]):
        d = json.load(open(f, encoding="utf-8"))
        n = len(json.dumps(d, ensure_ascii=False).encode())
        if cur and size + n > 230_000:
            parts.append(cur)
            cur, size = [], 0
        cur.append(d)
        size += n
    if cur:
        parts.append(cur)
    for i, rows_ in enumerate(parts, 1):
        json.dump({"datasetId": DATASET, "part": i, "parts": len(parts), "rows": rows_},
                  open(os.path.join(parts_dir, f"_v7-part-{i:02d}.json"), "w", encoding="utf-8"), ensure_ascii=False)
    m = load(os.path.join(pkg, "audit", "metrics.json"), {})
    meta = {"datasetId": DATASET, "packageVersion": "7", "asOf": "2026-10-02", "sourceCount": len(rows), "importedAt": today, "counts": counts,
            "packageMetrics": {k: m.get(k) for k in ("executed_queries", "search_hits", "source_keys_discovered", "new_expansion_candidates", "priority_sources_checked", "commercial_examples", "legacy_routes_retained")},
            "commercialExamples": len(ex),
            "note": "Расширение v7: ключи источников из 120 выполненных поисков (1026, из них 100 прочитаны), адреса, найденные со страниц, "
                    "и адреса прежних файлов, которых не было в сборе сайта. Ключ — домен или канал, не подтверждённая организация."}
    json.dump(meta, open(os.path.join(out, "_meta-v7.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return {"addresses": len(rows), **counts, "demand": len(ex)}


def queries(pkg: str, out: str) -> int:
    """Search templates of v7 (buyer language, countries interleaved), v6 query recipes and v4 phrases in one ordered file."""
    v7 = rd(os.path.join(pkg, "data", "buyer_language_queries.jsonl"))
    by = {}
    for q in v7:
        by.setdefault(q["country"], []).append(q)
    inter = [q for grp in zip(*by.values()) for q in grp] + [q for grp in by.values() for q in grp[min(map(len, by.values())):]]
    rows = [{"query_id": q["query_id"], "query": q["query"], "route": "v7 · " + q["role"]} for q in inter]
    rows += [{"query_id": q["query_id"], "query": q["query"], "route": "v6 · " + q.get("signal", "")} for q in rd(os.path.join(pkg, "v6", "data", "query_recipes.jsonl"))]
    rows += [{"query_id": q["phrase_id"], "query": q["phrase"], "route": "v4 · фраза"} for q in rd(os.path.join(pkg, "v6", "v5", "legacy", "F02", "claude_package", "v4", "query_bank.jsonl"))]
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


if __name__ == "__main__":
    if sys.argv[1:2] == ["queries"]:
        print(queries(sys.argv[2], sys.argv[3]), "шаблонов")
        sys.exit(0)
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != "build":
        sys.exit(__doc__)
    day = a[a.index("--date") + 1] if "--date" in a else dt.date.today().isoformat()
    pr = load(a[a.index("--probe") + 1], {}) if "--probe" in a else {}
    rl = load(a[a.index("--relay-probe") + 1], {}) if "--relay-probe" in a else {}
    print(json.dumps(build(a[1], a[2], day, pr or {}, rl or {}), ensure_ascii=False, indent=1))
