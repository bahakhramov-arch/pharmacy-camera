# -*- coding: utf-8 -*-
"""Заменить клиентов за день пересчитанными по видео (recount.csv).

    python fix_visitors.py recount.csv

Перед запуском закройте программу учёта. Скрипт:
  - делает копию visitors.csv -> visitors_backup_<время>.csv;
  - убирает записи за дату из recount.csv в промежутке, который он покрывает
    (от первого до последнего времени + запас до конца пересчёта, см. строку «# до» в файле);
  - вставляет пересчитанные записи и сортирует журнал по времени.
"""

import csv
import datetime as dt
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE_DIR, "visitors.csv")
HEADER = ["Дата", "Время", "Филиал", "Камера", "Событие"]


def read_rows(path):
    rows, until = [], None
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row:
                continue
            if row[0].startswith("# до"):        # «# до 2026-10-23 17:05:28»
                until = dt.datetime.strptime(row[0][5:].strip(), "%Y-%m-%d %H:%M:%S")
                continue
            if row[0] == "Дата" or len(row) < 5:
                continue
            rows.append(row[:5])
    return rows, until


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    new_rows, until = read_rows(sys.argv[1])
    if until is None:
        print("В файле нет строки «# до …» - это не файл пересчёта.")
        return
    since = until.replace(hour=0, minute=0, second=0)
    old_rows, _ = read_rows(LOG) if os.path.exists(LOG) else ([], None)

    def when(r):
        return dt.datetime.strptime(f"{r[0]} {r[1]}", "%Y-%m-%d %H:%M:%S")

    kept = [r for r in old_rows if not (since <= when(r) <= until)]
    removed = len(old_rows) - len(kept)
    if os.path.exists(LOG):
        backup = os.path.join(BASE_DIR, f"visitors_backup_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.csv")
        shutil.copy2(LOG, backup)
        print(f"Копия старого журнала: {backup}")
    rows = sorted(kept + new_rows, key=when)
    with open(LOG, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    n_in = sum(r[4] == "вход" for r in new_rows)
    n_out = sum(r[4] == "выход" for r in new_rows)
    print(f"Убрано старых записей за {since:%d.%m} до {until:%H:%M}: {removed}")
    print(f"Вставлено пересчитанных: вход {n_in}, выход {n_out}")
    print("Готово. Запустите программу учёта снова.")


if __name__ == "__main__":
    main()
