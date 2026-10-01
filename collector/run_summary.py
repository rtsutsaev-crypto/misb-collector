"""Короткий итог сбора для уведомления: последнее сообщение запуска (Routine присылает его на телефон или почту).

Запуск: python3 run_summary.py --dict dictionary.json --leads save --date ГГГГ-ММ-ДД --new N --active M [--updated U]
--leads — папка (или файлы) документов этого запуска из save_leads.py. Лучшие три — новые лиды с открытым приёмом
или без срока, по оценке Matcher.score (близость к профилю МИСБ, ТЭК, цена, срок); обязательное обучение (охрана
труда, электробезопасность) — после остальных, одинаковые названия — один раз. Печатает готовый текст.
"""
import argparse, glob, json, os

from collector import Matcher, norm_key

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


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dict", default="dictionary.json")
    a.add_argument("--leads", action="append", default=[])
    a.add_argument("--date", required=True)
    a.add_argument("--new", type=int, required=True)
    a.add_argument("--active", type=int, required=True)
    a.add_argument("--updated", type=int, default=0)
    x = a.parse_args()
    m = Matcher(json.load(open(x.dict, encoding="utf-8")))
    seen, best = set(), []
    for l in leads_in(x.leads):
        k = norm_key(str(l.get("title") or "")) or str(l.get("id"))
        dl = l.get("deadline") or ""
        if k in seen or (dl and dl < x.date):
            continue
        seen.add(k)
        s, _ = m.score(l, x.date)
        if s > 1:
            mand = "mandatory" in m.flag_list(l.get("title", ""), l.get("customer", ""))
            best.append((mand, -s, l))
    best.sort(key=lambda t: t[:2])
    head = f"МИСБ: новых лидов {x.new}, с открытым приёмом {x.active}"
    if x.updated:
        head += f", обновлено {x.updated}"
    lines = [head + "."]
    if x.new == 0:
        lines.append("Новых закупок по теме нет.")
    for _, _, l in best[:3]:
        t = cut(l.get("title"), 90)
        tail = [v for v in (cut(l.get("customer"), 50), money(l.get("price"), l.get("currency")),
                            ("до " + dmy(l.get("deadline"))) if l.get("deadline") else "") if v]
        lines.append(f"• {t}" + (f" — {', '.join(tail)}" if tail else ""))
    lines.append(f"Все новые: кнопка «смотреть» на сайте {SITE}" if x.new else f"Сайт: {SITE}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
