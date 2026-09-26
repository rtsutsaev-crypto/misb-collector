#!/usr/bin/env python3
"""B2B-Center manual market sync: deterministic core (Python 3.10+, stdlib only).

The cloud routine «МИСБ: ручной сбор B2B-Center» reads pages with WebFetch and
calls this module for everything that must be exact: the robots.txt verdict,
the cutoff and watermark, page validation, resuming an incomplete listing,
which cards to open, merging into stored rows, lead export and the final
status. The watermark (start of the last fully successful scan) moves only when
the listing reached the cutoff and every required card was read.

Work directory layout (W):
  config.json      config/b2b-market            state.json   b2b/state
  params.json      {runId, startedAt, lookbackDays}
  dictionary.json  config/dictionary
  db/b2bmarket/*.json, db/leadsets/*.json   ArtifactData list dumps
  run.json         this run (managed here)    out/         files to write back

Commands (each prints JSON):
  robots W FILE HTTP_STATUS | plan W | page W OFFSET ROWS.json | cards W
  card W ID CARD.json | finish W [--error MSG | --blocked MSG]
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys
from urllib.parse import urlparse

MSK = dt.timezone(dt.timedelta(hours=3))
DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\D{1,3}(\d{1,2}):(\d{2}))?")
DEFAULTS = {
    "listUrl": "https://www.b2b-center.ru/market/?show=all",
    "pageParam": "from",
    "cardSample": "https://www.b2b-center.ru/market/example/tender-1/",
    "firstRunDays": 14,
    "overlapDays": 2,
    "maxPages": 15,
    "maxCards": 40,
    "rowsPerDoc": 250,
    "leadsPerDoc": 300,
}
ROBOT_AGENTS = ("*", "Claude-User", "ClaudeBot", "Claude-Web", "anthropic-ai")
# Prototype stems: annotate only, never used to drop a stored procedure.
STEMS = (
    "обучен", "переобучен", "повышен квалификац", "переподготов", "образовательн",
    "электронн курс", "онлайн-курс", "вебинар", "тренинг", "семинар", "мастер-класс",
    "стратегическ сесси", "конференц", "форум", "тимбилдинг", "корпоративн мероприяти",
    "оценк персонал", "развити персонал", "дистанционн обучен", "e-learning",
)
LEAD_SOURCE = "b2b-center:market"
LEADS_PREFIX = "b2b-market-"


class SyncError(Exception):
    """A condition under which the watermark must not move."""


# ---------- small helpers ----------

def load(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def parse_dt(value) -> str | None:
    """'dd.mm.yyyy hh:mm' (Moscow) or ISO -> ISO with offset; None if absent."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    m = DATE_RE.search(text)
    if m:
        d, mo, y, h, mi = m.groups()
        try:
            return dt.datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), tzinfo=MSK).isoformat()
        except ValueError:
            return None
    try:
        x = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (x if x.tzinfo else x.replace(tzinfo=MSK)).isoformat()


def ts(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso)


def msk_date(iso: str | None) -> str:
    return ts(iso).astimezone(MSK).date().isoformat() if iso else ""


def cfg(W: str) -> dict:
    return {**DEFAULTS, **(load(os.path.join(W, "config.json"), {}) or {})}


def words(text: str) -> list[str]:
    return re.findall(r"[0-9a-zа-я]+", str(text or "").lower().replace("ё", "е"))


def norm_title(text: str) -> str:
    return "".join(words(text))[:90]


# ---------- robots.txt (Google semantics: groups, * and $, longest match) ----------

def _robot_groups(text: str) -> list[dict]:
    groups, cur, agent_run = [], None, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if cur is None or not agent_run:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(val.lower())
            agent_run = True
            continue
        agent_run = False
        if key in ("allow", "disallow") and cur is not None and val:
            cur["rules"].append((key == "allow", val))
    return groups


def robots_allows(text: str, url: str, agent: str) -> bool:
    groups = _robot_groups(text)
    ua = agent.lower()
    chosen = [g for g in groups if any(a != "*" and a in ua for a in g["agents"])]
    chosen = chosen or [g for g in groups if "*" in g["agents"]]
    parts = urlparse(url)
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    best = None
    for allow, pattern in (r for g in chosen for r in g["rules"]):
        body = pattern[:-1] if pattern.endswith("$") else pattern
        rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
        if re.match(rx + ("$" if pattern.endswith("$") else ""), path):
            if best is None or len(pattern) > best[0] or (len(pattern) == best[0] and allow):
                best = (len(pattern), allow)
    return True if best is None else best[1]


