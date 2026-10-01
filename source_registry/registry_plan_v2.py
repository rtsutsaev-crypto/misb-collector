#!/usr/bin/env python3
"""Rotation sources of the collector for the v2 registry: every reachable record gets a real check in turn.

Usage: python3 registry_plan_v2.py OUT_DIR(srcreg-v2 of import_v2.py build) PLAN(collector/sources-plan.json) [--write]

Three plan sources (type pages with batch: the collector reads `batch` addresses per run in a circle, the cursor is
kept in meta/rotation), each with `registry` = {url: source_id} so that every page result is written to
meta/registry-checks (collector/registry_checks.py):
  reg2-lists  — the list of purchases is visible from the cloud (access list_visible);
  reg2-pages  — the page opens, a list is not visible (news, associations, corporate sections, candidates);
                rows are signals, not notices: verify asks to check the need;
  reg2-retry  — no answer, 401/403, HTTP errors, nearly empty page: a re-check (policy R07), small batch.
Not included: records already served by a collector source (queue collecting/collecting_indirect/blocked_existing),
robots.txt disallowed and captcha (never bypassed). Order: priority wave, then source_id, so wave 1 comes first.
The script is idempotent: the same registry gives the same plan entries.
"""
import glob, json, os, sys

GROUPS = {
    "reg2-lists": {"access": {"list_visible"}, "batch": 13, "pause": 3,
                   "name": "Реестр 01.10 · адреса со списком закупок",
                   "note": "Адреса реестра 01.10.2026, у которых из облака виден список закупок. Лиды — по общим правилам pages."},
    "reg2-pages": {"access": {"page_open", "not_tested"}, "batch": 12, "pause": 3,
                   "name": "Реестр 01.10 · страницы каналов (по кругу)",
                   "note": "Адреса реестра 01.10.2026: страница открывается, списка закупок не видно (новости, ассоциации, корпоративные разделы, кандидаты). Строки — сигналы, не извещения.",
                   "verify": "Сигнал из канала реестра (публикация, новость, раздел), а не извещение о закупке: проверить потребность и заказчика."},
    "reg2-retry": {"access": {"unreachable", "forbidden", "http_error", "empty_page", "rate_limited", "login_required"}, "batch": 4, "pause": 3,
                   "name": "Реестр 01.10 · повторная проверка недоступных",
                   "note": "Адреса реестра 01.10.2026, которые из облака не ответили или вернули ошибку. Повторная проверка по кругу; ошибка — задача доступа, не «заказов нет»."},
}
SKIP_QUEUE = {"collecting", "collecting_indirect", "blocked_existing"}


def served_elsewhere(d):
    """Served by a collector source other than the reg2-* rotations (so a second run keeps its own entries)."""
    keys = (d.get("match") or {}).get("siteKeys") or []
    return d["queue"]["state"] in SKIP_QUEUE and any(not k.startswith("reg2-") for k in keys)


def search_source(path, docs, plan):
    """Search templates of the v3 package (search_queries.jsonl) as a rotating search source with stable order."""
    from urllib.parse import urlparse
    Q = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
    hosts = set()
    for d in docs:
        for u in (d.get("url"), d.get("readUrl")):
            h = (urlparse(u or "").hostname or "").lower().removeprefix("www.")
            if h:
                hosts.add(h)
    for s in plan["sources"]:
        for u in (s.get("urls") or []) + [s.get("from") or "", s.get("base") or ""]:
            h = (urlparse(u or "").hostname or "").lower().removeprefix("www.")
            if h:
                hosts.add(h)
    old = next((s.get("queries", []) for s in plan["sources"] if s["key"] == "reg3-search"), [])
    want = {q["query"]: q["query_id"] for q in Q}
    queries = [q for q in old if q in want] + [q for q in want if q not in old]
    return {"key": "reg3-search", "name": "Реестр v3 · поисковые шаблоны (по кругу)", "type": "search", "source": "reg3-search",
            "queries": queries, "registry": {q: want[q] for q in queries}, "batch": 8, "planned": 8,
            "knownHosts": sorted(hosts),
            "method": "Шаблоны веб-поиска пакета v3 (search_queries.jsonl): 8 за запуск по кругу. Любые домены выдачи; карточки открывай не больше 3 на шаблон. "
                      "Новые домены — в meta/discoveries (discoveries.py), результат шаблона — в meta/query-checks (registry_checks.py).",
            "verify": "Найдено веб-поиском по шаблону реестра v3: проверить, что это действующий запрос или закупка, и канал отклика.",
            "note": "OR и site: — подсказки веб-поиска; шаблон, который поиск не понял, выполняй упрощённо (первая часть до OR) и отметь в note."}


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    docs = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(a[0], "misb-v*--*.json")))]
    plan = json.load(open(a[1], encoding="utf-8"))
    taken = {u for s in plan["sources"] if not s["key"].startswith("reg2-") and s.get("type") != "skip" for u in s.get("urls", []) or []}
    out = {}
    for key, g in GROUPS.items():
        sel = [d for d in docs if d["access"]["state"] in g["access"] and not served_elsewhere(d) and d.get("identity") != "identity_conflict"
               and d.get("url") and d["url"] not in taken]
        sel.sort(key=lambda d: (d.get("priority") or 9, d["legacyRowId"]))
        # Stable order: addresses already in the rotation keep their places (the cursor in meta/rotation is a position
        # in this list), new ones are appended; the address read is readUrl (Telegram channels: t.me/s/<name>).
        want = {}
        for d in sel:
            want.setdefault(d.get("readUrl") or d["url"], d["sourceId"])
        old = next((s.get("urls", []) for s in plan["sources"] if s["key"] == key), [])
        urls = [u for u in old if u in want] + [u for u in want if u not in old]
        reg = {u: want[u] for u in urls}
        src = {"key": key, "name": g["name"], "type": "pages", "source": key, "urls": urls, "batch": min(g["batch"], len(urls)),
               "pause": g["pause"], "planned": min(g["batch"], len(urls)), "registry": reg,
               "method": "Порция batch адресов по кругу (meta/rotation). Каждый адрес независим: ошибка одной страницы не останавливает остальные. "
                         "Результат каждой страницы — в meta/registry-checks (registry_checks.py).",
               "note": g["note"]}
        if g.get("verify"):
            src["verify"] = g["verify"]
        out[key] = src
    if "--queries" in a:
        out["reg3-search"] = search_source(a[a.index("--queries") + 1], docs, plan)
    print({k: len(v.get("urls") or v.get("queries") or []) for k, v in out.items()})
    if "--write" in a:
        keys = set(out)
        rest = [s for s in plan["sources"] if s["key"] not in keys]
        # place the registry rotations right before eis-docs (it must stay the last source step)
        i = next((n for n, s in enumerate(rest) if s["key"] == "eis-docs"), len(rest))
        plan["sources"] = rest[:i] + [out[k] for k in list(GROUPS) + (["reg3-search"] if "reg3-search" in out else [])] + rest[i:]
        json.dump(plan, open(a[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(a[1], "a", encoding="utf-8").write("\n")
        print("план обновлён:", a[1])


if __name__ == "__main__":
    main()
