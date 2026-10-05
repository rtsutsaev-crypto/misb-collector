"""gosplan_signals.py — сигналы по закупкам и контрактам, которые уже видит монитор (ГосПлан, ключ из окружения).

1) failed — несостоявшиеся закупки 44-ФЗ: у профильных лидов, срок подачи которых истёк 1–10 дней назад, читается протокол
   (/fz44/purchases/{номер}/protocols). «Не подано ни одной заявки» или «все заявки отклонены» (п. 2 и 3 ч. 1 ст. 52 44-ФЗ) —
   заказчик объявит повтор или заключит договор с единственным поставщиком (п. 25 ч. 1 ст. 93): лид получает обновление
   {id, failed, failedAt, failReason} в коллекцию leadupdates (ArtifactData update — поля сливаются с документом; нет документа — set).
2) terminated — расторгнутые контракты на обучение (stage ET), обновлённые после курсора: заказчик остался с бюджетом и
   необученными людьми. Только предмет про обучение (Matcher), цена от --min-price, исполнитель — организация (ИНН 10 цифр).
   Выход — ранние сигналы sig-et-<реестровый номер> (source sig-terminated, флаг early) для save_leads.py.

Запуск: GOSPLAN_KEY=... python3 gosplan_signals.py --leadsets <папка leadsets> --dict dictionary.json --state fstate.json
        --date ГГГГ-ММ-ДД --out signals_out.json [--max 80] [--min-price 150000]
state (meta/signals-state): {"failedChecked": {номер: дата}, "etCursor": iso}. Выход: {leads, updates, state, stats}.
Ничего не выдумывает: причина — текст протокола (abandonedReason.name). Ключ в выход не пишется.
"""
import argparse, datetime as dt, glob, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C

KEY = os.environ.get("GOSPLAN_KEY", "")
BASE = "https://v2.gosplan.info"
FAIL_RX = re.compile(r"не подано ни одной заявки|все заявки .{0,40}отклонен|отклонены все заявки|п\.\s*[23]\s*ч\.\s*1\s*ст\.\s*52", re.I)
EIS = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber="
EIS_CT = "https://zakupki.gov.ru/epz/contract/contractCard/common-info.html?reestrNumber="


def get(path, params, st):
    url = BASE + path + "?" + urllib.parse.urlencode(dict(params, apikey=KEY), doseq=True)
    for t in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                st["requests"] += 1
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            st["requests"] += 1
            if e.code == 404: return []
            if e.code in (401, 403): raise SystemExit(f"ключ не принят: {e.code}")
            if e.code == 429 or e.code >= 500: time.sleep(2 + 3 * t); continue
            return None
        except Exception:
            time.sleep(2 + 2 * t)
    st["errors"] += 1
    return None


def failed(leadsets, m, state, today, mx, st, budget):
    lo, hi = (today - dt.timedelta(days=10)).isoformat(), (today - dt.timedelta(days=1)).isoformat()
    checked = state.setdefault("failedChecked", {})
    cand, seen = [], set()
    for f in glob.glob(os.path.join(leadsets, "*.json")):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []):
            i = str(l.get("id"))
            if i in seen or i in checked or not re.fullmatch(r"0\d{18}", i) or not (lo <= (l.get("deadline") or "") <= hi): continue
            seen.add(i)
            if m.classify(l.get("title", ""), l.get("okpd2") or ())[0]: cand.append((l["deadline"], i, l))
    cand.sort(reverse=True)
    st["failedCandidates"] = len(cand)
    ups, t0 = [], time.time()
    for _, i, l in cand[:mx]:
        if time.time() - t0 > budget: st["stopped"] = "бюджет времени"; break
        p = get(f"/fz44/purchases/{i}/protocols", {}, st)
        time.sleep(0.15)
        if p is None: continue
        fin = [d for d in p if isinstance(d, dict) and "Final" in str(d.get("doc_type", ""))] if isinstance(p, list) else []
        if not fin:
            continue                                      # итогового протокола ещё нет — проверим в следующий запуск
        checked[i] = today.isoformat()
        reason = ""
        for d in fin:
            ab = ((d.get("source") or {}).get("protocolInfo") or {}).get("abandonedReason") or {}
            if ab.get("name") and FAIL_RX.search(ab["name"]): reason = ab["name"]; break
        if reason:
            ups.append({"id": i, "failed": True, "failedAt": today.isoformat(), "failReason": reason[:300],
                        "failNote": "Закупка не состоялась: ждать повторную закупку или договор с единственным поставщиком (п. 25 ч. 1 ст. 93 44-ФЗ) — связаться с заказчиком",
                        "url": l.get("url") or EIS + i})
    st["failedChecked"] = len([1 for _, i, _ in cand[:mx] if i in checked]); st["failed"] = len(ups)
    # чистка: проверенные старше 30 дней больше не нужны
    old = (today - dt.timedelta(days=30)).isoformat()
    for k in [k for k, v in checked.items() if v < old]: checked.pop(k)
    return ups


