#!/usr/bin/env python3
"""Builds urls.json for the pilot from database dumps: the addresses that the Claude cloud could not read.

Sources (all optional except the registry):
  * source registry docs (srcreg) - probe of 29.09.2026: no answer, captcha, 403, robots, redirect errors
  * orgdir/catalog + orgdir/catalog-verify - catalog platforms whose check ended `blocked` or `failed`
  * sources docs with status covered/blocked - big ETPs read only through aggregators

Usage: python3 build_urls.py REGISTRY_DIR CATALOG.json CATALOG_VERIFY.json SOURCES_DIR OUT.json
"""
import glob
import json
import os
import sys
from urllib.parse import urlsplit


def load(p):
    d = json.load(open(p, encoding="utf-8"))
    return d.get("data", d) if isinstance(d, dict) and "data" in d and len(d) <= 4 else d


def norm(u):
    s = urlsplit(u.strip())
    return (s.netloc.lower().removeprefix("www."), s.path.rstrip("/") or "/")


def main(reg_dir, cat_p, ver_p, src_dir, out):
    rows, seen = [], {}

    def add(name, url, kind, cloud):
        if not url or not url.startswith("http"):
            return
        k = norm(url)
        if k in seen:
            seen[k]["kinds"].append(kind)
            return
        r = {"name": name, "url": url, "kinds": [kind], "cloud": cloud}
        seen[k] = r
        rows.append(r)

    for f in sorted(glob.glob(os.path.join(reg_dir, "misb-research*.json"))):
        r = load(f)
        p = (r.get("probe") or {}).get("url") or {}
        st = p.get("status")
        bad = (not st) or st != 200 or p.get("gate") or p.get("robots") == "disallowed"
        if bad:
            add(r["name"], r["url"], "registry", {"status": st, "gate": p.get("gate"), "robots": p.get("robots"), "note": (p.get("title") or "")[:80]})
    cat = {x["source_key"]: x for x in load(cat_p).get("rows", []) if not x.get("inactive")}
    for key, v in (load(ver_p).get("results") or {}).items():
        if v.get("status") in ("blocked", "failed") and key in cat:
            x = cat[key]
            add(x.get("source_name") or key, x.get("source_url"), "catalog", {"status": v.get("status"), "note": (v.get("note") or "")[:80]})
    for f in sorted(glob.glob(os.path.join(src_dir, "*.json"))):
        d = load(f)
        if d.get("status") in ("covered", "blocked") and d.get("url"):
            add(d.get("name") or d.get("key"), d["url"], "source", {"status": d["status"], "note": (d.get("note") or "")[:80]})
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(rows), "urls;", sum(1 for r in rows if "registry" in r["kinds"]), "from registry,",
          sum(1 for r in rows if "catalog" in r["kinds"]), "from catalog,", sum(1 for r in rows if "source" in r["kinds"]), "from sources")


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    main(*sys.argv[1:])
