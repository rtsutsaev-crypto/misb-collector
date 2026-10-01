#!/usr/bin/env python3
"""Rotation sources of the collector for the registry: every reachable record gets a real check in turn.

Usage: python3 registry_plan_v2.py OUT_DIR(srcreg-v3 of import_v2.py build) PLAN(collector/sources-plan.json)
         [--queries search_queries.jsonl] [--cu OUT_CU(srcreg-cu of import_cu.py build) --cu-queries search_queries.jsonl]
         [--fd OUT_FD(srcreg-fd of import_fd.py build)] [--write]

Plan sources (type pages with batch: the collector reads `batch` addresses per run in a circle, the cursor is
kept in meta/rotation), each with `registry` = {url: source_id} so that every page result is written to
meta/registry-checks (collector/registry_checks.py; the cu-* family writes meta/cu-checks, field checksDoc):
  reg2-lists / cu-lists  — the list of purchases is visible from the cloud (access list_visible);
  reg2-pages / cu-pages  — the page opens, a list is not visible (news, programmes, partnership, corporate sections,
                           candidates): rows are signals, not notices: verify asks to check the need;
  reg2-retry / cu-retry  — no answer, 401/403, HTTP errors, nearly empty page: a re-check (policy R07), small batch.
  fd-daily / fd-pages    — fresh demand package: channels of the first wave are read every run, the others in a short
                           circle; they leave the reg2-* rotation (the result is written to the FD-S… and MISB-… records).
Search templates: reg3-search (v3 package) and cu-search (corporate universities package), type search with batch.
Not included: records already served by a collector source (queue collecting/collecting_indirect/blocked_existing),
addresses read by another plan source (a cu-* channel already in a reg2-* rotation is read there), robots.txt
disallowed and captcha (never bypassed). Order: priority wave, then source_id, so wave 1 comes first (cu-pages: wave,
then the channel kind, purchases and expert calls first).
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
CU_GROUPS = {
    "cu-lists": {"access": {"list_visible"}, "batch": 10, "pause": 3,
                 "name": "Корп. университеты · адреса со списком",
                 "note": "Каналы корпоративных университетов и учебных центров (пакет 01.10.2026), у которых из облака виден список (закупки, программы с датами). Лиды — по общим правилам pages.",
                 "verify": "Канал корпоративного университета или учебного центра: проверить, что это закупка или запрос внешнего обучения, и канал отклика."},
    "cu-pages": {"access": {"page_open", "not_tested"}, "batch": 15, "pause": 3,
                 "name": "Корп. университеты · страницы каналов (по кругу)",
                 "note": "Каналы корпоративных университетов, академий и учебных центров компаний RU/BY/KZ (пакет 01.10.2026): новости, программы, партнёрство, вакансии, сайт. Строки — сигналы потребности, не извещения.",
                 "verify": "Сигнал потребности корпоративного университета или учебного центра (новая программа, набор преподавателей, партнёрство, закупка): проверить потребность, заказчика и канал отклика."},
    "cu-retry": {"access": {"unreachable", "forbidden", "http_error", "empty_page", "rate_limited", "login_required"}, "batch": 4, "pause": 3,
                 "name": "Корп. университеты · повторная проверка недоступных",
                 "note": "Каналы корпоративных университетов (пакет 01.10.2026), которые из облака не ответили или вернули ошибку. Повторная проверка по кругу; ошибка — задача доступа, не «потребности нет»."},
}
FD_GROUPS = {
    "fd-daily": {"access": {"list_visible", "page_open", "not_tested"}, "prio": {1}, "batch": 20, "pause": 3,
                 "name": "Свежий спрос · каналы первой волны (каждый запуск)",
                 "note": "Каналы пакета «Свежие потребности» 01.10.2026 первой волны: биржа HRTime, Telegram спикеров, HR/T&D-сообщества, приглашения экспертов школ и ассоциаций. Читаются каждый запуск.",
                 "verify": "Свежая потребность из канала (запрос, поиск эксперта, приглашение провайдеру, субподряд): проверить дату публикации, актуальность, заказчика, оплату и маршрут отклика. Отклик — только человеком."},
    "fd-pages": {"access": {"list_visible", "page_open", "not_tested"}, "prio": {2, 3, None}, "batch": 10, "pause": 3,
                 "name": "Свежий спрос · каналы второй и третьей волны (по кругу)",
                 "note": "Каналы пакета «Свежие потребности» 01.10.2026 второй и третьей волны: провайдеры и авторские сети, форумы, функциональные ассоциации, мероприятия, BY/KZ. Порция за запуск по кругу.",
                 "verify": "Свежая потребность или сигнал из канала: проверить дату публикации, актуальность, заказчика, оплату и маршрут отклика. Ранний сигнал (вопрос, вакансия) — не готовый заказ."},
}
# Order of channel kinds inside cu-pages: where a need is most likely to be stated comes first.
CU_KIND = ["procurement", "expert_application", "partners", "news", "programs", "careers", "university_site", "contacts", "social"]
SKIP_QUEUE = {"collecting", "collecting_indirect", "blocked_existing"}


def served_elsewhere(d, prefix="reg2-"):
    """Served by a collector source other than this family's rotations (so a second run keeps its own entries)."""
    keys = (d.get("match") or {}).get("siteKeys") or []
    if prefix == "cu-" and any(not k.startswith(prefix) for k in keys) and (d.get("match") or {}).get("status") == "matched":
        return True  # a channel of the package already read by another source (reg2-* rotation, corporate page)
    if prefix == "fd-":  # fresh demand takes its channels over from the slow reg2-* rotation, not from other sources
        return d["queue"]["state"] in SKIP_QUEUE and any(not k.startswith(("reg2-", "fd-")) for k in keys)
    return d["queue"]["state"] in SKIP_QUEUE and any(not k.startswith(prefix) for k in keys)


