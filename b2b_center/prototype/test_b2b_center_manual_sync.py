"""Meaningful offline contract tests; live market smoke-test remains required."""

import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

import b2b_center_manual_sync as sync


def row(tender_id: int, title: str, published: dt.datetime) -> str:
    posted = published.astimezone(sync.MOSCOW).strftime("%d.%m.%Y %H:%M")
    deadline = (published + dt.timedelta(days=3)).astimezone(sync.MOSCOW).strftime("%d.%m.%Y %H:%M")
    label = "Объявление о продаже" if title.startswith("Объявление о продаже") else "Запрос"
    return (
        f'<tr><td><small>Услуги по персоналу</small><br>'
        f'<a href="/app/market-next/example/tender-{tender_id}/#btid=2">'
        f'{label} № {tender_id}<div>{title}</div></a></td>'
        f"<td>АО Тест</td><td>{posted}</td><td>{deadline}</td></tr>"
    )


class ManualSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "test.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_pagination_and_failure_do_not_advance_watermark(self) -> None:
        current = dt.datetime.now(dt.timezone.utc)
        first = "<table>" + row(1003, "Обучение менеджеров", current) + row(
            1004, "Объявление о продаже оборудования", current - dt.timedelta(hours=1)
        ) + row(
            1002, "Оборудование", current - dt.timedelta(days=1)
        ) + "</table>"
        second = "<table>" + row(1001, "Семинар", current - dt.timedelta(days=20)) + "</table>"

        def good_fetch(url: str) -> str:
            if "show=all&from=0" in url:
                return first
            if "show=all&from=3" in url:
                return second
            return "<h1>Карточка закупки</h1><p>Организатор: АО Тест</p>"

        with patch.object(sync, "check_robots"), patch.object(sync, "fetch", side_effect=good_fetch), patch.object(sync.time, "sleep"):
            result = sync.run_scan(self.db)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(sync.status(self.db)["total"], 2)
        conn = sync.init_db(self.db)
        watermark = conn.execute("SELECT value FROM b2b_state WHERE key='last_successful_started_at'").fetchone()[0]
        conn.close()

        with patch.object(sync, "check_robots"), patch.object(sync, "fetch", side_effect=RuntimeError("site changed")):
            with self.assertRaisesRegex(RuntimeError, "site changed"):
                sync.run_scan(self.db)
        conn = sync.init_db(self.db)
        unchanged = conn.execute("SELECT value FROM b2b_state WHERE key='last_successful_started_at'").fetchone()[0]
        self.assertEqual(watermark, unchanged)
        self.assertEqual(sync.status(self.db)["latest_run"]["status"], "failed")
        conn.close()

    def test_detail_deadline_can_extend(self) -> None:
        first = row(4321, "Обучение", dt.datetime(2026, 9, 26, tzinfo=sync.MOSCOW))
        item = sync.parse_market_page("<table>" + first + "</table>")[0]
        self.assertNotIn("Услуги по персоналу", item["title"])
        self.assertNotIn("#", item["url"])
        detail = sync.extract_detail(
            "<p>Организатор: АО Тест</p>"
            "<p>Окончание приёма заявок: 15.10.2026 18:30, по МСК</p>"
            "<p>Дата последнего редактирования: 27.09.2026 09:00</p>"
        )
        conn = sync.init_db(self.db)
        sync.save_tender(conn, item, detail)
        stored = conn.execute("SELECT deadline_at FROM b2b_tenders WHERE tender_id=4321").fetchone()[0]
        self.assertEqual(stored, "2026-10-15T18:30:00+03:00")
        conn.close()


if __name__ == "__main__":
    unittest.main()
