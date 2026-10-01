"""Доступ к адресам реестра из облака: python3 probe_v3.py <sources.jsonl> probe.json [--skip probe_старый.json]
robots.txt соблюдается, вход и капча не обходятся. Для публичных каналов Telegram читается открытый веб-просмотр
t.me/s/<канал> (read_url); исходный адрес записи не меняется."""
import json, os, re, sys, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sources_probe"))
import probe_urls as pu


def read_url(u):
    """Адрес для чтения без входа: t.me/<канал> -> t.me/s/<канал> (публичный веб-просмотр)."""
    m = re.match(r"^https?://t\.me/(?!s/|joinchat|\+)([A-Za-z0-9_]{4,})/?$", u or "")
    return f"https://t.me/s/{m.group(1)}" if m else u


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    skip = json.load(open(sys.argv[sys.argv.index("--skip") + 1], encoding="utf-8")) if "--skip" in sys.argv else {}
    L = [json.loads(x) for x in open(src, encoding="utf-8") if x.strip()]
    urls, owner = [], {}
    for r in L:
        if r["source_id"] in skip:
            continue
        for kind, u in (("url", read_url(r.get("source_url"))), ("evidence", r.get("evidence_url"))):
            if u and u.startswith("http"):
                owner.setdefault(u, []).append((r["source_id"], kind))
                if len(owner[u]) == 1:
                    urls.append(u)
    res = pu.run(urls, 1.0, 8)
    today = dt.date.today().isoformat()
    by = dict(skip)
    for x in res:
        for sid, kind in owner[x["url"]]:
            by.setdefault(sid, {"checkedOn": today, "from": "облако сеанса Claude Code, без входа, robots.txt соблюдён"})[kind] = (
                {k: x.get(k) for k in ("url", "status", "final", "robots", "gate", "size", "title")}
                | {"procWords": x.get("proc"), "dates": x.get("dates"), "trainWords": x.get("train")})
    json.dump(by, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(urls), "адресов проверено;", len(by), "записей с результатом")
