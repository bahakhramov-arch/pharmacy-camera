# -*- coding: utf-8 -*-
"""Настройки смен, которые меняются без кода: сайт (вкладка «Настройки») и бот (/smena ...).

Хранятся в settings.json рядом с программой (в git не попадает - у каждого филиала свои).
Программа перечитывает файл, когда он меняется, - перезапуск не нужен.
"""

import json
import os
import re
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, "settings.json")

# Значения по умолчанию, пока settings.json ещё нет (аптека работает 08:00-23:00)
DEFAULTS = {
    "default_shift": "08:00",       # начало смены для всех, у кого нет своей
    "default_end": "23:00",         # конец смены по умолчанию
    "grace_minutes": 10,            # допуск: опоздание, если позже на > N минут
    "leave_timeout_min": 60,        # не виден дольше N минут = ушёл
    "shift_end_notify": True,       # в конце смены прислать итог по сотруднику в Telegram
    # своя смена: имя (как у фото, без регистра) -> {"start": "ЧЧ:ММ", "end": "ЧЧ:ММ"}
    "shifts": {"nurbonu": {"start": "08:00", "end": "23:00"}},
}

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}
_TIME = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")


def norm_time(s):
    """«8:5»/«08.05»/«0805» -> «08:05»; None, если это не время."""
    if s is None:
        return None
    s = str(s).strip()
    if re.fullmatch(r"\d{3,4}", s):
        s = s[:-2] + ":" + s[-2:]
    m = _TIME.match(s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def parse_range(s):
    """«08:00-23:00» -> ("08:00", "23:00"); «09:00» -> ("09:00", None); иначе None."""
    parts = re.split(r"\s*[-–—]\s*", str(s).strip())
    if len(parts) == 1:
        t = norm_time(parts[0])
        return (t, None) if t else None
    if len(parts) == 2:
        a, b = norm_time(parts[0]), norm_time(parts[1])
        return (a, b) if a and b else None
    return None


def _clean(data):
    out = {k: v for k, v in DEFAULTS.items() if k != "shifts"}
    out["shifts"] = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("default_shift", "default_end"):
        out[key] = norm_time(data.get(key, DEFAULTS[key])) or DEFAULTS[key]
    for key, lo, hi in (("grace_minutes", 0, 120), ("leave_timeout_min", 10, 600)):
        try:
            v = int(data.get(key, DEFAULTS[key]))
            out[key] = v if lo <= v <= hi else DEFAULTS[key]
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    out["shift_end_notify"] = bool(data.get("shift_end_notify", DEFAULTS["shift_end_notify"]))
    for name, val in (data.get("shifts") or {}).items():
        name = str(name).strip().lower()
        if not name:
            continue
        if isinstance(val, dict):          # {"start": .., "end": ..}
            start, end = norm_time(val.get("start")), norm_time(val.get("end"))
        else:                              # старый формат: просто «08:00»
            start, end = norm_time(val), None
        if start or end:
            out["shifts"][name] = {k: v for k, v in (("start", start), ("end", end)) if v}
    return out


def load():
    """Текущие настройки (с кэшем по времени изменения файла)."""
    with _lock:
        try:
            mtime = os.path.getmtime(PATH)
        except OSError:
            mtime = None
        if _cache["data"] is not None and mtime == _cache["mtime"]:
            return _cache["data"]
        data = DEFAULTS
        if mtime is not None:
            try:
                with open(PATH, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = DEFAULTS
        _cache.update(mtime=mtime, data=_clean(data))
        return _cache["data"]


def save(data):
    """Проверяет и сохраняет; возвращает то, что реально записано."""
    clean = _clean(data)
    with _lock:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
        _cache.update(mtime=None, data=None)
    return clean


def update(**changes):
    cur = load()
    new = {**cur, "shifts": {k: dict(v) for k, v in cur["shifts"].items()}}
    new.update(changes)
    return save(new)


def shift_range(name):
    """(начало, конец) смены сотрудника: своя или по умолчанию."""
    s = load()
    own = s["shifts"].get(name.lower(), {})
    return own.get("start", s["default_shift"]), own.get("end", s["default_end"])


def shift_for(name):
    return shift_range(name)[0]
