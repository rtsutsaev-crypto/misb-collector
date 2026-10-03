"""Порция ротации источника без чтения всего плана (план большой: реестры, шаблоны, известные домены).

Запуск: python3 portion.py --plan sources-plan.json --key <key> --start <cursors.<key> из meta/rotation, нет — 0>
Печатает JSON: {key, name, type, batch, total, start, next, items: [{n, value, id}], verify, note, method} — items — batch
элементов списка (urls у pages, queries у search, requests у api) по кругу начиная с start; id — запись реестра (source_id),
если у источника есть registry. next — значение курсора после этой порции (его и пиши в meta/rotation).
Адаптивный режим: --checks <документ проверок источника (meta/registry-checks, cu-checks, v7-checks …)> --today ГГГГ-ММ-ДД: вместо круга берутся batch адресов
с наименьшим сроком очередной проверки (ещё не проверенные — первыми; ok — раз в 7 дней, empty_success — 45, failed — 30, два failed подряд — 90); cursor не нужен,
next = null. Адрес, срок которого не наступил, в порцию не попадает (порция может быть короче batch).
Без --start и для источника без batch печатает описание источника и весь список (небольшие источники).
"""
import argparse, datetime as dt, json

INTERVAL = {"ok": 7, "empty_success": 45, "failed": 30}   # дней до следующей проверки адреса реестра; 2 failed подряд — 90


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--plan", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--start", type=int)
    a.add_argument("--checks"); a.add_argument("--today", default=dt.date.today().isoformat())
    x = a.parse_args()
    plan = json.load(open(x.plan, encoding="utf-8"))
    s = next((s for s in plan["sources"] if s["key"] == x.key), None)
    if not s:
        raise SystemExit(f"нет источника {x.key}")
    field = next((f for f in ("urls", "queries", "requests", "customerInns") if s.get(f)), None)
    lst = s.get(field) or []
    reg = s.get("registry") or {}
    batch = s.get("batch")
    if batch and x.checks and lst and reg:
        j = json.load(open(x.checks, encoding="utf-8")); items = (j.get("data", j)).get("items", {})
        today = dt.date.fromisoformat(x.today)

        def due(i):
            sid = reg.get(lst[i]) if isinstance(lst[i], str) else None
            sid = sid[0] if isinstance(sid, list) and sid else sid
            it = items.get(sid) if sid else None
            if not it or not it.get("at"): return dt.date.min
            hist = it.get("history") or []
            fails = 0
            for h in reversed(hist):
                if h.get("status") != "failed": break
                fails += 1
            days = 90 if (it.get("status") == "failed" and fails >= 2) else INTERVAL.get(it.get("status"), 30)
            return dt.date.fromisoformat(str(it["at"])[:10]) + dt.timedelta(days=days)
        order = sorted(range(len(lst)), key=lambda i: (due(i), i))
        idx = [i for i in order if due(i) <= today][:batch]
        start, nxt = 0, None
    elif batch and x.start is not None and lst:
        start = x.start % len(lst)
        idx = [(start + i) % len(lst) for i in range(min(batch, len(lst)))]
        nxt = (start + len(idx)) % len(lst)
    else:
        start, idx, nxt = 0, list(range(len(lst))), None
    out = {k: s.get(k) for k in ("key", "name", "type", "source", "batch", "pause", "verify", "note", "method", "checksDoc", "country", "currency")}
    out.update({"field": field, "total": len(lst), "start": start, "next": nxt,
                "items": [{"n": i, "value": lst[i], "id": reg.get(lst[i]) if isinstance(lst[i], str) else None} for i in idx]})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
