"""Перенос данных: снимок базы — папка <коллекция>/<документ>.json (так сохраняет выгрузка из базы артефакта).

  python3 -m misb.snapshot import <папка> --db data/misb.sqlite3   — загрузить снимок (документы с тем же путём заменяются)
  python3 -m misb.snapshot export <папка> --db data/misb.sqlite3   — выгрузить всю базу в тот же формат (резервная копия)
"""
import argparse, glob, json, os

from .store import Store


def do_import(src, store):
    n = 0; writes = []
    for f in sorted(glob.glob(os.path.join(src, "**", "*.json"), recursive=True)):
        rel = os.path.relpath(f, src)[:-5].replace(os.sep, "/")
        if "/" not in rel:
            continue
        data = json.load(open(f, encoding="utf-8"))
        writes.append(("set", rel, {"data": data}))
        if len(writes) >= 500:
            store.batch(writes); n += len(writes); writes = []
    if writes:
        store.batch(writes); n += len(writes)
    # в отдельной версии задание сбора запускает свой сервер: номер задания нужен странице только как признак «настроено»
    cfg, ver = store.get("config/collector")
    if cfg is not None:
        cfg["triggerId"] = "local"; cfg["server"] = "standalone"
        store.write("set", "config/collector", data=cfg)
    return n


def do_export(dst, store):
    n = 0
    for c in store.collections():
        for i, d, v in store.list(c):
            p = os.path.join(dst, *c.split("/"), i + ".json")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False); n += 1
    return n


def main():
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=["import", "export"]); a.add_argument("dir")
    a.add_argument("--db", default=os.environ.get("MISB_DB", "data/misb.sqlite3"))
    x = a.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(x.db)), exist_ok=True)
    s = Store(x.db)
    n = do_import(x.dir, s) if x.cmd == "import" else do_export(x.dir, s)
    print(f"{x.cmd}: документов {n}")


if __name__ == "__main__":
    main()