def robots_verdict(text: str, http: int, urls: list[str]) -> dict:
    if http == 404:
        return {"allowed": True, "note": "robots.txt на сайте нет (HTTP 404)"}
    if http != 200:
        return {"allowed": False, "note": f"robots.txt не прочитан (HTTP {http}); без проверки сбор не начинается"}
    for url in urls:
        for agent in ROBOT_AGENTS:
            if not robots_allows(text, url, agent):
                path = urlparse(url).path + ("?" + urlparse(url).query if urlparse(url).query else "")
                return {"allowed": False, "note": f"robots.txt запрещает {path} для «{agent}»; нужен официальный API или разрешение площадки"}
    return {"allowed": True, "note": "robots.txt разрешает вкладку «Все» и карточки закупок"}


# ---------- relevance and duplicates ----------

def _stem(w: str) -> str:
    return w if len(w) <= 4 else w[: max(4, int(len(w) * 0.7))]


def _phrases(dic: dict, keys: tuple[str, ...]) -> list[tuple[str, list[str]]]:
    out = []
    for key in keys:
        for entry in dic.get(key, []) or []:
            for ph in str(entry).split(","):
                ws = words(ph)
                if ws and not all(w.isdigit() for w in ws):
                    out.append((ph.strip(), [_stem(w) for w in ws]))
    return out


class Matcher:
    """The institute's existing selection rule (config/dictionary): topics or
    formats in the title, minus explicit exclusions. Annotation only."""

    def __init__(self, dic: dict | None):
        dic = dic or {}
        self.pos = _phrases(dic, ("topics", "formats"))
        self.neg = _phrases(dic, ("exclude",))

    @staticmethod
    def _hit(title_words: list[str], stems: list[str]) -> bool:
        return all(any(t.startswith(s) for t in title_words) for s in stems)

    def terms(self, title: str) -> tuple[list[str], bool]:
        tw = words(title)
        hits = [ph for ph, st in self.pos if self._hit(tw, st)]
        hits += [s for s in STEMS if self._hit(tw, words(s))]
        excluded = any(self._hit(tw, st) for _, st in self.neg)
        return hits[:6], bool(hits) and not excluded


def existing_leads(W: str) -> tuple[set[str], dict[tuple[str, str], str]]:
    ids, pairs = set(), {}
    for path in glob.glob(os.path.join(W, "db", "leadsets", "*.json")):
        if os.path.basename(path).startswith(LEADS_PREFIX):
            continue
        for lead in (load(path, {}) or {}).get("leads", []) or []:
            lid = str(lead.get("id") or "").strip()
            if lid:
                ids.add(lid)
            key = (norm_title(lead.get("title", "")), str(lead.get("deadline") or "")[:10])
            if key[0] and key[1]:
                pairs.setdefault(key, lid or "?")
    return ids, pairs


def load_store(W: str) -> tuple[dict[str, dict], list[str]]:
    store, doc_ids = {}, []
    for path in sorted(glob.glob(os.path.join(W, "db", "b2bmarket", "*.json"))):
        doc_ids.append(os.path.splitext(os.path.basename(path))[0])
        for row in (load(path, {}) or {}).get("rows", []) or []:
            store[str(row["id"])] = row
    return store, doc_ids


# ---------- run lifecycle ----------

def _run_path(W: str) -> str:
    return os.path.join(W, "run.json")


def plan(W: str) -> dict:
    c = cfg(W)
    state = load(os.path.join(W, "state.json"), {}) or {}
    params = load(os.path.join(W, "params.json"), {}) or {}
    started = parse_dt(params.get("startedAt")) or dt.datetime.now(dt.timezone.utc).isoformat()
    now = ts(started)
    lb = params.get("lookbackDays")
    lb = int(lb) if lb not in (None, "", 0, "0") else None
    wm = state.get("watermark")
    if wm:
        cutoff = ts(wm) - dt.timedelta(days=int(c["overlapDays"]))
        if lb:
            cutoff = min(cutoff, now - dt.timedelta(days=lb))
    else:
        cutoff = now - dt.timedelta(days=lb or int(c["firstRunDays"]))
    resume = state.get("resume")
    if not (isinstance(resume, dict) and resume.get("topPublishedAt") and resume.get("oldestPublishedAt")
            and isinstance(resume.get("nextOffset"), int)):
        resume = None
    run = {
        "runId": params.get("runId") or "", "startedAt": started, "cutoff": cutoff.isoformat(),
        "watermarkBefore": wm, "lookbackDays": lb, "resume": resume,
        "resumeDone": False, "jumped": False, "jumpChecked": False, "gap": False, "preJumpOffset": None,
        "pages": [], "listed": {}, "sales": 0, "outOfWindow": 0, "top": None, "oldest": None,
        "listDone": False, "stop": "", "nextOffset": 0, "pageSize": None,
        "cards": {}, "cardErrors": {}, "cardErrStreak": 0, "cardPlan": [], "required": [],
    }
    save(_run_path(W), run)
    return {"cutoff": run["cutoff"], "watermarkBefore": wm, "resume": bool(resume),
            "listUrl": c["listUrl"], "pageParam": c["pageParam"], "nextOffset": 0,
            "maxPages": c["maxPages"], "maxCards": c["maxCards"]}


