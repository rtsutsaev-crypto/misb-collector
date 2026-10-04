"""etp_match.py — та же закупка в ЕИС для карточки агрегатора (РосТендер, TenderGuru, Бикотендер, Комтендер, Energybase,
Synapse): номер извещения, по которому eisdocs.py узнает площадку. Агрегаторы прячут площадку за входом или подпиской,
а закупки 44-ФЗ и 223-ФЗ из их лент лежат и в ЕИС.

Поиск в ГосПлане: самое длинное значимое слово названия (object_info ищет по словам, не по фразе) и окно по сроку подачи
(44-ФЗ — collecting_finished, 223-ФЗ — submission_close: от срока −1 до +2 дней). Среди ответов — ближайшее название
(difflib), +0.15 за совпадение цены. Совпадение принимается от --min (0.8). Проба 04.10.2026 на 50 карточках: 8 уверенных
совпадений и 9 похожих; остальные — коммерческие закупки вне ЕИС.

Запуск: GOSPLAN_KEY=... python3 etp_match.py --leadsets <папка> --leaddocs <папка> [--known <meta/etp-match.json>]
        --date ГГГГ-ММ-ДД --out etp-match.json [--max 400] [--parallel 4] [--budget-sec 900]
Берёт карточки агрегаторов без площадки и без номера ЕИС, у которых есть срок подачи; уже проверенные (в --known) не
повторяет 30 дней. Выход — весь документ meta/etp-match: {"items": {id: {eis, score, title, at} | {none: true, at}}, "stats"}.
Ключ ГосПлана только из окружения.
"""
import argparse, collections, concurrent.futures as cf, datetime as dt, difflib, glob, json, os, re, time, urllib.error, urllib.parse, urllib.request

import etp_resolve as R

KEY = os.environ.get("GOSPLAN_KEY", "")
AGG = re.compile(r"^(rostender|tenderguru|bicotender|bico|komtender|energybase|synapse)")
EIS = re.compile(r"(?<!\d)(0\d{18}|3\d{10})(?!\d)")
STOP = set(("оказание оказанию оказании услуг услуги услуга услугам работ работы поставка право заключения договора договор "
            "выполнение проведение проведению проведения организация организации организацию обучение обучению обучения "
            "образовательных образовательные дополнительной профессиональной программе программам программы повышения "
            "квалификации нужд сотрудников работников персонала области").split())


def norm(t): return " ".join(re.findall(r"[а-яёa-z0-9]+", (t or "").lower()))


def gp(path, params):
    u = "https://v2.gosplan.info" + path + "?" + urllib.parse.urlencode(dict(params, apikey=KEY), doseq=True)
    for k in range(3):
        try:
            with urllib.request.urlopen(u, timeout=40) as r: return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(2 + 2 * k); continue
            return None
        except Exception:
            time.sleep(2)
    return None


def match(l):
    t = l.get("title") or ""
    ws = sorted({w for w in re.findall(r"[а-яёa-z]{5,}", t.lower()) if w not in STOP}, key=len, reverse=True)
    if not ws: return None, 0
    d = dt.date.fromisoformat(l["deadline"][:10])
    a, b = (d - dt.timedelta(days=1)).isoformat(), (d + dt.timedelta(days=2)).isoformat()
    best, req = None, 0
    for path, pa, pb in (("/fz44/purchases", "collecting_finished_after", "collecting_finished_before"),
                         ("/fz223/purchases", "submission_close_after", "submission_close_before")):
        j = gp(path, {"object_info": ws[0], pa: a, pb: b, "limit": 100}); req += 1
        for x in (j if isinstance(j, list) else []):
            s = difflib.SequenceMatcher(None, norm(t)[:200], norm(x.get("object_info"))[:200]).ratio()
            try:
                if l.get("price") and x.get("max_price") and abs(float(x["max_price"]) - float(l["price"])) < 1: s += 0.15
            except (TypeError, ValueError):
                pass
            if not best or s > best[0]: best = (round(s, 3), str(x.get("purchase_number") or ""), (x.get("object_info") or "")[:160])
    return best, req


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leadsets", required=True); a.add_argument("--leaddocs", required=True); a.add_argument("--known")
    a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--max", type=int, default=400); a.add_argument("--parallel", type=int, default=4)
    a.add_argument("--budget-sec", type=int, default=900); a.add_argument("--min", type=float, default=0.8)
    x = a.parse_args()
    if not KEY: raise SystemExit("нет GOSPLAN_KEY в окружении")

    def load(f):
        j = json.load(open(f, encoding="utf-8")); return j.get("data", j)
    leads = {}
    for f in sorted(glob.glob(os.path.join(x.leadsets, "**", "*.json"), recursive=True)):
        for l in load(f).get("leads", []): leads[str(l.get("id"))] = l
    ld = {}
    for f in glob.glob(os.path.join(x.leaddocs, "**", "*.json"), recursive=True): ld.update(load(f).get("items") or {})
    known = (load(x.known).get("items") or {}) if x.known and os.path.exists(x.known) else {}
    today = dt.date.fromisoformat(x.date)
    cand = []
    for i, l in leads.items():
        if not AGG.match(str(l.get("source") or "")) or not re.match(r"\d{4}-\d{2}-\d{2}", l.get("deadline") or ""): continue
        if R.site_etp(l, ld.get(i, {})) != "none" or EIS.search(" ".join([l.get("url") or "", l.get("note") or ""])): continue
        k = known.get(i)
        if k and (today - dt.date.fromisoformat(k.get("at", "2000-01-01"))).days < 30: continue
        cand.append((l.get("deadline"), i, l))
    cand.sort(key=lambda t: t[0], reverse=True)            # свежие сроки первыми: открытые закупки важнее архива
    items = dict(known); st = collections.Counter(candidates=len(cand)); t0 = time.time()
    with cf.ThreadPoolExecutor(x.parallel) as ex:
        futs = {}
        for _, i, l in cand[: x.max]:
            futs[ex.submit(match, l)] = i
        for f in cf.as_completed(futs):
            i = futs[f]
            if time.time() - t0 > x.budget_sec:
                st["stopped"] = 1; ex.shutdown(wait=False, cancel_futures=True); break
            try: best, req = f.result()
            except Exception: st["errors"] += 1; continue
            st["requests"] += req; st["checked"] += 1
            if best and best[0] >= x.min and best[1]:
                items[i] = {"eis": best[1], "score": best[0], "title": best[2], "at": x.date}; st["matched"] += 1
            else:
                items[i] = {"none": True, "score": best[0] if best else 0, "at": x.date}
    json.dump({"items": items, "stats": dict(st), "updatedAt": x.date}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(dict(st), ensure_ascii=False))


if __name__ == "__main__":
    main()
