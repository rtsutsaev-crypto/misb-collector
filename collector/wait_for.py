"""Ждёт фоновый шаг сбора не дольше одного отрезка, чтобы между отрезками обновлять progress/current.

Запуск: python3 wait_for.py FILE [FILE …] [--max 120] [--log фоновый.log]
Возвращает 0, когда все FILE появились; 3, если отрезок истёк, а файлов ещё нет (тогда: progress.py stage … и запись
progress/current, затем снова wait_for.py). С --log печатает последние строки журнала фонового процесса.
"""
import argparse, os, sys, time


def main():
    a = argparse.ArgumentParser()
    a.add_argument("files", nargs="+")
    a.add_argument("--max", type=int, default=120)
    a.add_argument("--log")
    x = a.parse_args()
    end = time.time() + max(1, min(x.max, 170))
    while time.time() < end:
        if all(os.path.exists(f) and os.path.getsize(f) > 0 for f in x.files):
            print("готово: " + ", ".join(x.files))
            return 0
        time.sleep(5)
    missing = [f for f in x.files if not (os.path.exists(f) and os.path.getsize(f) > 0)]
    print("ещё идёт, нет: " + ", ".join(missing))
    if x.log and os.path.exists(x.log):
        tail = open(x.log, encoding="utf-8", errors="ignore").read().splitlines()[-3:]
        for line in tail:
            print("  " + line[:200])
    return 3


if __name__ == "__main__":
    sys.exit(main())
