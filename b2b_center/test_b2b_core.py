"""Offline contract tests for b2b_core; the live smoke test is the first button press."""

import datetime as dt
import glob
import json
import os
import shutil
import tempfile
import unittest

import b2b_core as core

MSK = core.MSK
T0 = dt.datetime(2026, 9, 26, 12, 0, tzinfo=MSK)
DICTIONARY = {
    "topics": ["управление проектами, проектный офис", "охрана труда, первая помощь"],
    "formats": ["тренинг, мастер-класс", "семинар, вебинар", "повышение квалификации, ДПО"],
    "exclude": ["водители, БДД для водителей", "спорт, тренировки"],
}


def fmt(x: dt.datetime) -> str:
    return x.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


class Market:
    """A fake public «Все» tab: newest first, fixed page size, cards by id."""

    def __init__(self, size=3):
        self.size = size
        self.items = []
        self.cards = {}

    def add(self, tid, title, published, days=10, sale=False, organizer="АО Тест", eis=""):
        deadline = published + dt.timedelta(days=days)
        self.items.append({"id": str(tid), "title": title, "published": published, "deadline": deadline,
                           "sale": sale, "organizer": organizer})
        self.cards[str(tid)] = {"organizer": organizer, "organizerInn": "7700000000",
                                "customer": organizer, "customerInn": "7700000000",
                                "deadline": fmt(deadline), "edited": fmt(published), "eis": eis}

    def page(self, offset):
        ordered = sorted(self.items, key=lambda i: i["published"], reverse=True)
        return [{"id": i["id"], "url": f"https://www.b2b-center.ru/market/x/tender-{i['id']}/",
                 "title": ("Объявление о продаже " if i["sale"] else "") + i["title"],
                 "organizer": i["organizer"], "published": fmt(i["published"]),
                 "deadline": fmt(i["deadline"]), "sale": i["sale"]}
                for i in ordered[offset:offset + self.size]]


class Site:
    """Plays the cloud routine: runs the core commands and writes results back."""

    def __init__(self, test, config=None):
        self.W0 = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.W0)
        self.db = os.path.join(self.W0, "db")
        os.makedirs(os.path.join(self.db, "b2bmarket"))
        os.makedirs(os.path.join(self.db, "leadsets"))
        self.state = {}
        self.config = {"maxPages": 50, "maxCards": 100, **(config or {})}
        self.n = 0

    def run(self, market, now, lookback=None, fail_at_offset=None, card_fail=()):
        self.n += 1
        W = os.path.join(self.W0, f"run{self.n}")
        shutil.copytree(self.db, os.path.join(W, "db"))
        core.save(os.path.join(W, "config.json"), self.config)
        core.save(os.path.join(W, "state.json"), self.state)
        core.save(os.path.join(W, "dictionary.json"), DICTIONARY)
        core.save(os.path.join(W, "params.json"), {"runId": f"b2b-{self.n}", "startedAt": now.isoformat(),
                                                   "lookbackDays": lookback})
        core.plan(W)
        error = None
        offset = 0
        while True:
            if fail_at_offset is not None and offset == fail_at_offset:
                error = "WebFetch: 503 Service Unavailable"
                break
            try:
                res = core.page(W, offset, market.page(offset))
            except core.SyncError as exc:
                error = str(exc)
                break
            if res["next"] == "stop":
                break
            offset = res["nextOffset"]
        if error is None:
            for task in core.plan_cards(W):
                if task["id"] in card_fail:
                    core.card(W, task["id"], {"error": "timeout"})
                else:
                    core.card(W, task["id"], market.cards[task["id"]])
        summary = core.finish(W, error=error)
        for kind in ("b2bmarket", "leadsets"):
            for name in summary["deletes"][kind]:
                os.remove(os.path.join(self.db, kind, name + ".json"))
            for name in summary["writes"][kind]:
                shutil.copy(os.path.join(W, "out", kind, name + ".json"), os.path.join(self.db, kind))
        self.state = core.load(os.path.join(W, "out", "state.json"))
        return summary

    def rows(self):
        out = []
        for path in glob.glob(os.path.join(self.db, "b2bmarket", "*.json")):
            out += core.load(path)["rows"]
        return out

    def leads(self):
        out = []
        for path in glob.glob(os.path.join(self.db, "leadsets", core.LEADS_PREFIX + "*.json")):
            out += core.load(path)["leads"]
        return out


