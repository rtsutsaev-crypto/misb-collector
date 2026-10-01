"""Порция ротации источника без чтения всего плана (план большой: реестры, шаблоны, известные домены).

Запуск: python3 portion.py --plan sources-plan.json --key <key> --start <cursors.<key> из meta/rotation, нет — 0>
Печатает JSON: {key, name, type, batch, total, start, next, items: [{n, value, id}], verify, note, method} — items — batch
элементов списка (urls у pages, queries у search, requests у api) по кругу начиная с start; id — запись реестра (source_id),
если у источника есть registry. next — значение курсора после этой порции (его и пиши в meta/rotation).
Без --start и для источника без batch печатает описание источника и весь список (небольшие источники).
"""
import argparse, json


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--plan", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--start", type=int)
    x = a.parse_args()
    plan = json.load(open(x.plan, encoding="utf-8"))
    s = next((s for s in plan["sources"] if s["key"] == x.key), None)
    if not s:
        raise SystemExit(f"нет источника {x.key}")
    field = next((f for f in ("urls", "queries", "requests") if s.get(f)), None)
    lst = s.get(field) or []
    reg = s.get("registry") or {}
    batch = s.get("batch")
    if batch and x.start is not None and lst:
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
