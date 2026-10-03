"""feedback.py — отчёт обратной связи: ручная разметка лидов (state) → какие слова, формы, источники и корзины цены чаще «не интересно» или «для проработки».

Запуск раз в неделю: python3 feedback.py --state <папка документов state> --leadsets <папка leadsets> [--feedback <папка feedback>] --dict dictionary.json --date ГГГГ-ММ-ДД --out report.json
Метки: «плюс» — checked, further, work, bid, won; «минус» — uninteresting, skip (lost не считается ни тем ни другим). Ключ документа state — как у лида на сайте (site_key).
Кандидаты: признак с n ≥ 5 отметок и долей «минус» ≥ 80 % → предложить штраф/периферию/исключение; долей «плюс» ≥ 70 % → бонус. Причины «не интересно» берутся из feedback (reasons).
Результат — документ meta/feedback-report; правки словаря и fit.json вносит только человек отдельным коммитом.
"""
import argparse, collections, glob, json, os, re

from collector import Matcher, norm_text, site_key

PLUS = {"checked", "further", "work", "bid", "won"}
MINUS = {"uninteresting", "skip"}
STOP = set("оказание услуг услуги услуг работ для по и в на с к от из о об при за их его ее оказания выполнение закупка заказчика нужд года год ".split())


def load_docs(d):
    out = {}
    for f in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
        j = json.load(open(f, encoding="utf-8")); out[os.path.basename(f)[:-5]] = j.get("data", j)
    return out


def price_bucket(l):
    p = l.get("price")
    if not isinstance(p, (int, float)) or p <= 0: return "цена: нет"
    if l.get("currency", "RUB") != "RUB": return "цена: не рубли"
    return "цена: < 100 тыс." if p < 1e5 else "цена: 100–500 тыс." if p < 5e5 else "цена: 0,5–2 млн" if p < 2e6 else "цена: > 2 млн"


def features(l, m):
    t = norm_text(l.get("title", ""))
    f = {"источник: " + str(l.get("source", "")).split(":")[0], price_bucket(l)}
    rel, why, terms = m.classify(l.get("title", ""), l.get("okpd2") or ())
    f |= {"форма: " + w for w in terms}
    f |= {"флаг: " + x for x in (l.get("flags") or [])}
    f |= {"слово: " + w for w in set(re.findall(r"[а-яё]{6,}", t)) if w[:5] not in STOP}
    return f


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--state", required=True); a.add_argument("--leadsets", required=True); a.add_argument("--feedback")
    a.add_argument("--dict", default="dictionary.json"); a.add_argument("--date", required=True); a.add_argument("--out", required=True)
    a.add_argument("--min-n", type=int, default=5)
    x = a.parse_args()
    m = Matcher(json.load(open(x.dict, encoding="utf-8")))
    state = load_docs(x.state)
    leads = {}
    for f in glob.glob(os.path.join(x.leadsets, "*.json")):
        j = json.load(open(f, encoding="utf-8")); j = j.get("data", j)
        for l in j.get("leads", []): leads[site_key(l.get("id"))] = l
    reasons = collections.Counter()
    if x.feedback and os.path.isdir(x.feedback):
        for d in load_docs(x.feedback).values():
            for r in d.get("reasons") or []: reasons[r] += 1
    stat = collections.defaultdict(lambda: [0, 0])      # признак → [плюс, минус]
    marked = plus = minus = 0
    for key, d in state.items():
        l, st = leads.get(key), d.get("status")
        if not l or st not in PLUS | MINUS: continue
        marked += 1
        side = 0 if st in PLUS else 1
        plus += side == 0; minus += side == 1
        for ft in features(l, m): stat[ft][side] += 1
    cand = []
    for ft, (p, n) in stat.items():
        tot = p + n
        if tot < x.min_n: continue
        if n / tot >= 0.8: cand.append({"feature": ft, "n": tot, "minus": n, "plus": p, "suggest": "штраф, периферия или исключение"})
        elif p / tot >= 0.7: cand.append({"feature": ft, "n": tot, "minus": n, "plus": p, "suggest": "бонус или ядро"})
    cand.sort(key=lambda c: (-c["n"], c["feature"]))
    doc = {"date": x.date, "marked": marked, "plus": plus, "minus": minus, "reasons": dict(reasons.most_common()),
           "candidates": cand[:60], "note": "Кандидаты по разметке человека (n ≥ %d). Правки dictionary.json и fit.json — только человеком." % x.min_n}
    json.dump(doc, open(x.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"отмечено {marked} (плюс {plus}, минус {minus}), кандидатов {len(cand)}")


if __name__ == "__main__":
    main()