def _list_row(raw: dict) -> dict:
    tid = re.sub(r"\D", "", str(raw.get("id") or ""))
    if not tid:
        m = re.search(r"tender-(\d+)", str(raw.get("url") or ""))
        tid = m.group(1) if m else ""
    if not tid:
        raise SyncError(f"Строка без номера закупки: {str(raw)[:120]}")
    pub = parse_dt(raw.get("published") or raw.get("publishedAt"))
    dl = parse_dt(raw.get("deadline") or raw.get("deadlineAt"))
    if not pub or not dl:
        raise SyncError(f"Нет даты публикации или срока подачи у закупки {tid}: вёрстка изменилась?")
    title = " ".join(str(raw.get("title") or "").split())[:3000]
    return {
        "id": tid, "url": str(raw.get("url") or "").split("#", 1)[0],
        "title": title, "organizer": " ".join(str(raw.get("organizer") or "").split())[:1000],
        "publishedAt": pub, "deadlineAt": dl,
        "sale": bool(raw.get("sale")) or title.lower().startswith("объявление о продаже"),
    }


def page(W: str, offset: int, rows: list[dict]) -> dict:
    run = load(_run_path(W))
    c = cfg(W)
    if run["stop"]:
        raise SyncError("Листание уже завершено в этом запуске")
    if offset != run["nextOffset"]:
        raise SyncError(f"Ожидалась страница со смещением {run['nextOffset']}, получено {offset}")
    items = [_list_row(r) for r in rows]
    if not items:
        raise SyncError(f"На странице со смещением {offset} не найдено ни одной закупки: вёрстка изменилась или доступ закрыт")
    sig = [i["id"] for i in items]
    if any(p["sig"] == sig for p in run["pages"]):
        raise SyncError("Пагинация вернула уже прочитанную страницу")
    pubs = [ts(i["publishedAt"]) for i in items]
    if pubs != sorted(pubs, reverse=True):
        raise SyncError("Порядок по дате публикации нарушен: остановиться по дате безопасно нельзя")
    cutoff = ts(run["cutoff"])
    size = run["pageSize"] or len(items)
    run["pageSize"] = size
    note = ""

    if run["jumped"] and not run["jumpChecked"]:
        run["jumpChecked"] = True
        if not any(p >= ts(run["resume"]["oldestPublishedAt"]) for p in pubs):
            # The jump landed below the region covered earlier: go back and read sequentially.
            run["gap"], run["jumped"] = True, False
            run["nextOffset"] = run["preJumpOffset"]
            save(_run_path(W), run)
            return {"next": "continue", "nextOffset": run["nextOffset"],
                    "note": "стык с прошлым неполным запуском не подтвердился; читаю подряд"}

    if run["top"] is None:
        run["top"] = items[0]["publishedAt"]
    for item, pub in zip(items, pubs):
        if pub < cutoff:
            run["outOfWindow"] += 1
        elif item["sale"]:
            run["sales"] += 1
        else:
            run["listed"].setdefault(item["id"], item)
    run["pages"].append({"offset": offset, "count": len(items), "sig": sig,
                         "top": items[0]["publishedAt"], "bottom": items[-1]["publishedAt"]})
    run["oldest"] = items[-1]["publishedAt"]
    nxt = offset + len(items)

    R = run["resume"]
    if R and not run["resumeDone"] and not run["gap"]:
        idx = next((i for i, p in enumerate(pubs) if p <= ts(R["topPublishedAt"])), None)
        if idx is not None:
            run["resumeDone"] = True
            target = R["nextOffset"] + offset + idx - size
            if target > nxt:
                run["jumped"], run["preJumpOffset"], nxt = True, nxt, target
                note = f"прошлый неполный запуск уже прочитал этот участок; перехожу к смещению {target}"

    run["nextOffset"] = nxt
    if pubs[-1] < cutoff:
        run["listDone"], run["stop"] = True, "cutoff"
        result = {"next": "stop", "reason": "cutoff"}
    elif len(run["pages"]) >= int(c["maxPages"]):
        run["stop"] = "limit"
        result = {"next": "stop", "reason": "limit"}
    else:
        result = {"next": "continue", "nextOffset": nxt}
    if note:
        result["note"] = note
    result.update(pages=len(run["pages"]), listed=len(run["listed"]), sales=run["sales"],
                  oldest=run["oldest"], cutoff=run["cutoff"])
    save(_run_path(W), run)
    return result


