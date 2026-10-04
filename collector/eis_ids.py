"""Номера ЕИС для шага eis-docs: только настоящие номера извещений, без внутренних номеров агрегаторов.

Запуск: python3 eis_ids.py --new save --leadsets db/leadsets --leaddocs db/leaddocs [--misses misses.json --now ISO]
        --date ГГГГ-ММ-ДД --limit N --out ids.json
Номер берётся из eisNumber лида; поле id — только если у лида law «44-ФЗ»/«223-ФЗ», ссылка на zakupki.gov.ru или источник
gosplan:*. Внутренние номера РосТендера (11 цифр, начинаются с 3) по виду совпадают с номерами 223-ФЗ, поэтому вид номера
сам по себе ничего не доказывает (01.10.2026 так в ГосПлан ушли 14 номеров РосТендера, все с ответом 404).
Порядок: новые лиды этого запуска (--new), затем известные с открытым приёмом или без срока; номера, уже лежащие в
leaddocs, пропускаются; с --misses — и номера, которых нет в ГосПлане (правило в eis_misses.py).
С --backfill (дозаполнение площадки, etp_resolve.py) после них идут и закрытые закупки — свежие сроки первыми, а номера из
leaddocs без площадки и без признака electronic (разобраны до eisdocs.py 2.0) спрашиваются повторно.
"""
import argparse, glob, json, os, re
from datetime import datetime, timedelta

from eis_misses import MAX_TRIES, RETRY_HOURS, load as load_misses

EIS_RE = re.compile(r"0\d{18}|3\d{10}")


def docs_in(path):
    files = sorted(glob.glob(os.path.join(path, "*.json"))) if os.path.isdir(path) else [path]
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        yield d.get("data", d) if isinstance(d, dict) else {"leads": d}


def eis_number(lead):
    n = str(lead.get("eisNumber") or "")
    if EIS_RE.fullmatch(n):
        return n
    i = str(lead.get("id") or "")
    if not EIS_RE.fullmatch(i):
        return ""
    if lead.get("law") in ("44-ФЗ", "223-ФЗ") or "zakupki.gov.ru" in str(lead.get("url") or "") \
            or str(lead.get("source") or "").startswith("gosplan"):
        return i
    return ""


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--new", action="append", default=[])
    a.add_argument("--leadsets", required=True)
    a.add_argument("--leaddocs")
    a.add_argument("--date", required=True)
    a.add_argument("--limit", type=int, default=150)
    a.add_argument("--misses")
    a.add_argument("--now")
    a.add_argument("--out", required=True)
    a.add_argument("--match", help="meta/etp-match: номера ЕИС, найденные etp_match.py для карточек агрегаторов, — сразу после новых")
    a.add_argument("--backfill", action="store_true", help="после открытых — закрытые закупки и старые разборы без площадки")
    x = a.parse_args()
    done = set()
    if x.leaddocs and os.path.exists(x.leaddocs):
        for d in docs_in(x.leaddocs):
            for n, it in (d.get("items") or {}).items():
                if x.backfill and not it.get("platform") and "electronic" not in it:
                    continue   # разобран до 2.0: повторить, чтобы узнать, электронная ли закупка
                done.add(n)
    held = set()
    if x.misses:
        now = datetime.fromisoformat((x.now or "").replace("Z", "+00:00"))
        for n, m in load_misses(x.misses).items():
            try:
                last = datetime.fromisoformat(str(m.get("last", "")).replace("Z", "+00:00"))
            except ValueError:
                last = None
            if int(m.get("tries", 0)) >= MAX_TRIES or (last and now - last < timedelta(hours=RETRY_HOURS)):
                held.add(n)
    out, seen, skipped = [], set(done) | held, 0

    def take(lead):
        nonlocal skipped
        n = eis_number(lead)
        if not n:
            if EIS_RE.fullmatch(str(lead.get("id") or "")):
                skipped += 1
            return
        if n not in seen:
            seen.add(n)
            out.append(n)

    for p in x.new:
        for d in docs_in(p):
            for lead in d.get("leads", []):
                take(lead)
    if x.match and os.path.exists(x.match):
        m = json.load(open(x.match, encoding="utf-8")); m = m.get("data", m)
        for it in (m.get("items") or {}).values():
            n = str(it.get("eis") or "")
            if EIS_RE.fullmatch(n) and n not in seen:
                seen.add(n); out.append(n)
    fresh = len(out)
    for d in docs_in(x.leadsets):
        for lead in d.get("leads", []):
            dl = str(lead.get("deadline") or "")
            if not dl or dl >= x.date:
                take(lead)
    if x.backfill:
        rest = []
        for d in docs_in(x.leadsets):
            for lead in d.get("leads", []):
                dl = str(lead.get("deadline") or "")
                if dl and dl < x.date:
                    rest.append((dl, lead))
        for _, lead in sorted(rest, key=lambda t: t[0], reverse=True):
            take(lead)
    out = out[:x.limit]
    json.dump(out, open(x.out, "w", encoding="utf-8"))
    print(f"номеров {len(out)} (новых этого запуска {min(fresh, len(out))}); уже в leaddocs {len(done)}; "
          f"похожих на номер ЕИС, но не из ЕИС, пропущено {skipped}; нет в ГосПлане, ждут повтора или сняты {len(held)}")


if __name__ == "__main__":
    main()
