"""Инструменты агента сбора — те же имена и смысл, что у инструментов Claude, на которые опирается collector/INSTRUCTIONS.md:
Bash (команды в рабочей папке), WebFetch (страница + вопрос к модели), WebSearch (поиск через OpenRouter), ArtifactData (база сайта —
здесь локальное хранилище), ToolSearch (заглушка: всё уже загружено).
"""
import html, json, os, re, subprocess, urllib.parse

from .store import Store, VersionConflict, split

MAX_OUT = int(os.environ.get("MISB_TOOL_OUT", "20000"))


def clip(s, n=MAX_OUT):
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    return s if len(s) <= n else s[: n * 2 // 3] + f"\n…[сокращено {len(s) - n} знаков]…\n" + s[-n // 3:]


SCHEMAS = [
    {"type": "function", "function": {"name": "Bash", "description": "Выполнить команду bash в рабочей папке сбора. Вывод stdout+stderr (сокращается при большом объёме). Долгие шаги запускай в фоне через nohup … & и жди wait_for.py отрезками.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "description": "мс, по умолчанию 120000, максимум 600000"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "WebFetch", "description": "Открыть страницу по адресу и ответить на вопрос prompt по её тексту (ссылки страницы сохранены в виде «текст <адрес>»). Капчу и вход не обходит.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["url", "prompt"]}}},
    {"type": "function", "function": {"name": "WebSearch", "description": "Поиск в интернете; возвращает краткий ответ и список адресов.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "ArtifactData", "description": "База сайта (документы «коллекция/документ» с версиями). action: get | list | query | set | update | delete | str_replace | batch. "
     "Поле url не нужно (его можно передать — игнорируется). get/list с out_dir сохраняют документы в <out_dir>/<collection>/<doc_id>.json. set/update берут data или file_path (JSON-файл). "
     "Большие или сложные документы — сохрани в JSON-файл и передай file_path (надёжнее, чем data). Запись в существующий документ — только с if_version (версия из последнего чтения); при чужом изменении запись отклоняется и называет текущую версию. batch: writes = [{op, collection, doc_id, data|file_path, if_version}] до 50, атомарно.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["get", "list", "query", "set", "update", "delete", "str_replace", "batch"]},
         "url": {"type": "string"}, "collection": {"type": "string"}, "doc_id": {"type": "string"}, "data": {"type": "object", "description": "документ (объект; можно JSON-строкой)"}, "file_path": {"type": "string"},
         "if_version": {"type": "integer"}, "out_dir": {"type": "string"}, "query": {"type": "object"}, "writes": {"type": "array", "items": {"type": "object"}},
         "limit": {"type": "integer"}, "cursor": {"type": "string"}, "field": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}, "replace_all": {"type": "boolean"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "ToolSearch", "description": "Совместимость с инструкцией: все инструменты уже доступны.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": []}}},
]