def plan_cards(W: str) -> list[dict]:
    run = load(_run_path(W))
    c = cfg(W)
    store, _ = load_store(W)
    now = ts(run["startedAt"])
    required, optional = [], []
    for tid, item in run["listed"].items():
        s = store.get(tid)
        if s is None or s.get("cardPending") or s.get("cardError") or \
                s.get("title") != item["title"] or s.get("listDeadlineAt") != item["deadlineAt"]:
            required.append((0, tid, item["url"], "новая" if s is None else "изменилась или не дочитана"))
    for tid, s in store.items():
        if tid in run["listed"]:
            continue
        if s.get("cardPending") or s.get("cardError"):
            required.append((1, tid, s["url"], "не дочитана в прошлый раз"))
        elif s.get("deadlineAt") and ts(s["deadlineAt"]) >= now and s.get("status") != "archived":
            (required if s.get("relevant") else optional).append(
                (2 if s.get("relevant") else 3, tid, s["url"], "повторная проверка активной"))
    required.sort(key=lambda x: (x[0], (store.get(x[1]) or {}).get("checkedAt") or ""))
    optional.sort(key=lambda x: (store.get(x[1]) or {}).get("checkedAt") or "")
    chosen = (required + optional)[: int(c["maxCards"])]
    run["required"] = [x[1] for x in required]
    run["cardPlan"] = [x[1] for x in chosen]
    save(_run_path(W), run)
    return [{"id": tid, "url": url, "reason": why} for _, tid, url, why in chosen]


def card(W: str, tid: str, data: dict) -> dict:
    run = load(_run_path(W))
    tid = re.sub(r"\D", "", str(tid))
    err = str(data.get("error") or "").strip()
    if not err and (data.get("loginWall") or not str(data.get("organizer") or "").strip()):
        err = "в карточке нет организатора: нужен вход или вёрстка изменилась"
    if err:
        run["cardErrors"][tid] = err[:300]
        run["cardErrStreak"] += 1
    else:
        eis = re.sub(r"\D", "", str(data.get("eis") or ""))
        price = data.get("price")
        try:
            price = float(str(price).replace(" ", "").replace(",", ".")) if price not in (None, "") else None
        except ValueError:
            price = None
        run["cards"][tid] = {
            "organizer": " ".join(str(data.get("organizer") or "").split())[:1000],
            "organizerInn": re.sub(r"\D", "", str(data.get("organizerInn") or ""))[:12],
            "customer": " ".join(str(data.get("customer") or "").split())[:1000],
            "customerInn": re.sub(r"\D", "", str(data.get("customerInn") or ""))[:12],
            "publishedAt": parse_dt(data.get("published")),
            "deadlineAt": parse_dt(data.get("deadline")),
            "editedAt": parse_dt(data.get("edited")),
            "status": "archived" if data.get("archived") else str(data.get("status") or "").strip()[:80],
            "eis": eis if len(eis) in (11, 19) else "",
            "price": price if price and price > 0 else None,
            "checkedAt": run["startedAt"],
        }
        run["cardErrors"].pop(tid, None)
        run["cardErrStreak"] = 0
    save(_run_path(W), run)
    return {"ok": not err, "error": err, "errorStreak": run["cardErrStreak"],
            "done": len(run["cards"]), "planned": len(run["cardPlan"])}


def _fingerprint(row: dict) -> tuple:
    return (row.get("title"), row.get("deadlineAt"), row.get("editedAt"), row.get("status"),
            row.get("organizer"), row.get("customer"), row.get("eis"))


