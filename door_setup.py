# -*- coding: utf-8 -*-
"""Настройка линии двери для подсчёта клиентов (камеры VISITOR_CHANNELS).

Запуск:  python door_setup.py
В окне с кадром камеры:
  1-й и 2-й клик - концы линии по порогу двери (поперёк прохода, только ширина двери);
  3-й клик       - любая точка ВНУТРИ аптеки (по ту сторону линии от улицы);
  S - сохранить,  R - начать заново,  N - свежий кадр,  Q / Esc - пропустить камеру.
Результат пишется в door_lines.json, основная программа читает его при запуске.
"""

import cv2

import pharmacy_attendance as pa
from visitors import DoorCounter

VIEW_WIDTH = 1280


def grab(source):
    cap = pa.open_capture(source)
    frame = None
    if cap.isOpened():
        for _ in range(5):  # первые кадры RTSP бывают серыми
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
    cap.release()
    if frame is None:
        return None
    h, w = frame.shape[:2]
    return cv2.resize(frame, (VIEW_WIDTH, int(h * VIEW_WIDTH / w)))


def setup_camera(name, source, saved):
    frame = grab(source)
    if frame is None:
        print(f"[{name}] кадр не получен - проверьте камеру и пароль в secrets.env")
        return
    h, w = frame.shape[:2]
    cfg = saved.get(name)
    pts = [tuple(p) for p in cfg["line"]] + [tuple(cfg["inside"])] if cfg else []

    def on_click(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 3:
            pts.append((x / w, y / h))

    win = pa.ascii_label(name)  # заголовок окна OpenCV на Windows не умеет кириллицу
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_click)
    while True:
        view = frame.copy()
        if len(pts) == 3:
            DoorCounter(name, pts[:2], pts[2]).draw(view)
            hint = "S - save, R - reset, N - new frame, Q - skip"
        else:
            for p in pts:
                cv2.circle(view, (int(p[0] * w), int(p[1] * h)), 6, (0, 255, 255), -1)
            hint = ["click 1st end of door line", "click 2nd end of door line",
                    "click a point INSIDE the pharmacy"][len(pts)]
        cv2.putText(view, hint, (15, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4)
        cv2.putText(view, hint, (15, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        cv2.imshow(win, view)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("r"), ord("R")):
            pts.clear()
        elif key in (ord("n"), ord("N")):
            fresh = grab(source)
            if fresh is not None:
                frame = fresh
        elif key in (ord("s"), ord("S")) and len(pts) == 3:
            saved[name] = {"line": [list(pts[0]), list(pts[1])], "inside": list(pts[2])}
            pa.save_json(pa.DOOR_LINES_FILE, saved)
            print(f"[{name}] линия сохранена в {pa.DOOR_LINES_FILE}")
            break
        elif key in (ord("q"), ord("Q"), 27):
            print(f"[{name}] пропущена, без изменений")
            break
    cv2.destroyWindow(win)


def main():
    if not pa._is_filled(pa.NVR_PASSWORD) and not pa.CUSTOM_CAMERAS:
        print("Не заполнен NVR_PASSWORD в secrets.env")
        return
    saved = pa.load_json(pa.DOOR_LINES_FILE, {})
    cameras = pa.build_cameras()
    for ch in pa.VISITOR_CHANNELS:
        name = pa.camera_label(ch)
        if name in cameras:
            setup_camera(name, cameras[name], saved)


if __name__ == "__main__":
    main()
