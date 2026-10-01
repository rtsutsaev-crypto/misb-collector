"""Готовит документы leadsets одного источника для записи в базу сайта.

Запуск: python3 save_leads.py --run RUNID --key KEY --date ГГГГ-ММ-ДД --in out.json [--in out2.json …] [--outdir save]
Вход: out.json скриптов (объект с полем leads) или JSON-массив лидов.
Выход: файлы <outdir>/<doc_id>.json вида {source: "Обновление по кнопке", collectedAt, leads}, не больше 300 лидов
и 250 КБ в документе (doc_id = RUNID-KEY, дальше -2, -3 …). Печатает по строке на документ: doc_id, путь, число лидов.
Каждый файл записывается в базу как есть: ArtifactData set, collection "leadsets", doc_id, file_path.
"""
import argparse, json, os

MAX_LEADS = 300
MAX_BYTES = 250 * 1024


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    return d.get("leads", []) if isinstance(d, dict) else d


def size(obj):
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def chunks(leads, collected):
    head = size({"source": "Обновление по кнопке", "collectedAt": collected, "leads": []})
    cur, cur_bytes = [], head
    for lead in leads:
        b = size(lead) + 2
        if cur and (len(cur) >= MAX_LEADS or cur_bytes + b > MAX_BYTES):
            yield cur
            cur, cur_bytes = [], head
        cur.append(lead)
        cur_bytes += b
    if cur:
        yield cur


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--run", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--date", required=True)
    a.add_argument("--in", dest="inp", action="append", required=True)
    a.add_argument("--outdir", default="save")
    x = a.parse_args()
    leads, seen = [], set()
    for p in x.inp:
        for lead in load(p):
            k = str(lead.get("id") or "") or json.dumps(lead, ensure_ascii=False, sort_keys=True)
            if k not in seen:
                seen.add(k)
                leads.append(lead)
    os.makedirs(x.outdir, exist_ok=True)
    for i, part in enumerate(chunks(leads, x.date)):
        doc_id = f"{x.run}-{x.key}" + (f"-{i + 1}" if i else "")
        path = os.path.join(x.outdir, doc_id + ".json")
        json.dump({"source": "Обновление по кнопке", "collectedAt": x.date, "leads": part},
                  open(path, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"{doc_id}\t{path}\t{len(part)}")
    if not leads:
        print(f"# {x.key}: новых лидов нет, записывать нечего")


if __name__ == "__main__":
    main()
