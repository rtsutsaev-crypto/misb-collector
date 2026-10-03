"""Контроль размера документов базы: документ больше лимита записать нельзя, а близкий к лимиту тихо теряет записи (fit() в *-checks).

Запуск: python3 check_docs.py --dir <папка с документами meta/ и config/ (ArtifactData list/get с out_dir)> [--limit 240000] [--share 0.9]
Печатает документы крупнее share × limit по убыванию размера; код возврата 0 всегда — это предупреждение для сводки запуска.
"""
import argparse, glob, os


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dir", action="append", required=True)
    a.add_argument("--limit", type=int, default=240_000)
    a.add_argument("--share", type=float, default=0.9)
    x = a.parse_args()
    rows = []
    for d in x.dir:
        for f in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
            sz = os.path.getsize(f)
            if sz >= x.limit * x.share:
                rows.append((sz, os.path.relpath(f, d)))
    for sz, name in sorted(rows, reverse=True):
        print(f"{name}: {sz} байт ({round(100 * sz / x.limit)} % лимита)")
    print("предупреждение: документов у лимита — %d" % len(rows) if rows else "документов у лимита нет")


if __name__ == "__main__":
    main()