def _apply_card(row: dict, crd: dict) -> None:
    for key in ("organizer", "organizerInn", "customer", "customerInn", "editedAt", "status", "eis", "price"):
        if crd.get(key) not in (None, ""):
            row[key] = crd[key]
    if crd.get("deadlineAt"):
        row["deadlineAt"] = crd["deadlineAt"]
    row["checkedAt"] = crd["checkedAt"]
    row["cardPending"] = False
    row.pop("cardError", None)


def to_lead(row: dict) -> dict:
    customer = row.get("customer") or row.get("organizer") or ""
    inn = row.get("customerInn") if row.get("customer") else row.get("organizerInn")
    eis = row.get("eis") or ""
    law = "44-ФЗ" if re.fullmatch(r"0\d{18}", eis) else "223-ФЗ" if re.fullmatch(r"3\d{10}", eis) else ""
    lead = {"id": "b2b-" + row["id"], "title": row["title"], "customer": customer, "region": "",
            "price": row.get("price"), "deadline": msk_date(row.get("deadlineAt")), "law": law,
            "url": row["url"], "source": LEAD_SOURCE, "collectedAt": (row.get("firstSeenAt") or "")[:10],
            "platform": "B2B-Center", "flags": []}
    notes = []
    if inn:
        lead["customerInn"] = inn
    if eis:
        lead["eis"] = eis
    if row.get("customer") and row.get("organizer") and norm_title(row["customer"]) != norm_title(row["organizer"]):
        notes.append("Организатор: " + row["organizer"])
    if row.get("status") == "archived":
        notes.append("процедура в архиве")
    if row.get("cardPending"):
        notes.append("карточка ещё не прочитана")
    if notes:
        lead["note"] = "; ".join(notes)
    return lead