def known_hosts(docs, plan):
    from urllib.parse import urlparse
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
    return sorted(hosts)


def search_source(path, docs, plan, key="reg3-search"):
    """Search templates of a package (search_queries.jsonl) as a rotating search source with stable order."""
    Q = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
    if key == "cu-search":
        # purchases and expert calls first (a stated need), then new programmes, then new channels; wave, main sample
        route = {"Закупки и отбор": 0, "Эксперты и партнеры": 1, "Новые программы": 2, "Новые каналы": 3}
        inst = {}
        for d in docs:
            if d.get("inst"):
                inst[d["inst"]["id"]] = d["inst"]
        Q.sort(key=lambda q: (route.get(q.get("route"), 9), (inst.get(q["institution_id"]) or {}).get("priority") or 9,
                              (inst.get(q["institution_id"]) or {}).get("selection") != "main_200", q["query_id"]))
    old = next((s.get("queries", []) for s in plan["sources"] if s["key"] == key), [])
    want = {}
    for q in Q:
        want.setdefault(q["query"], []).append(q["query_id"])
    # one text can stand for several templates (institutions of one group): it is searched once, the result goes to each
    want = {t: ids[0] if len(ids) == 1 else ids for t, ids in want.items()}
    queries = [q for q in old if q in want] + [q for q in want if q not in old]
    if key == "cu-search":
        return {"key": key, "name": "Корп. университеты · поисковые шаблоны (по кругу)", "type": "search", "source": key,
                "queries": queries, "registry": {q: want[q] for q in queries}, "batch": 8, "planned": 8,
                "knownHosts": known_hosts(docs, plan), "checksDoc": "meta/cu-query-checks",
                "method": "Шаблоны веб-поиска пакета корпоративных университетов (search_queries.jsonl): 8 за запуск по кругу, сначала закупки и приглашения экспертов. "
                          "Любые домены выдачи; карточки открывай не больше 3 на шаблон. Новые домены — в meta/discoveries, результат шаблона — в meta/cu-query-checks.",
                "verify": "Найдено веб-поиском по организации из списка корпоративных университетов: проверить, что это действующая закупка, запрос или набор внешних экспертов, и канал отклика.",
                "note": "OR и кавычки — подсказки веб-поиска; шаблон, который поиск не понял, выполняй упрощённо (название организации и первая часть до OR) и отметь в note. Результат поиска — гипотеза, не подтверждённый спрос."}
    return {"key": "reg3-search", "name": "Реестр v3 · поисковые шаблоны (по кругу)", "type": "search", "source": "reg3-search",
            "queries": queries, "registry": {q: want[q] for q in queries}, "batch": 8, "planned": 8,
            "knownHosts": known_hosts(docs, plan),
            "method": "Шаблоны веб-поиска пакета v3 (search_queries.jsonl): 8 за запуск по кругу. Любые домены выдачи; карточки открывай не больше 3 на шаблон. "
                      "Новые домены — в meta/discoveries (discoveries.py), результат шаблона — в meta/query-checks (registry_checks.py).",
            "verify": "Найдено веб-поиском по шаблону реестра v3: проверить, что это действующий запрос или закупка, и канал отклика.",
            "note": "OR и site: — подсказки веб-поиска; шаблон, который поиск не понял, выполняй упрощённо (первая часть до OR) и отметь в note."}


