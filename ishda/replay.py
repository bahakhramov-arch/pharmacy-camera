# -*- coding: utf-8 -*-
"""Прогон Ishda AI по записи с регистратора - проверка без живых камер.

    python ishda\\replay.py запись.dav папка_результата [кадров_в_секунду] [папка_фото]

Сохраняет кадры с рамками (каждые 10 с), итог по трекам и скорость обработки.
"""

import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from ishda.pipeline import CameraPipeline     # noqa: E402

COLORS = [(255, 0, 180), (0, 200, 255), (0, 220, 0), (255, 160, 0), (0, 0, 255), (200, 200, 0)]


def load_db(photos):
    import pharmacy_attendance as pa
    pa.PHOTOS_DIR = photos
    det = cv2.FaceDetectorYN.create(os.path.join(os.path.dirname(HERE), "face_detection_yunet_2023mar.onnx"),
                                    "", (640, 640), pa.DETECT_SCORE, 0.3, 5000)
    rec = cv2.FaceRecognizerSF.create(os.path.join(os.path.dirname(HERE), "face_recognition_sface_2021dec.onnx"), "")
    return pa.load_employees(det, rec)


def main():
    src, out = sys.argv[1], sys.argv[2]
    fps_target = float(sys.argv[3]) if len(sys.argv) > 3 else 5
    photos = sys.argv[4] if len(sys.argv) > 4 else os.path.join(os.path.dirname(HERE), "photos")
    os.makedirs(out, exist_ok=True)
    model = os.environ.get("HZ_MODEL", "yolox_tiny.onnx")
    size = int(os.environ.get("HZ_SIZE", "416"))
    roi = tuple(float(v) for v in os.environ["HZ_ROI"].split(",")) if os.environ.get("HZ_ROI") else None
    pipe = CameraPipeline(load_db(photos), person_model=model, person_size=size,
                          person_score=float(os.environ.get("HZ_SCORE", "0.35")), roi=roi)
    print("модель", model, size, "зона", roi)
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    fps = fps if 1 <= fps <= 60 else 25
    step = max(1, round(fps / fps_target))
    i, n, spent, next_snap = -1, 0, 0.0, 0.0
    seen = {}                      # track id -> [первое t, последнее t, имя]
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        if i % step:
            continue
        t = i / fps
        t0 = time.time()
        frame, res = pipe.step(frame, t)
        spent += time.time() - t0
        n += 1
        for tr, name, face in res:
            s = seen.setdefault(tr.id, [t, t, None])
            s[1], s[2] = t, name or s[2]
        if t >= next_snap:
            next_snap += 10
            img = frame.copy()
            for tr, name, face in res:
                x, y, w, h = map(int, tr.box)
                c = COLORS[tr.id % len(COLORS)]
                cv2.rectangle(img, (x, y), (x + w, y + h), c, 3)
                lbl = f"#{tr.id} {name or '?'} ({pipe.ident.evidence(tr.id)})"
                cv2.putText(img, lbl, (x, max(30, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 1.1, c, 3)
                if face is not None:
                    fx, fy, fw, fh = map(int, face[:4])
                    cv2.rectangle(img, (fx, fy), (fx + fw, fy + fh), (255, 255, 255), 2)
            cv2.putText(img, f"t={t:.0f}s", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
            cv2.imwrite(os.path.join(out, f"t{int(t):04d}.jpg"), cv2.resize(img, (960, int(img.shape[0] * 960 / img.shape[1]))))
    print(f"кадров {n}, {spent / max(n, 1) * 1000:.0f} мс на кадр -> до {n / max(spent, 1e-6):.1f} кадр/с на камеру")
    for tid, (a, b, name) in sorted(seen.items()):
        if b - a >= 2:
            print(f"трек #{tid}: {a:5.0f}-{b:5.0f} с ({b - a:4.0f} с)  {name or '?'}  лиц: {pipe.ident.evidence(tid)}")


if __name__ == "__main__":
    main()
