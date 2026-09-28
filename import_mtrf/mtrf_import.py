#!/usr/bin/env python3
"""Tender cards from a «Монитор тендеров РФ» export into the monitor's leads.

The other monitor (monitor-tenderov-rf.hatchable.site) sits behind a Hatchable
login, so the cloud collector cannot read it; its owner downloads a JSON export
(format tender-monitor-consolidated-v1) and it is imported either here or on
the site's «Импорт» tab (same mapping, see site/monitor.html: mtrfLeads).

Usage:
  python3 mtrf_import.py EXPORT.json W [--date YYYY-MM-DD]

W/db/leadsets/*.json — the current leadsets (ArtifactData list with out_dir),
used to skip cards already in the base. Writes W/out/mtrf-<n>.json — leadset
documents of at most 200 leads — and W/out/mtrf-summary.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "b2b_center"))
from b2b_core import existing_leads, norm_title  # noqa: E402

FORMAT = "tender-monitor-consolidated-v1"
PER_DOC = 200
FOREIGN_RE = re.compile(r"казахстан|таджикистан|узбекистан|беларус|киргиз|кыргыз|qazaq|kazmunay|казмунайгаз|uzbekneftegaz|mitwork", re.I)
FOREIGN_HOSTS = (".kz", ".tj", ".uz", ".by", ".kg")
LAW = {"44-ФЗ": "44-ФЗ", "223-ФЗ": "223-ФЗ", "Коммерческая": "Коммерческий", "Коммерческий": "Коммерческий"}


def clean_url(url: str) -> str:
    url = str(url or "").strip()
    i = url.rfind("https://")
    return url[i:] if i > 0 else url  # energybase rows carry "https://energybase.ru/https://energybase.ru/tender/…"


def lead_id(t: dict, url: str) -> str:
    """Same ids as the monitor's own sources, so the same card is not shown twice."""
    ext = str(t.get("external_id") or "")
    m = re.search(r"tenderguru\.ru/tender/(\d+)", url)
    if m:
        return m.group(1)  # our TenderGuru leads use the TenderGuru number
    m = re.search(r"bicotender\.ru/tender(\d+)\.html", url) or re.search(r"rostender\.info/(?:.*/)?(\d+)-tender", url) \
        or re.search(r"rostender\.info/tender/(\d+)", url)
    if m:
        return m.group(1)
    num = re.sub(r"^[\w-]+:", "", ext)
    if re.fullmatch(r"0\d{18}|3\d{10}", num):
        return num  # EIS purchase number
    m = re.search(r"regNumber=(\d{11,19})", url)
    if m:
        return m.group(1)
    m = re.search(r"b2b-center\.ru/.*tender-(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"energybase\.ru/tender(?:-outdated)?/([\w-]{6,})", url)
    if m:
        return "energybase-" + m.group(1)
    return f"mtrf-{t.get('id')}"  # row id of the other monitor: unique and stable across its exports


def date10(v) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v or ""))
    return m.group(1) if m else ""


def skip_reason(t: dict, url: str) -> str:
    if str(t.get("category") or "").startswith("Вне профиля"):
        return "вне профиля"
    if t.get("canonical_tender_id"):
        return "дубль внутри выгрузки"
    if (t.get("currency") or "RUB") != "RUB" or str(t.get("region") or "").strip() in ("Казахстан", "Беларусь", "Таджикистан", "Узбекистан"):
        return "не РФ"
    if urlparse(url).netloc.endswith(FOREIGN_HOSTS) or FOREIGN_RE.search(str(t.get("source") or "")):
        return "не РФ"
    if not str(t.get("title") or "").strip():
        return "без названия"
    return ""


def to_lead(t: dict, today: str, exported: str) -> dict:
    url = clean_url(t.get("url"))
    cust = str(t.get("customer") or "").strip()
    inn = ""
    m = re.fullmatch(r"ИНН:?\s*(\d{10}|\d{12})", cust)
    if m:
        inn, cust = m.group(1), "ИНН " + m.group(1)
    reasons = str(t.get("fit_reasons") or "").strip()
    note = f"«Монитор тендеров РФ», выгрузка {exported}: {t.get('category') or 'без категории'}, fit там {t.get('fit_score')}"
    if reasons:
        note += "; " + reasons[:200]
    lead = {"id": lead_id(t, url), "title": " ".join(str(t["title"]).split()), "customer": cust,
            "region": str(t.get("region") or "") if t.get("region") not in ("Россия", None) else "",
            "price": t.get("amount") if isinstance(t.get("amount"), (int, float)) and t.get("amount") > 0 else None,
            "deadline": date10(t.get("deadline_at")), "law": LAW.get(t.get("law") or "", ""), "url": url,
            "source": "mtrf:" + str(t.get("source") or "прочее"),
            "collectedAt": date10(t.get("updated_at")) or today, "flags": [], "note": note}
    if inn:
        lead["customerInn"] = inn
    if not lead["law"]:
        lead["law"] = "44-ФЗ" if re.fullmatch(r"0\d{18}", lead["id"]) else "223-ФЗ" if re.fullmatch(r"3\d{10}", lead["id"]) else ""
    return lead


def run(export_path: str, W: str, today: str) -> dict:
    X = json.load(open(export_path, encoding="utf-8"))
    if X.get("format") != FORMAT:
        sys.exit(f"не тот формат: {X.get('format')!r}, нужен {FORMAT}")
    exported = date10(X.get("exported_at") or X.get("generated_at"))
    exported = ".".join(reversed(exported.split("-"))) if exported else "без даты"
    ids, pairs = existing_leads(W)
    urls = set()
    for p in os.listdir(os.path.join(W, "db", "leadsets")):
        d = json.load(open(os.path.join(W, "db", "leadsets", p), encoding="utf-8"))
        for l in (d.get("data", d) or {}).get("leads", []) or []:
            if l.get("url"):
                urls.add(str(l["url"]).split("#")[0].rstrip("/"))
    out, skipped = [], {}
    for t in X.get("datasets", {}).get("tenders", []) or []:
        url = clean_url(t.get("url"))
        why = skip_reason(t, url)
        if not why:
            l = to_lead(t, today, exported)
            if l["id"] in ids or url.split("#")[0].rstrip("/") in urls or (norm_title(l["title"]), l["deadline"]) in pairs:
                why = "уже в базе"
            else:
                ids.add(l["id"])
                if url:
                    urls.add(url.split("#")[0].rstrip("/"))
                if l["deadline"]:
                    pairs[(norm_title(l["title"]), l["deadline"])] = l["id"]
                out.append(l)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
    os.makedirs(os.path.join(W, "out"), exist_ok=True)
    docs = []
    for i in range(0, len(out), PER_DOC):
        doc = {"source": "Монитор тендеров РФ", "name": f"Монитор тендеров РФ · выгрузка {exported} · часть {i // PER_DOC + 1}",
               "importedAt": today, "collectedAt": today, "leads": out[i:i + PER_DOC]}
        path = os.path.join(W, "out", f"mtrf-{i // PER_DOC + 1}.json")
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        docs.append(path)
    open_n = sum(1 for l in out if l["deadline"] >= today)
    summary = {"exported": exported, "tenders": len(X["datasets"].get("tenders", [])), "new": len(out),
               "open": open_n, "skipped": skipped, "docs": docs}
    json.dump(summary, open(os.path.join(W, "out", "mtrf-summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return summary


if __name__ == "__main__":
    args = sys.argv[1:]
    day = args[args.index("--date") + 1] if "--date" in args else dt.date.today().isoformat()
    print(json.dumps(run(args[0], args[1], day), ensure_ascii=False, indent=1))
