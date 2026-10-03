"""Разовое дозаполнение срока подачи у лидов goszakup.gov.kz, собранных до перехода на поиск объявлений (в списке лотов срока нет).

Запуск: python3 kz_backfill.py --leads <папка leadsets> --out <папка leadupdates> --date ГГГГ-ММ-ДД [--pause 5] [--limit 300]
Берёт лиды source «kz:goszakup» без срока, открывает карточку объявления (/ru/announce/index/<id>, crawl-delay 5 с по robots.txt),
читает «Срок окончания приема заявок» и пишет документ leadupdates/<ключ лида как на сайте: site_key(id)> (id, deadline, prevDeadline "", url, urls, changedAt, runId, source).
Записывать эти файлы в базу: ArtifactData batch, collection "leadupdates", не больше 50 за раз; лид не удаляется, меняется только срок.
"""
import argparse, glob, json, os, re, subprocess, time

from collector import site_key

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leads", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--date", required=True)
    a.add_argument("--pause", type=float, default=5)
    a.add_argument("--limit", type=int, default=300)
    x = a.parse_args()
    todo = {}
    for f in sorted(glob.glob(os.path.join(x.leads, "*.json"))):
        d = json.load(open(f, encoding="utf-8")); d = d.get("data", d)
        for l in d.get("leads", []):
            if l.get("source") == "kz:goszakup" and not l.get("deadline") and l.get("url"):
                todo[str(l["id"])] = l["url"]
    os.makedirs(x.out, exist_ok=True)
    done = nodl = failed = 0
    for lid, url in list(todo.items())[: x.limit]:
        r = subprocess.run(["curl", "-s", "-L", "-m", "60", "-A", UA, "-w", "\n%{http_code}", url], capture_output=True, text=True, errors="ignore")
        body, _, code = r.stdout.rpartition("\n")
        m = re.search(r"Срок окончания приема заявок</label>.*?value=\"(20\d\d-\d\d-\d\d)", body, re.S)
        if code == "200" and m:
            doc = {"id": lid, "deadline": m.group(1), "prevDeadline": "", "url": url, "urls": [url], "changedAt": x.date,
                   "runId": "kz-backfill-" + x.date.replace("-", ""), "source": "kz-goszakup"}
            json.dump(doc, open(os.path.join(x.out, site_key(lid) + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
            done += 1
        elif code == "200":
            nodl += 1
        else:
            failed += 1
        time.sleep(x.pause)
    print(json.dumps({"всего без срока": len(todo), "записано": done, "срока нет на странице": nodl, "ошибок": failed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
