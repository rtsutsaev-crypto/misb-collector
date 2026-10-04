"""Документное хранилище на SQLite с той же моделью, что у базы артефакта: путь «коллекция/документ», JSON-данные, версия документа.

Версии нужны для if_version (защита от затирания чужой записи) и для дешёвого опроса изменений сайтом: у каждой коллекции
есть счётчик rev, который растёт при любой записи в неё.
"""
import json, sqlite3, threading, time

DELETE = {"__delete__": True}


class VersionConflict(Exception):
    def __init__(self, path, current):
        super().__init__(f"{path}: документ изменён, текущая версия {current}")
        self.path, self.current = path, current


def split(path):
    path = path.strip("/")
    if "/" not in path:
        raise ValueError(f"путь документа должен быть «коллекция/документ»: {path}")
    coll, _, doc = path.rpartition("/")
    return coll, doc


def merge(base, patch):
    """update: поля patch поверх base; {"__delete__": true} удаляет поле (на любой глубине, не внутри массивов)."""
    out = dict(base or {})
    for k, v in patch.items():
        if v == DELETE:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS docs (coll TEXT, id TEXT, data TEXT, version INTEGER, updated REAL, PRIMARY KEY (coll, id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS revs (coll TEXT PRIMARY KEY, rev INTEGER)")
        self.lock = threading.RLock()

    # чтение
    def get(self, path):
        coll, doc = split(path)
        r = self.db.execute("SELECT data, version FROM docs WHERE coll=? AND id=?", (coll, doc)).fetchone()
        return (json.loads(r[0]), r[1]) if r else (None, None)

    def list(self, coll):
        coll = coll.strip("/")
        return [(i, json.loads(d), v) for i, d, v in self.db.execute("SELECT id, data, version FROM docs WHERE coll=? ORDER BY id", (coll,))]

    def rev(self, coll):
        r = self.db.execute("SELECT rev FROM revs WHERE coll=?", (coll.strip("/"),)).fetchone()
        return r[0] if r else 0

    def collections(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT coll FROM docs ORDER BY coll")]

    # запись
    def _bump(self, coll):
        self.db.execute("INSERT INTO revs(coll, rev) VALUES(?, 1) ON CONFLICT(coll) DO UPDATE SET rev = rev + 1", (coll,))

    def _check(self, path, cur, if_version):
        if if_version is not None and cur is not None and int(if_version) != cur:
            raise VersionConflict(path, cur)

    def _put(self, coll, doc, data, ver):
        self.db.execute("INSERT INTO docs(coll, id, data, version, updated) VALUES(?,?,?,?,?) ON CONFLICT(coll, id) DO UPDATE SET data=excluded.data, "
                        "version=excluded.version, updated=excluded.updated", (coll, doc, json.dumps(data, ensure_ascii=False), ver, time.time()))
        self._bump(coll)

    def apply(self, op, path, data=None, if_version=None, field=None, old=None, new=None, replace_all=False):
        """Одна запись без транзакции снаружи; возвращает новую версию (для delete — None)."""
        coll, doc = split(path)
        cur_data, cur = self.get(path)
        self._check(path, cur, if_version)
        if op == "set":
            ver = (cur or 0) + 1; self._put(coll, doc, data, ver); return ver
        if op == "update":
            ver = (cur or 0) + 1; self._put(coll, doc, merge(cur_data, data), ver); return ver
        if op == "delete":
            self.db.execute("DELETE FROM docs WHERE coll=? AND id=?", (coll, doc)); self._bump(coll); return None
        if op == "str_replace":
            if cur_data is None or not isinstance(cur_data.get(field), str):
                raise ValueError(f"{path}: нет строкового поля {field}")
            s = cur_data[field]; n = s.count(old)
            if n == 0 or (n > 1 and not replace_all):
                raise ValueError(f"{path}.{field}: фрагмент {'не найден' if n == 0 else 'встречается %d раз' % n}")
            cur_data[field] = s.replace(old, new) if replace_all else s.replace(old, new, 1)
            ver = cur + 1; self._put(coll, doc, cur_data, ver); return ver
        raise ValueError(f"неизвестная операция {op}")

    def write(self, op, path, **kw):
        with self.lock:
            return self.apply(op, path, **kw)

    def batch(self, writes):
        """Атомарно: все записи или ни одной. writes — [(op, path, kwargs)]."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                out = [self.apply(op, path, **kw) for op, path, kw in writes]
                self.db.execute("COMMIT")
                return out
            except Exception:
                self.db.execute("ROLLBACK")
                raise
