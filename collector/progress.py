"""Ведёт локальный файл прогресса сбора; после каждой команды его записывают в базу сайта:
ArtifactData set, collection "progress", doc_id "current", file_path <файл>, if_version — версия из прошлой записи.

  python3 progress.py init   --file prog.json --plan sources-plan.json --run RUNID [--version 5.6]
  python3 progress.py source --file prog.json --key KEY --status ok|partial|failed|skipped|reading
                             [--done N] [--found N] [--fresh N] [--updated N] [--requests N] [--errors N] [--late N] [--note "…"] [--log "…"]
                             (метки startedAt/finishedAt/durationSec ставятся сами: reading — старт, ok/partial/failed — финиш)
  python3 progress.py stage  --file prog.json --stage "…" [--percent N] [--log "…"]
  python3 progress.py finish --file prog.json --new N --active N --updated N --total N
  python3 progress.py error  --file prog.json --stage "…"

Процент: 5 — чтение базы, 5..90 — доля прочитанных страниц (done / planned по всем источникам, кроме skipped),
90..99 — запись (stage), 100 — finish. В log хранятся последние 40 записей.
"""
import argparse, datetime as dt, json

LOG_KEEP = 40


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def log(p, text):
    if text:
        p["log"] = (p.get("log", []) + [{"at": now(), "text": text}])[-LOG_KEEP:]


def percent(p):
    act = [s for s in p["sources"] if s["status"] != "skipped"]
    planned = sum(max(s.get("planned") or 0, 0) for s in act) or 1
    done = sum(min(s.get("done") or 0, s.get("planned") or 0) for s in act)
    return 5 + round(85 * done / planned)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=["init", "source", "stage", "finish", "error"])
    a.add_argument("--file", required=True)
    a.add_argument("--plan")
    a.add_argument("--run")
    a.add_argument("--version", default="")
    a.add_argument("--key")
    a.add_argument("--status")
    for f in ("done", "found", "fresh", "updated", "requests", "errors", "late", "percent", "new", "active", "total"):
        a.add_argument("--" + f, type=int)
    a.add_argument("--note")
    a.add_argument("--stage")
    a.add_argument("--light", action="store_true", help="init: облегчённый запуск — читаются только источники плана с полем light")
    a.add_argument("--allow-wait", action="store_true", help="finish: завершить, даже если есть источники в статусе wait (только если шаг невозможен)")
    a.add_argument("--log")
    x = a.parse_args()

    if x.cmd == "init":
        plan = json.load(open(x.plan, encoding="utf-8"))
        src = []
        for s in plan.get("sources", plan if isinstance(plan, list) else []):
            planned = s.get("batch") or s.get("planned") or 1
            src.append({"key": s["key"], "name": s.get("name", s["key"]), "method": s.get("method", ""),
                        "planned": planned, "done": 0, "found": 0, "fresh": 0, "updated": 0,
                        "status": "skipped" if (s.get("type") == "skip" or (x.light and not s.get("light"))) else "wait",
                        "note": s.get("note", "") if s.get("type") == "skip" else ("облегчённый запуск" if x.light and not s.get("light") else "")})
        t = now()
        p = {"runId": x.run, "status": "running", "startedAt": t, "updatedAt": t, "percent": 1,
             "stage": "Запуск: читаю план и базу сайта", "sources": src, "log": []}
        log(p, "Облачный сеанс начал сбор" + (f" по плану v{x.version}" if x.version else ""))
    else:
        p = json.load(open(x.file, encoding="utf-8"))
        if x.cmd == "source":
            s = next((s for s in p["sources"] if s["key"] == x.key), None)
            if s is None:
                raise SystemExit(f"источника {x.key} нет в прогрессе")
            if x.status:
                s["status"] = x.status
                if x.status == "reading":
                    s.setdefault("startedAt", now())
                elif x.status in ("ok", "partial", "failed", "skipped"):
                    s["finishedAt"] = now()
                    if s.get("startedAt"):
                        s["durationSec"] = max(0, round((dt.datetime.strptime(s["finishedAt"], "%Y-%m-%dT%H:%M:%S.000Z")
                                                         - dt.datetime.strptime(s["startedAt"], "%Y-%m-%dT%H:%M:%S.000Z")).total_seconds()))
            for f in ("done", "found", "fresh", "updated", "requests", "errors", "late"):
                if getattr(x, f) is not None:
                    s[f] = getattr(x, f)
            if x.status in ("ok", "partial", "failed") and x.done is None:
                s["done"] = s["planned"]
            if x.note is not None:
                s["note"] = x.note
            p["percent"] = max(p.get("percent", 0), min(percent(p), 90))
            p["stage"] = x.stage or (f"Читаю {s['name']}" if s["status"] == "reading" else f"Готово: {s['name']}")
            log(p, x.log or (f"{s['name']}: подходящих {s['found']}, новых {s['fresh']}"
                             + (f", обновлено {s['updated']}" if s["updated"] else "")
                             + (f" — {s['note']}" if s["note"] else "") if s["status"] != "reading" else ""))
        elif x.cmd == "stage":
            p["stage"] = x.stage
            if x.percent is not None:
                p["percent"] = x.percent
            log(p, x.log)
        elif x.cmd == "finish":
            left = [s["key"] for s in p["sources"] if s.get("status") == "wait"]
            if left and not x.allow_wait:
                print("НЕ ЗАВЕРШЕНО: источники ещё в статусе wait: " + ", ".join(left)
                      + ". Выполни их или явно отметь каждый: progress.py source --key <ключ> --status skipped --note «причина». Файл не изменён.")
                raise SystemExit(3)
            p.update(status="done", percent=100,
                     stage=f"Готово: новых {x.new}, из них с открытым приёмом {x.active}",
                     result={"new": x.new, "active": x.active, "updated": x.updated, "total": x.total})
            log(p, p["stage"])
        elif x.cmd == "error":
            p.update(status="error", stage=x.stage)
            log(p, "Ошибка: " + x.stage)
        p["updatedAt"] = now()
    json.dump(p, open(x.file, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{p['status']} {p['percent']}% · {p['stage']}")


if __name__ == "__main__":
    main()
