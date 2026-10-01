"""Результат проверки адресов реестра в обычном запуске (документ meta/registry-checks): что реально прочитано.

Запуск после источника с полем registry: python3 registry_checks.py [--doc старый.json] --plan sources-plan.json
  --key <key источника> --results reg_results.json --now ISO --out rc.json
reg_results.json — список {url, status, rows, matched, fresh, note} по каждой прочитанной странице:
  status "ok" — страница прочитана, есть строки по теме; "empty_success" — прочитана, строк по теме нет (это не ошибка
  и не повод выключать источник); "failed" — не прочитана: 403, 429, капча, вход, таймаут, пустая страница
  (это задача доступа, а не «заказов нет»).
Адрес -> source_id реестра берётся из поля registry источника в плане ({url: "MISB-…"}). Для каждого source_id
хранится последний результат и счётчики; история — последние 5 проверок.
"""
import argparse, json, os

STATUSES = ("ok", "empty_success", "failed")


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
        sid = reg.get(r.get("url"))
        if not sid:
            unknown.append(r.get("url"))
            continue
        st = r.get("status") if r.get("status") in STATUSES else "failed"
        it = old.get(sid) or {"checks": 0, "okCount": 0, "emptyCount": 0, "failCount": 0, "history": []}
        it["checks"] = int(it.get("checks", 0)) + 1
        it[{"ok": "okCount", "empty_success": "emptyCount", "failed": "failCount"}[st]] = int(it.get(
            {"ok": "okCount", "empty_success": "emptyCount", "failed": "failCount"}[st], 0)) + 1
        last = {"at": x.now[:10], "status": st, "rows": r.get("rows"), "matched": r.get("matched"), "fresh": r.get("fresh"),
                "note": str(r.get("note") or "")[:200], "url": r.get("url"), "via": x.key}
        it.update(last)
        it["history"] = ([{k: last[k] for k in ("at", "status", "rows", "matched", "fresh")}] + it.get("history", []))[:5]
        old[sid] = it
        done += 1
    json.dump({"updatedAt": x.now, "items": old}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    by = {s: sum(1 for r in res if r.get("status") == s and reg.get(r.get("url"))) for s in STATUSES}
    print(f"записано {done} проверок ({', '.join(f'{k} {v}' for k, v in by.items())}); всего в документе {len(old)}"
          + (f"; адреса без source_id: {len(unknown)}" if unknown else ""))


if __name__ == "__main__":
    main()
