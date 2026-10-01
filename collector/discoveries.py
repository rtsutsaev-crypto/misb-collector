"""Новые каналы, найденные в запуске (документ meta/discoveries): регистрируются до оценки полезности (политика R19).

Запуск: python3 discoveries.py [--doc старый.json] --plan sources-plan.json --key <key источника> --found found.json
  --now ISO --out disc.json
found.json — список {url, title, query_id, note}: адреса из выдачи поиска (или ссылки со страниц), чей домен не входит
в knownHosts источника (известные домены реестра). Запись по домену: первое и последнее обнаружение, сколько раз,
пример адреса, заголовок, какие шаблоны его нашли. Документ — не больше 230 КБ: при переполнении сначала
вытесняются домены, найденные один раз и давно.
"""
import argparse, json, os, re
from urllib.parse import urlparse

LIMIT = 230_000


def host(u):
    h = (urlparse(u or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--doc")
    a.add_argument("--plan", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--found", required=True)
    a.add_argument("--now", required=True)
    a.add_argument("--out", required=True)
    x = a.parse_args()
    plan = json.load(open(x.plan, encoding="utf-8"))
    src = next((s for s in plan.get("sources", []) if s.get("key") == x.key), {})
    known = {h.lower().removeprefix("www.") for h in src.get("knownHosts", [])}
    items = {}
    if x.doc and os.path.exists(x.doc):
        d = json.load(open(x.doc, encoding="utf-8"))
        d = d.get("data", d) if isinstance(d, dict) and "id" in d and "data" in d else d
        items = d.get("items", {})
    new = seen = skipped = 0
    for f in json.load(open(x.found, encoding="utf-8")):
        h = host(f.get("url"))
        if not h or not re.search(r"\.[a-zа-я]{2,}$", h):
            continue
        if h in known or any(h.endswith("." + k) for k in known):
            skipped += 1
            continue
        it = items.get(h)
        if it is None:
            it = items[h] = {"first": x.now[:10], "count": 0, "queries": [], "url": f.get("url"), "title": str(f.get("title") or "")[:140],
                             "state": "new", "via": x.key}
            new += 1
        else:
            seen += 1
        it["last"] = x.now[:10]
        it["count"] = int(it.get("count", 0)) + 1
        q = f.get("query_id")
        if q and q not in it["queries"]:
            it["queries"] = (it["queries"] + [q])[-8:]
    while len(json.dumps(items, ensure_ascii=False).encode()) > LIMIT and items:
        victim = min(items, key=lambda k: (items[k].get("count", 0) > 1, items[k].get("last", "")))
        items.pop(victim)
    json.dump({"updatedAt": x.now, "items": items}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"новых доменов {new}, повторно найдено {seen}, уже в реестре {skipped}; всего в документе {len(items)}")


if __name__ == "__main__":
    main()