def rotation(docs, plan, groups, prefix):
    """Plan sources of one family (reg2-* or cu-*) with stable order of addresses."""
    own = set(groups)
    taken = {u for s in plan["sources"] if s["key"] not in own and not (prefix in ("reg2-", "fd-") and s["key"].startswith(("reg2-", prefix)))
             and s.get("type") != "skip" for u in s.get("urls", []) or []}
    if prefix == "cu-":  # addresses read by a reg2-* rotation are not read twice
        taken |= {u for s in plan["sources"] if s["key"].startswith("reg2-") for u in s.get("urls", []) or []}
    out = {}
    for key, g in groups.items():
        sel = [d for d in docs if d["access"]["state"] in g["access"] and not served_elsewhere(d, prefix) and d.get("identity") != "identity_conflict"
               and d.get("url") and d["url"] not in taken and (d.get("readUrl") or d["url"]) not in taken
               and ("prio" not in g or d.get("priority") in g["prio"])]
        if prefix == "cu-":
            kind = lambda d: CU_KIND.index(d.get("cls")) if d.get("cls") in CU_KIND else len(CU_KIND)  # noqa: E731
            sel.sort(key=lambda d: (d.get("priority") or 9, kind(d), d["legacyRowId"]))
        else:
            sel.sort(key=lambda d: (d.get("priority") or 9, d["legacyRowId"]))
        # Stable order: addresses already in the rotation keep their places (the cursor in meta/rotation is a position
        # in this list), new ones are appended; the address read is readUrl (Telegram channels: t.me/s/<name>).
        want = {}
        for d in sel:
            want.setdefault(d.get("readUrl") or d["url"], []).append(d["sourceId"])
            if prefix == "fd-":  # the check also counts for the v3 record of the same channel
                want[d.get("readUrl") or d["url"]] += [x.replace("misb-v3--", "") for x in (d.get("match") or {}).get("v3Docs", [])]
        old = next((s.get("urls", []) for s in plan["sources"] if s["key"] == key), [])
        urls = [u for u in old if u in want] + [u for u in want if u not in old]
        # one address of several records (duplicates are kept by the packages): read once, the result goes to each
        reg = {u: want[u][0] if len(want[u]) == 1 else want[u] for u in urls}
        src = {"key": key, "name": g["name"], "type": "pages", "source": key, "urls": urls, "batch": min(g["batch"], len(urls)),
               "pause": g["pause"], "planned": min(g["batch"], len(urls)), "registry": reg,
               "method": "Порция batch адресов по кругу (meta/rotation). Каждый адрес независим: ошибка одной страницы не останавливает остальные. "
                         "Результат каждой страницы — в " + ("meta/cu-checks" if prefix == "cu-" else "meta/registry-checks") + " (registry_checks.py).",
               "note": g["note"]}
        if prefix == "cu-":
            src["checksDoc"] = "meta/cu-checks"
        if g.get("verify"):
            src["verify"] = g["verify"]
        out[key] = src
    return out


def load_docs(d, pattern):
    return [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(d, pattern)))]


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    docs = load_docs(a[0], "misb-v*--*.json")
    cu = load_docs(a[a.index("--cu") + 1], "misb-cu--*.json") if "--cu" in a else []
    fd = load_docs(a[a.index("--fd") + 1], "misb-fd--*.json") if "--fd" in a else []
    plan = json.load(open(a[1], encoding="utf-8"))
    out = {}
    if fd:
        # fresh demand goes first: its channels leave the reg2-* rotation (read daily or in a short circle instead)
        out.update(rotation(fd, plan, FD_GROUPS, "fd-"))
        plan_fd = {**plan, "sources": [out.get(s["key"], s) for s in plan["sources"]] + [v for k, v in out.items() if k not in {s["key"] for s in plan["sources"]}]}
    else:
        plan_fd = plan
    out.update(rotation(docs, plan_fd, GROUPS, "reg2-"))
    if "--queries" in a:
        out["reg3-search"] = search_source(a[a.index("--queries") + 1], docs + cu + fd, plan)
    if cu:
        # the reg2-* entries computed above go first, so that cu-* never takes an address a reg2 rotation reads
        tmp = {**plan, "sources": [out.get(s["key"], s) for s in plan["sources"]] + [v for k, v in out.items() if k not in {s["key"] for s in plan["sources"]}]}
        out.update(rotation(cu, tmp, CU_GROUPS, "cu-"))
        if "--cu-queries" in a:
            out["cu-search"] = search_source(a[a.index("--cu-queries") + 1], cu, plan, "cu-search")
            out["cu-search"]["knownHosts"] = known_hosts(docs + cu + fd, plan)
    print({k: len(v.get("urls") or v.get("queries") or []) for k, v in out.items()})
    if "--write" in a:
        keys = set(out)
        rest = [s for s in plan["sources"] if s["key"] not in keys]
        # place the registry rotations right before eis-docs (it must stay the last source step)
        i = next((n for n, s in enumerate(rest) if s["key"] == "eis-docs"), len(rest))
        order = list(FD_GROUPS) + list(GROUPS) + ["reg3-search"] + list(CU_GROUPS) + ["cu-search"]
        plan["sources"] = rest[:i] + [out[k] for k in order if k in out] + rest[i:]
        json.dump(plan, open(a[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(a[1], "a", encoding="utf-8").write("\n")
        print("план обновлён:", a[1])


if __name__ == "__main__":
    main()
