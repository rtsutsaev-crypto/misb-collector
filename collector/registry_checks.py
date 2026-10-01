"""Результат проверки адресов реестра в обычном запуске (документ meta/registry-checks): что реально прочитано.

Запуск после источника с полем registry: python3 registry_checks.py [--doc старый.json] --plan sources-plan.json
  --key <key источника> --results reg_results.json --now ISO --out rc.json
reg_results.json — список {url, status, rows, matched, fresh, note} по каждой прочитанной странице:
  status "ok" — страница прочитана, есть строки по теме; "empty_success" — прочитана, строк по теме нет (это не ошибка
  и не повод выключать источник); "failed" — не прочитана: 403, 429, капча, вход, таймаут, пустая страница
  (это задача доступа, а не «заказов нет»).
Адрес -> source_id реестра берётся из поля registry источника в плане ({url: "MISB-…"}, {url: "CU-SRC-…"}; у поиска
один текст шаблона может стоять за несколькими шаблонами — тогда список id, результат пишется каждому). Для
каждого source_id хранится последний результат и счётчики; история — последние 3 проверки. Документ куда писать —
поле checksDoc источника (по умолчанию meta/registry-checks; каналы корпоративных университетов — meta/cu-checks,
их поисковые шаблоны — meta/cu-query-checks). Документ — не больше 240 КБ: при переполнении сначала сокращается
история, затем заметки (успешных — целиком, остальных — до 60 знаков), в крайнем случае — самые давние проверки.
"""
import argparse, json, os

STATUSES = ("ok", "empty_success", "failed")
LIMIT = 240_000


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--doc")
    a.add_argument("--plan", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--results", required=True)
    a.add_argument("--now", required=True)
    a.add_argument("--out", required=True)
    x = a.parse_args()
    plan = json.load(open(x.plan, encoding="utf-8"))
    src = next((s for s in plan.get("sources", []) if s.get("key") == x.key), None)
    if not src or not src.get("registry"):
        raise SystemExit(f"у источника {x.key} нет поля registry")
    reg = src["registry"]
    old = {}
    if x.doc and os.path.exists(x.doc):
        d = json.load(open(x.doc, encoding="utf-8"))
        d = d.get("data", d) if isinstance(d, dict) and "id" in d and "data" in d else d
        old = d.get("items", {})
    res = json.load(open(x.results, encoding="utf-8"))
    done, unknown = 0, []
    for r in res:
        sids = reg.get(r.get("url"))
        if not sids:
            unknown.append(r.get("url"))
            continue
        for sid in sids if isinstance(sids, list) else [sids]:  # one search text can stand for several templates
            done += put(old, sid, r, x)
    json_out(old, x)
    by = {s: sum(1 for r in res if r.get("status") == s and reg.get(r.get("url"))) for s in STATUSES}
    print(f"записано {done} проверок ({', '.join(f'{k} {v}' for k, v in by.items())}); всего в документе {len(old)}"
          + (f"; адреса без source_id: {len(unknown)}" if unknown else ""))


def put(old, sid, r, x):
    """The result of one read (or one search) for one registry record."""
    st = r.get("status") if r.get("status") in STATUSES else "failed"
    cnt = {"ok": "okCount", "empty_success": "emptyCount", "failed": "failCount"}[st]
    it = old.get(sid) or {"checks": 0, "okCount": 0, "emptyCount": 0, "failCount": 0, "history": []}
    it["checks"] = int(it.get("checks", 0)) + 1
    it[cnt] = int(it.get(cnt, 0)) + 1
    last = {"at": x.now[:10], "status": st, "rows": r.get("rows"), "matched": r.get("matched"), "fresh": r.get("fresh"),
            "note": str(r.get("note") or "")[:160], "via": x.key}
    it.pop("url", None)  # the address is in the plan (registry) and in the registry document
    it.update(last)
    it["history"] = ([{k: last[k] for k in ("at", "status", "fresh")}] + [{k: h.get(k) for k in ("at", "status", "fresh")} for h in it.get("history", [])])[:3]
    old[sid] = it
    return 1


def json_out(old, x):
    def fit(step):
        """Apply step to items, oldest first, until the document fits LIMIT (sizes kept per item: no full re-dumps)."""
        one = lambda k: len(json.dumps({k: old[k]}, ensure_ascii=False, separators=(",", ":")).encode())  # noqa: E731
        sizes = {k: one(k) for k in old}
        total = sum(sizes.values())
        for k in sorted(old, key=lambda k: old[k].get("at", "")):
            if total <= LIMIT:
                return
            step(k)
            n = one(k) if k in old else 0
            total += n - sizes[k]
            sizes[k] = n

    fit(lambda k: old[k].update(history=old[k].get("history", [])[:1]))
    fit(lambda k: old[k].update(note="") if old[k].get("status") != "failed" else None)
    fit(lambda k: old[k].update(note=old[k].get("note", "")[:60]))
    fit(lambda k: old.pop(k))  # last resort: the oldest checks leave the document
    json.dump({"updatedAt": x.now, "items": old}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
