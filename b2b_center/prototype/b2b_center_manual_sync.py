#!/usr/bin/env python3
"""Manual B2B-Center public-market importer (Python 3.10+, standard library).

Run `python b2b_center_manual_sync.py serve` and press the local button, or
`python b2b_center_manual_sync.py scan`. No scheduler is installed or started.
The public HTML parser must be smoke-tested against the live page before use.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


BASE = "https://www.b2b-center.ru"
MARKET = BASE + "/market/"
ALL_MARKET = MARKET + "?show=all"  # Includes procedures closed between manual runs.
USER_AGENT = "InstituteTenderMonitor/1.0 (+manual public-market import)"
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})\b")
ID_RE = re.compile(r"/tender-(\d+)(?:/|\?|$)")
SPACES_RE = re.compile(r"\s+")
MOSCOW = dt.timezone(dt.timedelta(hours=3))
RUN_LOCK = threading.Lock()


def compact(value: str) -> str:
    return SPACES_RE.sub(" ", html.unescape(value)).strip()


def parse_date(value: str) -> str | None:
    match = DATE_RE.search(value)
    if not match:
        return None
    day, month, year, hour, minute = (int(n) for n in match.groups())
    try:
        return dt.datetime(year, month, day, hour, minute, tzinfo=MOSCOW).isoformat()
    except ValueError:
        return None


class MarketRows(HTMLParser):
    """Collect tender anchors and their enclosing table cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.row: dict | None = None
        self.cell: list[str] | None = None
        self.anchor: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self.row = {"cells": [], "links": []}
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "a" and self.row is not None:
            self.anchor = {"href": attributes.get("href") or "", "parts": []}

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)
        if self.anchor is not None:
            self.anchor["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor is not None and self.row is not None:
            self.row["links"].append(
                (self.anchor["href"], compact(" ".join(self.anchor["parts"])))
            )
            self.anchor = None
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row["cells"].append(compact(" ".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


class AllText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def parse_market_page(markup: str) -> list[dict]:
    parser = MarketRows()
    parser.feed(markup)
    result: list[dict] = []
    seen: set[int] = set()
    for row in parser.rows:
        cells = row["cells"]
        for href, anchor_title in row["links"]:
            url = urldefrag(urljoin(BASE, href)).url
            match = ID_RE.search(url)
            if not match or not url.startswith(BASE + "/"):
                continue
            tender_id = int(match.group(1))
            if tender_id in seen:
                continue
            dates = [parse_date(c) for c in cells]
            dates = [d for d in dates if d]
            if len(dates) < 2:
                raise ValueError(f"Missing publication/deadline columns for tender {tender_id}")
            seen.add(tender_id)
            result.append(
                {
                    "id": tender_id,
                    "url": url,
                    "title": anchor_title[:3000],
                    "is_sale": anchor_title.lower().startswith("объявление о продаже"),
                    "organizer": (cells[1] if len(cells) > 1 else "")[:1000],
                    "published_at": dates[-2],
                    "deadline_at": dates[-1],
                    "listing_text": compact(" ".join(cells))[:8000],
                }
            )
    return result


def extract_detail(markup: str) -> dict:
    parser = AllText()
    parser.feed(markup)
    text = compact(" ".join(parser.parts))
    if not re.search(r"\bОрганизатор\b", text):
        raise ValueError("Tender card lacks organizer; login wall or HTML change")
    edited = re.search(r"Дата последнего редактирования\s*:?\s*" + DATE_RE.pattern, text)
    deadline = re.search(
        r"(?:Дата окончания подачи заявок|Окончание при[её]ма заявок)\s*:?\s*"
        + DATE_RE.pattern, text
    )
    archived = "Процедура находится в архиве" in text
    return {
        "detail_text": text[:20000],
        "edited_at": parse_date(edited.group(0)) if edited else None,
        "deadline_at": parse_date(deadline.group(0)) if deadline else None,
        "is_archived": archived,
    }


def relevant_words(title: str) -> list[str]:
    """Only annotate; never discard a tender on keyword grounds."""
    stems = (
        "обучен", "переобучен", "повышен квалификац", "переподготов",
        "образовательн", "электронн курс", "онлайн-курс", "вебинар",
        "тренинг", "семинар", "мастер-класс", "стратегическ сесси",
        "конференц", "форум", "тимбилдинг", "корпоративн мероприяти",
        "оценк персонал", "развити персонал", "дистанционн обучен",
        "интерактивн обучен", "e-learning", "lms",
    )
    lower = title.lower()
    return [word for word in stems if word in lower]


def init_db(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS b2b_tenders (
          tender_id INTEGER PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
          organizer TEXT, published_at TEXT NOT NULL, deadline_at TEXT,
          listing_text TEXT, detail_text TEXT, edited_at TEXT,
          is_archived INTEGER NOT NULL DEFAULT 0,
          matched_terms TEXT NOT NULL, first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS b2b_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS b2b_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          completed_at TEXT, status TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
          fetched INTEGER NOT NULL DEFAULT 0, inserted INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );
        """
    )
    connection.commit()
    return connection


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def fetch(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            with urlopen(request, timeout=30) as response:
                if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
                    raise ValueError(f"Unexpected content type at {url}")
                body = response.read(3_000_001)
                if len(body) > 3_000_000:
                    raise ValueError(f"Page too large at {url}")
                charset = response.headers.get_content_charset() or "utf-8"
                return body.decode(charset, errors="replace")
        except HTTPError as exc:
            if exc.code in (401, 403, 404):
                raise
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last_error = exc
            delay = min(60, 3 * (2**attempt))
            if exc.code == 429:
                try:
                    delay = min(60, max(delay, int(exc.headers.get("Retry-After", "0"))))
                except ValueError:
                    pass
            time.sleep(delay)
        except (URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(3 * (2**attempt))
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def check_robots() -> None:
    parser = RobotFileParser()
    url = BASE + "/robots.txt"
    parser.set_url(url)
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as response:
            policy = response.read(1_000_000).decode("utf-8", errors="replace")
        parser.parse(policy.splitlines())
    except HTTPError as exc:
        if exc.code == 404:
            return  # The site did not publish a robots policy.
        raise RuntimeError(f"Cannot verify robots.txt (HTTP {exc.code})") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Cannot verify robots.txt; scan not started") from exc
    if not parser.can_fetch(USER_AGENT, MARKET):
        raise RuntimeError("robots.txt disallows the public market; seek API permission")


def save_tender(conn: sqlite3.Connection, item: dict, detail: dict) -> bool:
    exists = conn.execute("SELECT 1 FROM b2b_tenders WHERE tender_id=?", (item["id"],)).fetchone()
    terms = relevant_words(item["title"])
    conn.execute(
        """INSERT INTO b2b_tenders
        (tender_id,url,title,organizer,published_at,deadline_at,listing_text,
         detail_text,edited_at,is_archived,matched_terms,first_seen_at,last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tender_id) DO UPDATE SET
          url=excluded.url,title=excluded.title,organizer=excluded.organizer,
          published_at=excluded.published_at,deadline_at=excluded.deadline_at,
          listing_text=excluded.listing_text,detail_text=excluded.detail_text,
          edited_at=excluded.edited_at,is_archived=excluded.is_archived,
          matched_terms=excluded.matched_terms,
          last_seen_at=excluded.last_seen_at""",
        (
            item["id"], item["url"], item["title"], item["organizer"],
            item["published_at"], detail.get("deadline_at") or item["deadline_at"],
            item["listing_text"], detail["detail_text"], detail["edited_at"],
            int(detail.get("is_archived", False)), json.dumps(terms, ensure_ascii=False),
            now(), now(),
        ),
    )
    conn.commit()
    return exists is None


def run_scan(
    db_path: str, lookback_days: int = 14, page_limit: int = 5000,
    lock_preacquired: bool = False,
) -> dict:
    if not lock_preacquired and not RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("B2B-Center scan is already running")
    conn: sqlite3.Connection | None = None
    run_id: int | None = None
    try:
        conn = init_db(db_path)
        started = now()
        conn.execute("INSERT INTO b2b_runs(started_at,status) VALUES (?,?)", (started, "running"))
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        previous = conn.execute(
            "SELECT value FROM b2b_state WHERE key='last_successful_started_at'"
        ).fetchone()
        # A failed scan never advances this watermark. Overlap detects late edits.
        cutoff = dt.datetime.fromisoformat(previous[0] if previous else started)
        cutoff -= dt.timedelta(days=2 if previous else lookback_days)
        check_robots()
        offset = pages = fetched = inserted = 0
        page_signatures: set[tuple[int, ...]] = set()
        touched_ids: set[int] = set()
        while pages < page_limit:
            page = parse_market_page(fetch(f"{ALL_MARKET}&from={offset}"))
            if not page:
                raise RuntimeError(f"No tenders parsed at offset {offset}; HTML may have changed")
            signature = tuple(item["id"] for item in page)
            if signature in page_signatures:
                raise RuntimeError("Pagination repeated a page; watermark was not advanced")
            page_signatures.add(signature)
            dates = [dt.datetime.fromisoformat(item["published_at"]) for item in page]
            if dates != sorted(dates, reverse=True):
                raise RuntimeError("Publication order changed; cannot safely stop by date")
            for item in page:
                if dt.datetime.fromisoformat(item["published_at"]) < cutoff:
                    continue
                if item["is_sale"]:
                    continue
                if item["id"] in touched_ids:
                    continue
                detail = extract_detail(fetch(item["url"]))
                inserted += int(save_tender(conn, item, detail))
                fetched += 1
                touched_ids.add(item["id"])
                time.sleep(1)
            pages += 1
            conn.execute(
                "UPDATE b2b_runs SET pages=?,fetched=?,inserted=? WHERE id=?",
                (pages, fetched, inserted, run_id),
            )
            conn.commit()
            if min(dates) < cutoff:
                break
            offset += len(page)
            time.sleep(1)
        else:
            raise RuntimeError(f"Reached page limit {page_limit}; watermark was not advanced")

        # Recheck stored active cards even if they were published before cutoff.
        current_moscow = dt.datetime.now(MOSCOW).isoformat()
        for old in conn.execute(
            "SELECT * FROM b2b_tenders WHERE deadline_at >= ? ORDER BY deadline_at",
            (current_moscow,),
        ).fetchall():
            if old["tender_id"] in touched_ids:
                continue
            item = dict(old)
            item["id"] = old["tender_id"]
            detail = extract_detail(fetch(old["url"]))
            save_tender(conn, item, detail)
            fetched += 1
            time.sleep(1)
        conn.execute(
            "INSERT INTO b2b_state(key,value) VALUES('last_successful_started_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (started,),
        )
        conn.execute(
            "UPDATE b2b_runs SET completed_at=?,status='success',fetched=? WHERE id=?",
            (now(), fetched, run_id),
        )
        conn.commit()
        return {"status": "success", "pages": pages, "fetched": fetched, "inserted": inserted,
                "watermark": started}
    except Exception as exc:
        if conn is not None and run_id is not None:
            conn.execute(
                "UPDATE b2b_runs SET completed_at=?,status='failed',error=? WHERE id=?",
                (now(), str(exc)[:1000], run_id),
            )
            conn.commit()
        raise
    finally:
        if conn is not None:
            conn.close()
        RUN_LOCK.release()


def status(db_path: str) -> dict:
    conn = init_db(db_path)
    try:
        latest = conn.execute("SELECT * FROM b2b_runs ORDER BY id DESC LIMIT 1").fetchone()
        total = conn.execute("SELECT COUNT(*) FROM b2b_tenders").fetchone()[0]
        matches = conn.execute("SELECT COUNT(*) FROM b2b_tenders WHERE matched_terms!='[]'").fetchone()[0]
        return {"latest_run": dict(latest) if latest else None, "total": total,
                "title_matches": matches, "running": RUN_LOCK.locked()}
    finally:
        conn.close()


def serve(db_path: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/b2b-center/status":
                self.send_json(status(db_path))
            elif self.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/api/b2b-center/scan":
                self.send_error(404)
                return
            if not RUN_LOCK.acquire(blocking=False):
                self.send_json({"error": "Scan already running"}, 409)
                return
            # Local demonstration only. Production route must use the site's auth.
            thread = threading.Thread(target=self.background_scan, daemon=True)
            thread.start()
            self.send_json({"status": "started"}, 202)

        def background_scan(self) -> None:
            try:
                run_scan(db_path, lock_preacquired=True)
            except Exception as exc:
                print("B2B-Center manual scan failed:", exc, flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Local manual button: http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


PAGE = """<!doctype html><html lang=ru><meta charset=utf-8><title>B2B-Center</title>
<style>body{font:16px system-ui;max-width:650px;margin:50px auto;line-height:1.5}
button{font:inherit;padding:10px 18px;cursor:pointer}pre{white-space:pre-wrap}</style>
<h1>B2B-Center — ручной сбор</h1><button id=scan>Проверить новые закупки</button>
<pre id=status>Загрузка…</pre><script>
async function update(){const r=await fetch('/api/b2b-center/status');const j=await r.json();
document.getElementById('status').textContent=JSON.stringify(j,null,2);
document.getElementById('scan').disabled=j.running}
document.getElementById('scan').onclick=async()=>{const r=await fetch('/api/b2b-center/scan',{method:'POST'});
if(!r.ok)alert((await r.json()).error);update()};update();
setInterval(update,5000);</script></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("scan", "serve", "status"))
    parser.add_argument("--db", default="b2b_center.sqlite3")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.action == "serve":
        serve(args.db, args.port)
    elif args.action == "scan":
        print(json.dumps(run_scan(args.db), ensure_ascii=False))
    else:
        print(json.dumps(status(args.db), ensure_ascii=False))


if __name__ == "__main__":
    main()