class RobotsTests(unittest.TestCase):
    urls = ["https://www.b2b-center.ru/market/?show=all", "https://www.b2b-center.ru/market/x/tender-1/"]

    def test_disallowed_market_stops(self):
        v = core.robots_verdict("User-agent: *\nDisallow: /market/\n", 200, self.urls)
        self.assertFalse(v["allowed"])
        self.assertIn("/market/", v["note"])

    def test_wildcard_and_specific_agent(self):
        self.assertFalse(core.robots_verdict("User-agent: *\nDisallow: /*show=all\n", 200, self.urls)["allowed"])
        self.assertFalse(core.robots_verdict("User-agent: *\nAllow: /\n\nUser-agent: ClaudeBot\nDisallow: /\n",
                                             200, self.urls)["allowed"])
        self.assertTrue(core.robots_verdict("User-agent: *\nDisallow: /market/$\nDisallow: /admin/\n",
                                            200, self.urls)["allowed"])

    def test_longest_match_and_missing_file(self):
        text = "User-agent: *\nDisallow: /market/\nAllow: /market/?show=all\nAllow: /market/x/\n"
        self.assertTrue(core.robots_verdict(text, 200, self.urls)["allowed"])
        self.assertTrue(core.robots_verdict("", 404, self.urls)["allowed"])
        self.assertFalse(core.robots_verdict("", 403, self.urls)["allowed"])


