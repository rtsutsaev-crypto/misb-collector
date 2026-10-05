"""Короткий итог сбора для уведомления: последнее сообщение запуска (Routine присылает его на телефон или почту).

Запуск: python3 run_summary.py --dict dictionary.json --leads save --date ГГГГ-ММ-ДД --new N --active M [--updated U]
--leads — папка (или файлы) документов этого запуска из save_leads.py. Лучшие три — новые лиды с открытым приёмом
или без срока, по оценке Matcher.score (близость к профилю МИСБ, ТЭК, цена, срок); обязательное обучение (охрана
труда, электробезопасность) — после остальных, одинаковые названия — один раз. Печатает готовый текст.
"""
import argparse, datetime as dt, glob, json, os

from collector import Matcher, is_late, norm_key

SITE = "https://claude.ai/artifact/DXESGRPAkHFVVU8Ugj5PUQ"


def leads_in(paths):
    for p in paths:
        files = sorted(glob.glob(os.path.join(p, "*.json"))) if os.path.isdir(p) else [p]
        for f in files:
            d = json.load(open(f, encoding="utf-8"))
            d = d.get("data", d) if isinstance(d, dict) else {"leads": d}
            yield from d.get("leads", [])


def cut(s, n):
    s = " ".join(str(s or "").split())
    return s[:n] + "…" if len(s) > n else s


def money(p, cur):
    if not isinstance(p, (int, float)) or p <= 0:
        return ""
    s = f"{p / 1e6:.1f} млн" if p >= 1e6 else f"{round(p / 1e3)} тыс."
    return s + ("" if (cur or "RUB") == "RUB" else f" {cur}")


def dmy(d):
    return f"{d[8:10]}.{d[5:7]}" if len(d or "") >= 10 else ""


def health(p, active):
    """Здоровье запуска: ключи отказали, много failed, пустой полный план, самые долгие источники (по меткам progress.py)."""
    src = p.get("sources") or []
    failed = [s for s in src if s.get("status") == "failed"]
    out = []
    warn = []
    if len(failed) >= 8:
        warn.append(f"отказали {len(failed)} источников")
    keyfail = [s["key"] for s in failed if "ключ" in (s.get("note") or "").lower() or "403" in (s.get("note") or "")]
    if keyfail:
        warn.append("ключ не принят (403): " + ", ".join(keyfail[:6]))
    run = [s for s in src if s.get("status") not in ("skipped", "wait")]
    if len(run) >= 40 and active < 15:
        warn.append(f"с открытым приёмом только {active} при полном плане")
    delta = next((s for s in src if s["key"] == "gosplan-delta"), None)
    if delta and delta.get("status") == "ok" and not delta.get("found") and dt.datetime.now(dt.timezone.utc).weekday() < 5:
        warn.append("gosplan-delta прошла без подходящих — проверьте курсор")
    if warn:
        out.append("ВНИМАНИЕ: " + "; ".join(warn) + ".")
    slow = sorted((s for s in src if s.get("durationSec")), key=lambda s: -s["durationSec"])[:5]
    if slow:
        out.append("Дольше всего: " + ", ".join(f"{s['key']} {round(s['durationSec'] / 60, 1)} мин" for s in slow))
    reqs = sum(s.get("requests") or 0 for s in src if s["key"].startswith(("gosplan", "group-profile", "contracts", "eis-docs")))
    if reqs:
        out.append(f"Запросов ГосПлана: {reqs}")
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--leads", action="append", default=[])
    a.add_argument("--date", required=True)
    a.add_argument("--new", type=int, required=True)
    a.add_argument("--active", type=int, required=True)
    a.add_argument("--updated", type=int, default=0)
    a.add_argument("--queue", help="queue.json из priority.py: вместо трёх лучших по score — пять первых лидов очереди на сегодня")
    a.add_argument("--progress", help="файл прогресса (progress.py): добавить строку «ВНИМАНИЕ» и самые долгие источники")
    x = a.parse_args()
    m = Matcher(json.load(open(x.dict, encoding="utf-8")))
    seen, best, late, sig = set(), [], 0, {}
    for l in leads_in(x.leads):
        if str(l.get("id") or "").startswith("sig-"):         # ранние сигналы и партнёрские карточки — отдельной строкой, не в новых лидах
            sig[l.get("source") or "sig"] = sig.get(l.get("source") or "sig", 0) + 1
            continue
        k = norm_key(str(l.get("title") or "")) or str(l.get("id"))
        dl = l.get("deadline") or ""
        if k in seen or (dl and dl < x.date):
            continue
        seen.add(k)
        if is_late(dl, x.date, "rfq" in (l.get("flags") or [])): late += 1
        s, _ = m.score(l, x.date)
        if s > 1:
            mand = "mandatory" in m.flag_list(l.get("title", ""), l.get("customer", ""))
            best.append((mand, -s, l))
    best.sort(key=lambda t: t[:2])
    head = f"МИСБ: новых лидов {x.new}, с открытым приёмом {x.active}"
    if late:
        head += f" (поздних — срок сегодня или завтра: {late})"
    if x.updated:
        head += f", обновлено {x.updated}"
    lines = [head + "."]
    if x.new == 0:
        lines.append("Новых закупок по теме нет.")
    qi = []
    if x.queue and os.path.exists(x.queue):
        qi = json.load(open(x.queue, encoding="utf-8")).get("queueInfo", [])[:5]
    if qi:
        lines.append("Очередь на сегодня (по приоритету):")
        best = [(False, 0, q) for q in qi]
    for _, _, l in (best[:5] if qi else best[:3]):
        t = cut(l.get("title"), 90)
        tail = [v for v in (cut(l.get("customer"), 50), money(l.get("price"), l.get("currency")),
                            ("до " + dmy(l.get("deadline"))) if l.get("deadline") else "") if v]
        lines.append(f"• {t}" + (f" — {', '.join(tail)}" if tail else ""))
    if sig:
        names = {"sig-precursor": "предвестники", "sig-terminated": "расторжения", "sig-vacancy": "вакансии", "sig-head": "новые руководители",
                 "sig-partner": "партнёры без лицензии", "sig-events": "мероприятия"}
        lines.append(f"Ранние сигналы (не входят в новые лиды): {sum(sig.values())} — " + ", ".join(f"{names.get(k, k)} {v}" for k, v in sorted(sig.items())))
    if x.progress:
        lines += health(json.load(open(x.progress, encoding="utf-8")), x.active)
    lines.append(f"Все новые: кнопка «смотреть» на сайте {SITE}" if x.new else f"Сайт: {SITE}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
