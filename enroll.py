# -*- coding: utf-8 -*-
"""Добавить сотруднику фото с камеры учёта - так распознавание становится надёжнее:
на обычном фото лицо крупное и анфас, а камера видит людей сверху и издалека.

    python enroll.py Zufar          (20 секунд, первая камера сотрудников из STAFF_CHANNELS)
    python enroll.py Zufar 30 5     (30 секунд, камера 5)

Пока идёт запись, перед камерой должен быть ТОЛЬКО этот сотрудник: пусть постоит
и посмотрит в камеру, немного поворачивая голову. Лучшие кадры лица сохранятся
в photos/Имя_cam1.jpg, Имя_cam2.jpg ... Потом перезапустите программу учёта.
"""

import os
import sys
import time

import cv2

import pharmacy_attendance as pa


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    name = sys.argv[1].strip()
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    ch = int(sys.argv[3]) if len(sys.argv) > 3 else pa.STAFF_CHANNELS[0]
    url = pa.build_cameras().get(pa.camera_label(ch))
    if not url:
        print(f"Камера {ch} не настроена (STAFF_CHANNELS / VISITOR_CHANNELS в secrets.env)")
        return
    det = cv2.FaceDetectorYN.create("face_detection_yunet_2023mar.onnx", "", (640, 640),
                                    pa.DETECT_SCORE, 0.3, 5000)
    cap = pa.open_capture(url)
    if not cap.isOpened():
        print("Камера не открывается - проверьте сеть и пароль регистратора")
        return
    print(f"Запись {secs:.0f} с с камеры {ch}: перед камерой только {name}, смотрит в неё...")
    found, t0, n = [], time.time(), 0
    while time.time() - t0 < secs:
        ok, f = cap.read()
        if not ok:
            continue
        n += 1
        if n % 5:
            continue
        h, w = f.shape[:2]
        if w > pa.MAX_FRAME_WIDTH:
            f = cv2.resize(f, (pa.MAX_FRAME_WIDTH, int(h * pa.MAX_FRAME_WIDTH / w)))
        faces = pa.nms_faces(pa.detect_faces(det, f, thorough=False))
        if faces is None or len(faces) == 0:
            continue
        face = max(faces, key=lambda x: x[2] * x[3])        # самое крупное лицо в кадре
        if face[2] < pa.MIN_FACE_WIDTH:
            continue
        x, y, bw, bh = map(int, face[:4])
        pad = int(bw * 0.8)
        crop = f[max(0, y - pad):y + bh + pad, max(0, x - pad):x + bw + pad].copy()
        found.append((float(face[-1]) * bw, time.time(), crop))   # уверенность × размер
    cap.release()
    if not found:
        print("Лицо не найдено. Пусть подойдёт ближе к камере и посмотрит прямо в неё.")
        return
    # 4 лучших кадра, разнесённых по времени (разные повороты головы)
    found.sort(key=lambda r: -r[0])
    picked = []
    for r in found:
        if all(abs(r[1] - p[1]) > 1.5 for p in picked):
            picked.append(r)
        if len(picked) == 4:
            break
    os.makedirs(pa.PHOTOS_DIR, exist_ok=True)
    k = 1
    for _, _, crop in picked:
        while os.path.exists(os.path.join(pa.PHOTOS_DIR, f"{name}_cam{k}.jpg")):
            k += 1
        cv2.imencode(".jpg", crop)[1].tofile(os.path.join(pa.PHOTOS_DIR, f"{name}_cam{k}.jpg"))
        k += 1
    print(f"Сохранено фото: {len(picked)} -> {pa.PHOTOS_DIR}\\{name}_cam*.jpg. Перезапустите программу.")


if __name__ == "__main__":
    main()