class SyncTests(unittest.TestCase):
    def test_failure_midway_keeps_watermark_and_next_run_catches_up(self):
        m = Market(size=3)
        for i in range(9):  # three pages inside the first 14-day window
            m.add(100 + i, f"Семинар по закупкам {i}", T0 - dt.timedelta(hours=3 * i + 1))
        m.add(90, "Старая закупка", T0 - dt.timedelta(days=30))
        site = Site(self)
        s1 = site.run(m, T0, fail_at_offset=3)
        self.assertEqual(s1["status"], "error")
        self.assertIsNone(site.state.get("watermark"))
        self.assertEqual(len(site.rows()), 3)  # the first page is kept, not lost
        m.add(200, "Тренинг переговоров", T0 + dt.timedelta(hours=1))
        s2 = site.run(m, T0 + dt.timedelta(hours=2))
        self.assertEqual(s2["status"], "done")
        self.assertEqual(site.state["watermark"], (T0 + dt.timedelta(hours=2)).isoformat())
        ids = sorted(r["id"] for r in site.rows())
        self.assertEqual(ids, sorted(str(100 + i) for i in range(9)) + ["200"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_next_run_uses_two_day_overlap_not_fourteen(self):
        m = Market(size=5)
        m.add(1, "Семинар", T0 - dt.timedelta(days=1))
        m.add(2, "Архив", T0 - dt.timedelta(days=60))
        site = Site(self)
        self.assertEqual(site.run(m, T0)["status"], "done")
        s = site.run(m, T0 + dt.timedelta(days=10))
        self.assertEqual(s["cutoff"], (T0 - dt.timedelta(days=2)).isoformat())
        s = site.run(m, T0 + dt.timedelta(days=11), lookback=30)
        self.assertEqual(s["cutoff"], (T0 + dt.timedelta(days=11) - dt.timedelta(days=30)).isoformat())

    def test_page_limit_is_incomplete_and_resume_catches_up(self):
        m = Market(size=3)
        for i in range(21):  # 7 pages inside the window
            m.add(1000 + i, f"Закупка {i}", T0 - dt.timedelta(hours=12 * i + 1))
        m.add(999, "Старая", T0 - dt.timedelta(days=20))
        site = Site(self, {"maxPages": 3})
        statuses, now, extra = [], T0, 0
        while True:
            s = site.run(m, now)
            statuses.append(s["status"])
            if s["status"] == "done" or len(statuses) > 10:
                break
            self.assertIsNone(site.state.get("watermark"))
            now += dt.timedelta(hours=1)
            extra += 1
            m.add(5000 + extra, f"Новая {extra}", now - dt.timedelta(minutes=30))
        self.assertEqual(statuses[-1], "done")
        self.assertIn("incomplete", statuses)
        self.assertEqual(site.state["watermark"], now.isoformat())
        ids = [r["id"] for r in site.rows()]
        expected = {str(1000 + i) for i in range(21)} | {str(5000 + k) for k in range(1, extra + 1)}
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(set(ids)))

    def test_deadline_extension_updates_row_and_lead(self):
        m = Market(size=5)
        m.add(77, "Тренинг для руководителей", T0 - dt.timedelta(hours=5), days=5)
        site = Site(self)
        site.run(m, T0)
        new_deadline = T0 + dt.timedelta(days=20)
        m.items[0]["deadline"] = new_deadline
        m.cards["77"]["deadline"] = fmt(new_deadline)
        m.cards["77"]["edited"] = fmt(T0 + dt.timedelta(hours=1))
        s = site.run(m, T0 + dt.timedelta(hours=2))
        self.assertEqual((s["counters"]["new"], s["counters"]["updated"]), (0, 1))
        rows = site.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["deadlineAt"], new_deadline.isoformat())
        self.assertEqual([l["deadline"] for l in site.leads()], [new_deadline.date().isoformat()])

    def test_sales_out_of_window_and_relevance(self):
        m = Market(size=10)
        m.add(1, "Оборудование насосное", T0 - dt.timedelta(hours=1), sale=True)
        m.add(2, "Поставка труб", T0 - dt.timedelta(hours=2))
        m.add(3, "Обучение водителей БДД", T0 - dt.timedelta(hours=3))
        m.add(4, "Повышение квалификации специалистов по закупкам", T0 - dt.timedelta(hours=4))
        m.add(5, "Семинар", T0 - dt.timedelta(days=40))
        site = Site(self)
        s = site.run(m, T0)
        self.assertEqual(s["counters"]["sales"], 1)
        rows = {r["id"]: r for r in site.rows()}
        self.assertEqual(set(rows), {"2", "3", "4"})  # all kept, whatever the topic
        self.assertFalse(rows["2"]["relevant"])
        self.assertFalse(rows["3"]["relevant"])  # explicitly excluded, still stored
        self.assertTrue(rows["4"]["relevant"])
        self.assertEqual([l["id"] for l in site.leads()], ["b2b-4"])

    def test_same_tender_elsewhere_is_kept_but_not_a_second_lead(self):
        m = Market(size=10)
        m.add(10, "Семинар по охране труда", T0 - dt.timedelta(hours=1), eis="32612345678")
        m.add(11, "Вебинар по кадровому делу", T0 - dt.timedelta(hours=2))
        site = Site(self)
        other = [{"id": "32612345678", "title": "Семинар по охране труда", "deadline": "2026-10-06"},
                 {"id": "x1", "title": "Вебинар по кадровому делу",
                  "deadline": (T0 - dt.timedelta(hours=2) + dt.timedelta(days=10)).date().isoformat()}]
        core.save(os.path.join(site.db, "leadsets", "run-1.json"), {"leads": other})
        s = site.run(m, T0)
        self.assertEqual(s["counters"]["dupes"], 2)
        self.assertEqual(len(site.rows()), 2)
        self.assertEqual(site.leads(), [])

    def test_card_budget_and_card_errors_keep_watermark(self):
        m = Market(size=10)
        for i in range(5):
            m.add(300 + i, f"Тренинг {i}", T0 - dt.timedelta(hours=i + 1))
        m.add(299, "Архив", T0 - dt.timedelta(days=60))
        site = Site(self, {"maxCards": 2})
        s1 = site.run(m, T0)
        self.assertEqual(s1["status"], "incomplete")
        self.assertIsNone(site.state.get("watermark"))
        s2 = site.run(m, T0 + dt.timedelta(hours=1), card_fail=("302",))
        self.assertEqual(s2["status"], "incomplete")
        site.config["maxCards"] = 10
        s3 = site.run(m, T0 + dt.timedelta(hours=2))
        self.assertEqual(s3["status"], "done")
        self.assertTrue(all(not r.get("cardPending") and not r.get("cardError") for r in site.rows()))

    def test_bad_pages_raise(self):
        site = Site(self)
        W = os.path.join(site.W0, "w")
        os.makedirs(W)
        core.save(os.path.join(W, "params.json"), {"startedAt": T0.isoformat()})
        core.plan(W)
        a = {"id": "1", "published": fmt(T0 - dt.timedelta(hours=2)), "deadline": fmt(T0)}
        b = {"id": "2", "published": fmt(T0 - dt.timedelta(hours=1)), "deadline": fmt(T0)}
        with self.assertRaisesRegex(core.SyncError, "Порядок"):
            core.page(W, 0, [a, b])
        with self.assertRaisesRegex(core.SyncError, "не найдено"):
            core.page(W, 0, [])
        core.page(W, 0, [b, a])
        with self.assertRaisesRegex(core.SyncError, "уже прочитанную"):
            core.page(W, 2, [b, a])
        with self.assertRaisesRegex(core.SyncError, "Нет даты"):
            core.page(W, 2, [{"id": "3", "published": "вчера"}])

    def test_login_wall_card_is_an_error(self):
        site = Site(self)
        W = os.path.join(site.W0, "w")
        os.makedirs(W)
        core.save(os.path.join(W, "params.json"), {"startedAt": T0.isoformat()})
        core.plan(W)
        self.assertFalse(core.card(W, "5", {"organizer": "", "loginWall": True})["ok"])


if __name__ == "__main__":
    unittest.main()
