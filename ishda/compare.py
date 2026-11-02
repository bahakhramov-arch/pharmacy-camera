# -*- coding: utf-8 -*-
"""Сверка: старая система и Ishda AI за день, по каждому сотруднику.

    python ishda\\compare.py 2026-11-02 [номер_филиала_в_панели] [папка_результата]

Берёт оба журнала через панель (логин admin, пароль - ISHDA_PANEL_PASSWORD в окружении),
находит отрезки, где системы расходятся дольше 10 минут, и на середине каждого отрезка
вытаскивает кадр с камер сотрудников - чтобы глазами решить, кто прав.
"""

import datetime as dt
import json
import os
import sys

import requests

PANEL = os.environ.get("ISHDA_PANEL", "https://dostlik.5plyus.uz/panel")
AUTH = ("admin", os.environ.get("ISHDA_PANEL_PASSWORD", ""))
MIN_GAP = dt.timedelta(minutes=10)


def api(bid, path, **kw):
    r = requests.get(f"{PANEL}/b/{bid}/api/{path}", auth=AUTH, timeout=120, **kw)
    r.raise_for_status()
    return r


def intervals(events, day_end):
    """[(время, имя, приход/уход)] -> {имя: [(начало, конец)]} - когда человек на месте."""
    out, open_ = {}, {}
    for t, name, kind in sorted(events):
        if kind == "приход" and name not in open_:
            open_[name] = t
        elif kind == "уход" and name in open_:
            out.setdefault(name, []).append((open_.pop(name), t))
    for name, t in open_.items():
        out.setdefault(name, []).append((t, day_end))
    return out


def present(iv, t):
    return any(a <= t < b for a, b in iv)


def disagreements(a, b, start, end, step=dt.timedelta(minutes=1)):
    """Отрезки, где одна система говорит «на месте», а другая - нет."""
    res, cur, t = [], None, start
    while t < end:
        pa_, pb_ = present(a, t), present(b, t)
        state = None if pa_ == pb_ else ("старая: на месте", "Ishda: на месте")[pb_]
        if state != (cur[2] if cur else None):
            if cur and t - cur[0] >= MIN_GAP:
                res.append((cur[0], t, cur[2]))
            cur = (t, None, state) if state else None
        t += step
    if cur and end - cur[0] >= MIN_GAP:
        res.append((cur[0], end, cur[2]))
    return res


def main():
    day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    bid = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    out = sys.argv[3] if len(sys.argv) > 3 else f"compare_{day}"
    os.makedirs(out, exist_ok=True)
    ov = api(bid, "overview").json()
    now = dt.datetime.now()
    day_end = min(now, dt.datetime.combine(day, dt.time(23, 59, 59)))
    old = [(dt.datetime.fromisoformat(f"{e['date']}T{e['time']}"), e["name"], e["type"])
           for e in api(bid, f"events?day={day}").json()["events"]]
    try:                                        # полный журнал Ishda (новые версии программы)
        ish = [(dt.datetime.fromisoformat(f"{e['date']}T{e['time']}"), e["name"], e["type"])
               for e in api(bid, f"ishda_events?day={day}").json()["events"]]
        since = None
    except requests.HTTPError:                 # старая версия: только события с последнего запуска
        st = (ov.get("system") or {}).get("ishda") or {}
        ish = [(dt.datetime.combine(day, dt.time.fromisoformat(t)), n, k) for t, n, k in st.get("events", [])]
        since = dt.datetime.fromisoformat(ov["system"]["started"])
    a, b = intervals(old, day_end), intervals(ish, day_end)
    start = since or dt.datetime.combine(day, dt.time(0, 0))
    report = {"day": str(day), "branch": ov["branch"], "from": str(start), "to": str(day_end), "people": {}}
    for name in sorted(set(a) | set(b)):
        diffs = disagreements(a.get(name, []), b.get(name, []), start, day_end)
        report["people"][name] = [{"from": f"{x:%H:%M}", "to": f"{y:%H:%M}", "who": s} for x, y, s in diffs]
        for x, y, s in diffs:
            mid = x + (y - x) / 2
            for ch in (5, 6):
                if mid > now - dt.timedelta(minutes=2):
                    r = api(bid, f"snap?ch={ch}")
                else:
                    r = api(bid, f"record?ch={ch}&start={mid:%Y-%m-%dT%H:%M}&minutes=0.2", stream=True)
                ext = "jpg" if "image" in r.headers.get("content-type", "") else "dav"
                path = os.path.join(out, f"{name}_{mid:%H%M}_cam{ch}.{ext}")
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                if ext == "dav":                # из записи - один кадр
                    import cv2
                    cap = cv2.VideoCapture(path)
                    ok, frame = cap.read()
                    cap.release()
                    if ok:
                        cv2.imwrite(path[:-4] + ".jpg", frame)
                    os.remove(path)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