def terminated(m, classes, state, today, min_price, st):
    since = state.get("etCursor") or (today - dt.timedelta(days=14)).isoformat() + "T00:00:00"
    out, last = [], since
    for law in ("fz44", "fz223"):
        for cls in classes:
            rows = get(f"/{law}/contracts", {"stage": "ET", "classifier": cls, "updated_after": since, "price_ge": min_price,
                                             "limit": 100, "sort": "updated_at_asc"}, st)
            time.sleep(0.15)
            for r in rows or []:
                last = max(last, r.get("updated_at") or "")
                sup = r.get("suppliers") or []
                if not sup or any(len(s) != 10 for s in sup): continue            # физлица-преподаватели (п. 33) — не сигнал
                ok, why, _ = m.classify(r.get("subject") or "", r.get("okpd2") or [])
                if not ok: continue
                exe_end = r.get("exe_end") or ""
                if exe_end and exe_end < today.isoformat(): continue            # срок исполнения уже прошёл — прекращено по факту
                rn = r["reg_num"]
                out.append({"id": "sig-et-" + rn, "title": "Контракт на обучение расторгнут: " + (r.get("subject") or "")[:300],
                            "customer": "ИНН " + (r.get("customer") or ""), "customerInn": r.get("customer") or "", "region": "",
                            "price": None, "deadline": "", "validUntil": (today + dt.timedelta(days=60)).isoformat(),
                            "law": "44-ФЗ" if law == "fz44" else "223-ФЗ", "url": EIS_CT + rn, "source": "sig-terminated",
                            "collectedAt": today.isoformat(), "country": "RU", "currency": "RUB", "flags": ["early"],
                            "eisNumber": r.get("purchase_number") or "", "signalPrice": r.get("price"),
                            "note": f"Исполнение контракта {rn} прекращено (цена {r.get('price')}, исполнитель ИНН {', '.join(sup)}, срок до {exe_end or '—'}). "
                                    "Заказчик остался с бюджетом и необученными людьми — предложить замену."})
    dedup = {l["id"]: l for l in out}
    st["terminated"] = len(dedup)
    state["etCursor"] = last[:19] if last > since else since
    return list(dedup.values())


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--leadsets", required=True); a.add_argument("--dict", default="dictionary.json"); a.add_argument("--state")
    a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--max", type=int, default=150); a.add_argument("--min-price", type=int, default=150000)
    a.add_argument("--budget-sec", type=int, default=300)
    x = a.parse_args()
    if not KEY: sys.exit("нет GOSPLAN_KEY")
    today = dt.date.fromisoformat(x.date)
    dic = json.load(open(x.dict, encoding="utf-8")); dic = dic.get("data", dic)
    m = C.Matcher(dic)
    state = json.load(open(x.state, encoding="utf-8")) if x.state and os.path.exists(x.state) else {}
    state = state.get("data", state)
    st = {"requests": 0, "errors": 0}
    ups = failed(x.leadsets, m, state, today, x.max, st, x.budget_sec)
    leads = terminated(m, sorted({c[:5] for c in dic.get("okpd2", [])}), state, today, x.min_price, st)
    state["updatedAt"] = x.date
    json.dump({"leads": leads, "updates": ups, "state": state, "stats": st}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
