"""contacts.py — контакт по закупке из извещения ЕИС (ГосПлан: /fz44/purchases/<номер>, /fz223/purchases/<номер>):
ответственное лицо, телефон и e-mail, которые заказчик сам указал в извещении. Без платных ключей и квот.

Запуск: GOSPLAN_KEY=... python3 contacts.py --queue <meta/queue.json> [--known <папка leadcontacts>] --date ГГГГ-ММ-ДД --out contacts_out.json [--max 60]
Берёт лиды очереди (поле items, по убыванию priority) с номером ЕИС: 19 цифр на 0 — 44-ФЗ, 11 цифр — 223-ФЗ; лиды с уже найденным контактом
пропускает (обновляет раз в 90 дней), без контакта — повторяет раз в 30 дней; не больше --max запросов.
Выход: {"docs": [{id, key, name, phone, email, org, checkedAt} | {id, key, none: true, checkedAt}], "stats": {...}}; документ — в коллекцию
leadcontacts (doc_id = key, как у лида на сайте). Ключ только из окружения GOSPLAN_KEY, в выход не пишется.
"""
import argparse, datetime as dt, glob, json, os, re, time, urllib.error, urllib.parse, urllib.request

import collector as C

KEY = os.environ.get("GOSPLAN_KEY", "")
BASE = "https://v2.gosplan.info"


def get(path):
    url = BASE + path + "?" + urllib.parse.urlencode({"apikey": KEY})
    for t in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404: return {}
            if e.code == 429 or e.code >= 500: time.sleep(2 + 3 * t); continue
            return None
        except Exception:
            time.sleep(2)
    return None


def fio(p):
    return " ".join(str(p.get(k) or "").strip() for k in ("lastName", "firstName", "middleName")).strip()


def pick(doc, num):
    """Контакт из ответа ГосПлана: новейшая версия извещения, где указан телефон или e-mail."""
    for d in reversed(doc.get("docs") or []):
        s = d.get("source") or {}
        if num.startswith("0"):                        # 44-ФЗ
            r = s.get("purchaseResponsibleInfo") or {}
            i = r.get("responsibleInfo") or {}
            c = {"name": fio(i.get("contactPersonInfo") or {}), "phone": (i.get("contactPhone") or "").strip(),
                 "email": (i.get("contactEMail") or "").strip(), "org": ((r.get("responsibleOrgInfo") or {}).get("shortName") or "").strip()[:120]}
        else:                                          # 223-ФЗ
            i = s.get("contact") or {}
            c = {"name": fio(i), "phone": (i.get("phone") or "").strip(), "email": (i.get("email") or "").strip(), "org": ""}
        if c["phone"] or c["email"]: return c
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--queue", required=True); a.add_argument("--known")
    a.add_argument("--date", required=True); a.add_argument("--out", required=True); a.add_argument("--max", type=int, default=60)
    x = a.parse_args()
    if not KEY: raise SystemExit("нет GOSPLAN_KEY в окружении")
    today = dt.date.fromisoformat(x.date)
    q = json.load(open(x.queue, encoding="utf-8")); q = q.get("data", q)
    items = sorted(q.get("items") or [], key=lambda t: -(t.get("priority") or 0))
    known = {}
    if x.known and os.path.isdir(x.known):
        for f in glob.glob(os.path.join(x.known, "**", "*.json"), recursive=True):
            j = json.load(open(f, encoding="utf-8")); known[os.path.basename(f)[:-5]] = j.get("data", j)
    cand = []
    for t in items:
        i = str(t.get("id"))
        if not (re.fullmatch(r"0\d{18}", i) or re.fullmatch(r"\d{11}", i)): continue
        old = known.get(C.site_key(i))
        if old:
            age = (today - dt.date.fromisoformat(old.get("checkedAt", "2000-01-01"))).days
            if age < (30 if old.get("none") else 90): continue
        cand.append(i)
    docs, st = [], {"queue": len(items), "candidates": len(cand), "requests": 0, "found": 0, "none": 0}
    for i in cand[: x.max]:
        j = get(("/fz44/purchases/" if i.startswith("0") else "/fz223/purchases/") + i); st["requests"] += 1
        time.sleep(0.15)
        if j is None: continue
        c = pick(j, i) if isinstance(j, dict) else None
        k = C.site_key(i)
        if c:
            docs.append(dict({"id": i, "key": k}, **{f: v for f, v in c.items() if v}, checkedAt=x.date)); st["found"] += 1
        else:
            docs.append({"id": i, "key": k, "none": True, "checkedAt": x.date}); st["none"] += 1
    json.dump({"docs": docs, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
