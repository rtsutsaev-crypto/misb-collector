"""Проверка агента без сети: поддельный OpenRouter отдаёт заранее заданные вызовы инструментов, поддельный сайт — страницу.
Проверяется: инструменты работают с локальной базой, ключи не попадают в запросы к модели, конфликт версий, сокращение истории.
  python3 -m unittest tests.test_agent   (из папки standalone)"""
import json, os, subprocess, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = "SECRETKEY123456"


def script(site):
    calls = [
        ("ToolSearch", {"query": "select:ArtifactData"}),
        ("ArtifactData", {"action": "get", "collection": "progress", "doc_id": "current"}),
        ("Bash", {"command": 'echo "key=$GOSPLAN_KEY"; ls run_summary.py progress.py'}),
        ("Bash", {"command": "echo '{\"status\":\"running\",\"stage\":\"тест\"}' > prog.json"}),
        ("ArtifactData", {"action": "set", "collection": "progress", "doc_id": "current", "file_path": "prog.json"}),   # документа ещё нет — можно без if_version
        ("ArtifactData", {"action": "set", "collection": "progress", "doc_id": "current", "file_path": "prog.json", "if_version": 1}),
        ("ArtifactData", {"action": "set", "collection": "progress", "doc_id": "current", "data": {"status": "x"}, "if_version": 1}),  # конфликт
        ("WebFetch", {"url": site + "/page?apikey=$GOSPLAN_KEY", "prompt": "какие закупки?"}),
        ("ArtifactData", {"action": "batch", "writes": [{"op": "set", "collection": "leadsets", "doc_id": "t1", "data": {"leads": [1]}},
                                                       {"op": "update", "collection": "progress", "doc_id": "current", "data": {"status": "done"}, "if_version": 2}]}),
        ("ArtifactData", {"action": "list", "collection": "leadsets", "out_dir": "dl"}),
        ("ArtifactData", {"action": "query", "collection": "leadsets", "query": {"where": [["leads", "array-contains", 1]]}}),
    ]
    return calls


class Mock(BaseHTTPRequestHandler):
    calls, seen, site = [], [], ""

    def log_message(self, *a):
        pass

    def do_GET(self):   # поддельный сайт
        body = f"<html><body><h1>Закупки</h1><a href='/lot/1'>Обучение персонала ДПО</a> ключ в странице {SECRET}<p>" + "текст " * 30 + "</body></html>"
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.seen.append(req)
        if "tools" not in req:      # чтение страницы / поиск
            msg = {"role": "assistant", "content": "Лот: Обучение персонала ДПО </lot/1>"}
        else:
            n = sum(1 for m in req["messages"] if m["role"] == "assistant")
            if n < len(Mock.calls):
                name, args = Mock.calls[n]
                msg = {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            else:
                msg = {"role": "assistant", "content": "ИТОГ: тестовый сбор завершён"}
        out = {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.001, "prompt_tokens_details": {"cached_tokens": 900}}}
        b = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)


class AgentTest(unittest.TestCase):
    def test_run(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock); threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        Mock.calls = script(base)
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.sqlite3")
        env = dict(os.environ, OPENROUTER_API_KEY="or-test", OPENROUTER_MODEL="test/model", OPENROUTER_BASE=base + "/api/v1",
                   MISB_WORK=os.path.join(tmp, "work"), GOSPLAN_KEY=SECRET, MISB_SITE_URL="https://misb.example", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        env.pop("HTTPS_PROXY", None); env.pop("HTTP_PROXY", None); env.pop("https_proxy", None); env.pop("http_proxy", None)
        r = subprocess.run([sys.executable, "-m", "misb.agent", "--db", db, "--mode", "full", "--manual"], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
        srv.shutdown()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ИТОГ: тестовый сбор завершён", r.stdout)
        dump = json.dumps(Mock.seen, ensure_ascii=False)
        self.assertNotIn(SECRET, dump, "ключ ушёл в запрос к модели")
        self.assertNotIn("or-test", dump)
        tool_out = {m["tool_call_id"]: m["content"] for m in Mock.seen[-1]["messages"] if m["role"] == "tool"}
        self.assertIn("key=$GOSPLAN_KEY", tool_out["c2"])
        self.assertIn("version 1", tool_out["c4"])   # нового документа — без if_version можно
        self.assertIn("version 2", tool_out["c5"])
        self.assertIn("текущая версия 2", tool_out["c6"])
        self.assertIn("Обучение персонала", tool_out["c7"])
        self.assertIn("атомарно", tool_out["c8"])
        self.assertIn("1 документов", tool_out["c9"])
        self.assertIn('"t1"', tool_out["c10"])
        sys.path.insert(0, HERE)
        from misb.store import Store
        st = Store(db)
        self.assertEqual(st.get("progress/current")[0]["status"], "done")
        self.assertEqual(st.get("leadsets/t1")[0], {"leads": [1]})
        page_req = [q for q in Mock.seen if "tools" not in q][0]
        self.assertIn(f"Обучение персонала ДПО <{base}/lot/1>", page_req["messages"][1]["content"])
        rs = open([os.path.join(dp, "run_summary.py") for dp, _, fs in os.walk(os.path.join(tmp, "work")) if "run_summary.py" in fs][0]).read()
        self.assertIn('SITE = "https://misb.example"', rs)
        self.assertTrue(Mock.seen[0]["messages"][1]["content"].startswith("РУЧНОЙ ЗАПУСК"))


class HistoryTest(unittest.TestCase):
    def test_compact(self):
        sys.path.insert(0, HERE)
        from misb.agent import History
        h = History(keep=4, limit=1000)
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        for i in range(10):
            msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": str(i), "content": "x" * 2000})
        self.assertTrue(h.compact(msgs))
        self.assertLess(len(msgs[3]["content"]), 400)
        self.assertEqual(len(msgs[-1]["content"]), 2000)


if __name__ == "__main__":
    unittest.main()


class RelayTest(unittest.TestCase):
    def test_relay_get(self):
        sys.path.insert(0, HERE)
        from misb import relay
        site = ThreadingHTTPServer(("127.0.0.1", 0), Mock); threading.Thread(target=site.serve_forever, daemon=True).start()
        url, token, srv = relay.start()
        tmp = tempfile.mkdtemp()
        env = dict(os.environ, RELAY_URL=url, RELAY_TOKEN=token, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            env.pop(k, None)
        out = os.path.join(tmp, "r.json")
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "collector", "relay_get.py"), "--urls", f"http://127.0.0.1:{site.server_port}/p",
                            "--pause", "0", "--out", out], env=env, capture_output=True, text=True, timeout=60)
        bad = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "collector", "relay_get.py"), "--urls", f"http://127.0.0.1:{site.server_port}/p",
                              "--pause", "0", "--out", out + "2"], env=dict(env, RELAY_TOKEN="wrong"), capture_output=True, text=True, timeout=60)
        site.shutdown(); srv.shutdown()
        p = json.load(open(out))["pages"][0]
        self.assertEqual(p["status"], "ok", r.stdout + r.stderr)
        self.assertTrue(any("Обучение персонала" in t for t, _ in p["links"]))
        self.assertEqual(json.load(open(out + "2"))["pages"][0]["status"], "failed")