class Tools:
    def __init__(self, store: Store, workdir, env, llm):
        self.store, self.workdir, self.env, self.llm = store, workdir, env, llm
        self.secrets = {}          # имя переменной → значение; модели показываются только имена

    def redact(self, s):
        for k, v in self.secrets.items():
            if len(v) >= 6:
                s = s.replace(v, "$" + k)
        return s

    def call(self, name, args):
        f = getattr(self, "t_" + name, None)
        if not f:
            return f"Ошибка: инструмента {name} нет. Доступны: Bash, WebFetch, WebSearch, ArtifactData, ToolSearch."
        try:
            return clip(self.redact(f(**args)))
        except TypeError as e:
            return f"Ошибка аргументов {name}: {e}"
        except Exception as e:  # ошибка инструмента — сообщение агенту, сбор продолжается
            return self.redact(f"Ошибка {name}: {type(e).__name__}: {e}")

    # --- Bash
    def t_Bash(self, command, timeout=120000, **_):
        t = max(1, min(int(timeout or 120000), 600000)) / 1000
        try:
            r = subprocess.run(["bash", "-c", command], cwd=self.workdir, env=self.env, capture_output=True, text=True, errors="replace", timeout=t)
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
            return (out.strip() or "(пустой вывод)") + ("" if r.returncode == 0 else f"\n[код выхода {r.returncode}]")
        except subprocess.TimeoutExpired:
            return f"Команда не уложилась в {t:.0f} с и остановлена. Долгие шаги запускай в фоне (nohup … &) и жди wait_for.py."

    def t_ToolSearch(self, query="", **_):
        return "Все инструменты уже загружены: ArtifactData, WebFetch, WebSearch, Bash."

    # --- WebFetch / WebSearch
    def fetch_text(self, url):
        r = subprocess.run(["curl", "-sS", "-L", "-m", "60", "--max-filesize", "4000000", "-A", "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36",
                            "-H", "Accept-Language: ru,en;q=0.8", "-w", "\n__CODE__%{http_code} %{content_type}", url], capture_output=True, env=self.env, timeout=90)
        raw = r.stdout
        tail = raw.rfind(b"\n__CODE__")
        body, meta = (raw[:tail], raw[tail + 9:].decode(errors="ignore")) if tail >= 0 else (raw, "000 ")
        code, _, ctype = meta.partition(" ")
        if r.returncode != 0 and not body:
            raise RuntimeError((r.stderr.decode(errors="ignore") or "нет ответа").strip()[:300])
        m = re.search(r"charset=([\w-]+)", ctype) or re.search(rb'charset=["\']?([\w-]+)', body[:3000])
        enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
        try:
            text = body.decode(enc, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        if "html" in ctype or text.lstrip()[:200].lower().startswith(("<!doctype", "<html")) or "<body" in text[:5000].lower():
            text = html_to_text(text, url)
        return code, text

    def t_WebFetch(self, url, prompt="", **_):
        shown, real = url, url
        for k, v in self.secrets.items():
            real = real.replace("${" + k + "}", v).replace("$" + k, v)
        code, text = self.fetch_text(real)
        text = self.redact(text)
        url = shown
        if not code.startswith("2"):
            return f"Страница ответила HTTP {code}. Начало ответа: {text[:600]}"
        if len(text.strip()) < 50:
            return f"Страница почти пустая ({len(text)} знаков): вероятно, содержимое строится скриптом. Начало: {text[:300]}"
        return self.llm.ask_page(url, prompt, text)

    def t_WebSearch(self, query, **_):
        return self.llm.web_search(query)

    # --- ArtifactData
    def _file(self, p):
        p = p if os.path.isabs(p) else os.path.join(self.workdir, p)
        return json.load(open(p, encoding="utf-8"))

    def _save(self, out_dir, coll, doc, data):
        d = out_dir if os.path.isabs(out_dir) else os.path.join(self.workdir, out_dir)
        p = os.path.join(d, *coll.split("/"), doc + ".json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return p

    def _need_pin(self, path, if_version):
        _, cur = self.store.get(path)
        if cur is not None and if_version is None:
            raise ValueError(f"документ {path} уже существует (версия {cur}): запись без if_version отклонена — передай if_version прочитанной версии")

    def t_ArtifactData(self, action, collection=None, doc_id=None, data=None, file_path=None, if_version=None, out_dir=None, query=None, writes=None,
                       field=None, old_str=None, new_str=None, replace_all=False, url=None, limit=None, cursor=None, **_):
        st = self.store
        if action == "get":
            path = f"{collection}/{doc_id}"; d, v = st.get(path)
            if d is None:
                return f'Документа "{doc_id}" в коллекции "{collection}" нет.'
            if out_dir:
                p = self._save(out_dir, collection, doc_id, d)
                return f'Документ "{collection}"/"{doc_id}" сохранён: {p} ({os.path.getsize(p)} байт), version {v}.'
            return json.dumps({"id": doc_id, "version": v, "data": d}, ensure_ascii=False)
        if action in ("list", "query"):
            q = query or {}
            rows = st.list(collection)
            if action == "query":
                rows = [r for r in rows if all(match(r[1], w) for w in q.get("where", []))]
                ob = q.get("order_by")
                if ob:
                    rows.sort(key=lambda r: (get_field(r[1], ob["field"]) is None, get_field(r[1], ob["field"])), reverse=ob.get("direction") == "desc")
            # с out_dir — вся коллекция сразу (страниц нет, next_cursor не возвращается); без out_dir — страницами по limit
            start = int(cursor or q.get("cursor") or 0) if str(cursor or q.get("cursor") or "0").isdigit() else 0
            lim = len(rows) if out_dir else int(q.get("limit") or limit or 100)
            more = start + lim < len(rows)
            rows = rows[start:start + lim]
            if not rows:
                return f'В коллекции "{collection}" документов нет' + (" по условию." if action == "query" else ".")
            if out_dir:
                for i, d, v in rows:
                    self._save(out_dir, collection, i, d)
                base = out_dir if os.path.isabs(out_dir) else os.path.join(self.workdir, out_dir)
                return f'{len(rows)} документов коллекции "{collection}" сохранены в {os.path.join(base, *collection.split("/"))}/<doc_id>.json: ' + \
                    ", ".join(f"{i} (v{v})" for i, d, v in rows[:200]) + (" …" if len(rows) > 200 else "")
            res = {"docs": [{"id": i, "version": v, "data": d} for i, d, v in rows]}
            if more:
                res["next_cursor"] = str(start + lim)
            return json.dumps(res, ensure_ascii=False)
        if action in ("set", "update", "delete", "str_replace"):
            path = f"{collection}/{doc_id}"
            if action != "str_replace" and action != "delete" and data is None and not file_path:
                return "Ошибка: нужен data или file_path."
            body = self._file(file_path) if file_path else as_obj(data)
            self._need_pin(path, if_version)
            try:
                if action == "str_replace":
                    v = st.write("str_replace", path, if_version=if_version, field=field, old=old_str, new=new_str, replace_all=replace_all)
                else:
                    v = st.write(action, path, data=body, if_version=if_version)
            except VersionConflict as e:
                return f"Запись отклонена: {path} изменён после чтения, текущая версия {e.current}. Перечитай документ и повтори запись с if_version={e.current}."
            return f'Записано: {action} "{collection}"/"{doc_id}"' + (f" (version {v})." if v else ".")
        if action == "batch":
            ws = writes or []
            if not 1 <= len(ws) <= 50:
                return "Ошибка: в batch от 1 до 50 записей."
            plan = []
            for w in ws:
                path = f'{w["collection"]}/{w["doc_id"]}'
                self._need_pin(path, w.get("if_version"))
                body = self._file(w["file_path"]) if w.get("file_path") else as_obj(w.get("data"))
                plan.append((w["op"], path, {"data": body, "if_version": w.get("if_version")} if w["op"] != "delete" else {"if_version": w.get("if_version")}))
            try:
                vs = st.batch(plan)
            except VersionConflict as e:
                return f"Пакет не записан: {e.path} изменён, текущая версия {e.current}. Перечитай и повтори."
            return "Пакет записан атомарно:\n" + "\n".join(f"- {op} {p}" + (f" (version {v})" if v else "") for (op, p, _), v in zip(plan, vs))
        return f"Ошибка: неизвестное действие {action}."


def as_obj(x):
    """data может прийти JSON-строкой (некоторые провайдеры теряют поля у объектов без схемы)."""
    if isinstance(x, str):
        x = json.loads(x)
    if x is not None and not isinstance(x, dict):
        raise ValueError("data должно быть JSON-объектом")
    return x


def get_field(d, path):
    for k in str(path).split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def match(doc, w):
    f, op, val = w
    x = get_field(doc, f)
    op = {"==": "eq", "!=": "ne", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}.get(op, op)
    try:
        return {"eq": lambda: x == val, "ne": lambda: x != val, "in": lambda: x in val, "not-in": lambda: x not in val,
                "lt": lambda: x is not None and x < val, "lte": lambda: x is not None and x <= val, "gt": lambda: x is not None and x > val,
                "gte": lambda: x is not None and x >= val, "array-contains": lambda: isinstance(x, list) and val in x}[op]()
    except (TypeError, KeyError):
        return False


def html_to_text(s, base):
    s = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", s)
    def a(m):
        href, t = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        t = re.sub(r"\s+", " ", html.unescape(t)).strip()
        if not t or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            return " " + t + " "
        return f" {t} \x01{urllib.parse.urljoin(base, html.unescape(href))}\x02 "   # метки вместо <>: ниже снимаются теги
    s = re.sub(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', a, s)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6]|/table|/section|/article)[^>]*>", "\n", s)
    s = re.sub(r"(?i)<(td|th)[^>]*>", " | ", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\x01", "<").replace("\x02", ">")
    lines = [re.sub(r"[ \t ]+", " ", x).strip() for x in s.split("\n")]
    return "\n".join(x for x in lines if x)
