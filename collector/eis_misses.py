"""Номера ЕИС, которых нет в ГосПлане: сколько раз и когда их уже спрашивали (документ meta/eisdocs-misses).

Запуск после eisdocs.py: python3 eis_misses.py --misses misses.json --errors errors.json --ok out.json --now ISO --out new.json
misses.json — прежний документ (нет — не передавай --misses), errors.json и out.json — из eisdocs.py.
Новые ошибки добавляются (tries = 1), повторные увеличивают tries; найденные номера из списка убираются.
eis_ids.py с --misses пропускает номер, если его спрашивали меньше RETRY_HOURS часов назад или tries >= MAX_TRIES:
свежие извещения 223-ФЗ появляются в ГосПлане с задержкой (01.10.2026 номер 32616428735 дал 404 в 15:39 и нашёлся
в 16:25), а старые (2018, 2024) не появляются никогда и без учёта спрашивались бы в каждом запуске.
"""
import argparse, json, os

RETRY_HOURS = 6
MAX_TRIES = 4
KEEP = 3000


def load(path):
    if not path or not os.path.exists(path):
        return {}
    d = json.load(open(path, encoding="utf-8"))
    d = d.get("data", d) if isinstance(d, dict) else {}
    return d.get("items", d) if isinstance(d, dict) else {}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--misses")
    a.add_argument("--errors", required=True)
    a.add_argument("--ok")
    a.add_argument("--now", required=True)
    a.add_argument("--out", required=True)
    x = a.parse_args()
    items = load(x.misses)
    errors = json.load(open(x.errors, encoding="utf-8")) if os.path.exists(x.errors) else {}
    ok = json.load(open(x.ok, encoding="utf-8")) if x.ok and os.path.exists(x.ok) else {}
    found = [n for n in ok if n in items]
    for n in found:
        items.pop(n)
    for n, code in errors.items():
        m = items.get(n) or {"first": x.now, "tries": 0}
        m.update(tries=int(m.get("tries", 0)) + 1, last=x.now, code=str(code))
        items[n] = m
    if len(items) > KEEP:
        items = dict(sorted(items.items(), key=lambda kv: kv[1].get("last", ""))[-KEEP:])
    json.dump({"updatedAt": x.now, "retryHours": RETRY_HOURS, "maxTries": MAX_TRIES, "items": items},
              open(x.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    gave_up = sum(1 for m in items.values() if int(m.get("tries", 0)) >= MAX_TRIES)
    print(f"ошибок в этом запуске {len(errors)}; нашлись после прежних ошибок {len(found)}; "
          f"в списке {len(items)}, из них больше не спрашиваются {gave_up}")


if __name__ == "__main__":
    main()
