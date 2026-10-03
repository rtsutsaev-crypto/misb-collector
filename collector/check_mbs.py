"""check_mbs.py — сверка словаря и fit с каталогом программ Московской бизнес-школы (775 названий, файл заказчика от 04.10.2026).
Каждое название должно (1) получить хотя бы одно направление fit и (2) пройти классификатор, если поставить перед ним «Оказание услуг по обучению по программе: ».
Запуск (из папки collector): python3 check_mbs.py [--show]; код выхода 1, если без направления больше 20 названий или исключено больше 8."""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector as C, priority as P

a = argparse.ArgumentParser(); a.add_argument("--show", action="store_true"); x = a.parse_args()
T = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref", "mbs-titles.json"), encoding="utf-8"))
m = C.Matcher(json.load(open("dictionary.json", encoding="utf-8"))); fit = P.Fit(json.load(open("fit.json", encoding="utf-8")))
nod = [t for t in T if not fit.profile({"title": t})[1]]
bad = [(t, m.classify("Оказание услуг по обучению по программе: " + t)[1]) for t in T]
bad = [(t, w) for t, w in bad if not m.classify("Оказание услуг по обучению по программе: " + t)[0]]
print(f"названий {len(T)}; без направления {len(nod)}; исключено классификатором {len(bad)}")
if x.show:
    for t in nod: print("  нет направления:", t)
    for t, w in bad: print("  исключено:", t, "|", w)
sys.exit(1 if len(nod) > 20 or len(bad) > 8 else 0)