def finish(W: str, error: str | None = None, blocked: str | None = None) -> dict:
    c = cfg(W)
    state = load(os.path.join(W, "state.json"), {}) or {}
    run = load(_run_path(W))
    if run is None:  # stopped before plan (robots, lock): nothing listed
        params = load(os.path.join(W, "params.json"), {}) or {}
        run = {"runId": params.get("runId") or "", "startedAt": parse_dt(params.get("startedAt")) or "",
               "cutoff": "", "watermarkBefore": state.get("watermark"), "pages": [], "listed": {},
               "cards": {}, "cardErrors": {}, "required": [], "cardPlan": [], "listDone": False,
               "stop": "", "sales": 0, "outOfWindow": 0, "nextOffset": 0, "top": None, "oldest": None}
    now = run["startedAt"]
    store, old_docs = load_store(W)
    matcher = Matcher(load(os.path.join(W, "dictionary.json"), {}))
    lead_ids, lead_pairs = existing_leads(W)
    new = updated = 0

    for tid, item in run["listed"].items():
        prev = store.get(tid)
        row = dict(prev or {"id": tid, "firstSeenAt": now, "cardPending": True})
        before = _fingerprint(row) if prev else None
        if not prev or not row.get("checkedAt") or row.get("listDeadlineAt") != item["deadlineAt"]:
            row["deadlineAt"] = item["deadlineAt"]  # the list shows a moved deadline before the card is reread
        row.update(url=item["url"] or row.get("url", ""), title=item["title"],
                   publishedAt=item["publishedAt"], listDeadlineAt=item["deadlineAt"], lastSeenAt=now)
        if item["organizer"]:
            row["organizer"] = item["organizer"]
        if tid in run["cards"]:
            _apply_card(row, run["cards"][tid])
        if prev is None:
            new += 1
        elif _fingerprint(row) != before:
            updated += 1
        store[tid] = row
    for tid, crd in run["cards"].items():
        if tid in run["listed"] or tid not in store:
            continue
        before = _fingerprint(store[tid])
        _apply_card(store[tid], crd)
        updated += int(_fingerprint(store[tid]) != before)
    for tid, err in run["cardErrors"].items():
        if tid in store:
            store[tid]["cardError"] = err

    dupes = 0
    for row in store.values():
        row["terms"], row["relevant"] = matcher.terms(row.get("title", ""))
        eis = row.get("eis") or ""
        pair = (norm_title(row.get("title", "")), msk_date(row.get("deadlineAt")))
        row["dupOf"] = eis if eis and eis in lead_ids else (
            "название+срок: " + lead_pairs[pair] if pair in lead_pairs else "")
        dupes += int(bool(row["dupOf"]))

    reasons = []
    if blocked:
        status = "blocked"
        reasons.append(blocked)
    elif error:
        status = "error"
        reasons.append(error)
    else:
        if not run["listDone"]:
            reasons.append(f"листание не дошло до контрольной отметки: лимит {c['maxPages']} стр. за запуск")
        missing = [t for t in run["required"] if t not in run["cards"]]
        if missing:
            reasons.append(f"не прочитано обязательных карточек: {len(missing)}")
        status = "incomplete" if reasons else "done"

    new_state = {k: v for k, v in state.items() if k not in ("lastRun",)}
    if status == "done":
        new_state["watermark"], new_state["resume"] = now, None
    elif status == "incomplete" and not run["listDone"] and run["pages"]:
        new_state["resume"] = {"runStartedAt": now, "topPublishedAt": run["top"],
                               "oldestPublishedAt": run["oldest"], "nextOffset": run["nextOffset"],
                               "cutoff": run["cutoff"]}
    elif status == "incomplete":
        new_state["resume"] = None
    counters = {
        "pages": len(run["pages"]), "listed": len(run["listed"]), "sales": run["sales"],
        "outOfWindow": run["outOfWindow"], "cardsViewed": len(run["cards"]),
        "cardErrors": len(run["cardErrors"]), "cardsRequired": len(run["required"]),
        "new": new, "updated": updated, "stored": len(store),
        "relevant": sum(1 for r in store.values() if r["relevant"]), "dupes": dupes,
    }
    coverage = {"done": "full", "incomplete": "incomplete"}.get(status, "")
    new_state["lastRun"] = {"runId": run["runId"], "startedAt": now, "status": status,
                            "coverage": coverage, "counters": counters, "note": "; ".join(reasons)}

    rows = sorted(store.values(), key=lambda r: (r.get("publishedAt") or "", r["id"]), reverse=True)
    per = int(c["rowsPerDoc"])
    row_docs = {f"rows-{i // per + 1}": {"rows": rows[i:i + per], "updatedAt": now} for i in range(0, len(rows), per)}
    leads = [to_lead(r) for r in rows if r["relevant"] and not r["dupOf"]]
    lper = int(c["leadsPerDoc"])
    lead_docs = {f"{LEADS_PREFIX}{i // lper + 1}": {
        "source": "B2B-Center · вкладка «Все» (кнопка «Проверить B2B-Center»)", "kind": "b2b-market",
        "collectedAt": now[:10], "updatedAt": now, "leads": leads[i:i + lper]} for i in range(0, len(leads), lper)}
    old_leads = [os.path.splitext(os.path.basename(p))[0]
                 for p in glob.glob(os.path.join(W, "db", "leadsets", LEADS_PREFIX + "*.json"))]
    for name, doc in row_docs.items():
        save(os.path.join(W, "out", "b2bmarket", name + ".json"), doc)
    for name, doc in lead_docs.items():
        save(os.path.join(W, "out", "leadsets", name + ".json"), doc)
    save(os.path.join(W, "out", "state.json"), new_state)
    summary = {
        "status": status, "coverage": coverage, "note": "; ".join(reasons), "counters": counters,
        "cutoff": run["cutoff"], "watermarkBefore": run["watermarkBefore"],
        "watermarkAfter": new_state.get("watermark"), "resume": new_state.get("resume"),
        "leads": len(leads),
        "writes": {"b2bmarket": sorted(row_docs), "leadsets": sorted(lead_docs)},
        "deletes": {"b2bmarket": sorted(set(old_docs) - set(row_docs)),
                    "leadsets": sorted(set(old_leads) - set(lead_docs))},
    }
    save(os.path.join(W, "out", "summary.json"), summary)
    return summary


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, W = argv[0], argv[1]
    try:
        if cmd == "robots":
            with open(argv[2], encoding="utf-8") as f:
                text = f.read()
            c = cfg(W)
            out = robots_verdict(text, int(argv[3]), [c["listUrl"], c["cardSample"]])
            save(os.path.join(W, "robots.json"), out)
        elif cmd == "plan":
            out = plan(W)
        elif cmd == "page":
            out = page(W, int(argv[2]), load(argv[3], []))
        elif cmd == "cards":
            out = plan_cards(W)
        elif cmd == "card":
            out = card(W, argv[2], load(argv[3], {}))
        elif cmd == "finish":
            opts = dict(zip(argv[2::2], argv[3::2]))
            out = finish(W, error=opts.get("--error"), blocked=opts.get("--blocked"))
        else:
            raise SystemExit(f"unknown command {cmd}")
    except SyncError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
