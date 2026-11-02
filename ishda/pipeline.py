# -*- coding: utf-8 -*-
"""Ishda AI: одна камера = фигуры (детектор людей) + слежение + лица, приклеенные к фигурам.

Отличие от старой системы: присутствие определяется по фигуре, которую видно почти всегда,
а лицо нужно лишь несколько раз, чтобы узнать, кто это.
"""

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from visitors import PersonDetector            # noqa: E402  YOLOX, уже используется у двери

from ishda.identity import Identity           # noqa: E402
from ishda.tracker import Tracker             # noqa: E402

MAX_W = 1920


def face_scores(feature, db, recognizer):
    """{имя: лучшее сходство с его фото}."""
    return {n: max(recognizer.match(feature, f, cv2.FaceRecognizerSF_FR_COSINE) for f in fs)
            for n, fs in db.items()}


class CameraPipeline:
    def __init__(self, db, person_model="yolox_tiny.onnx", person_size=416, person_score=0.35,
                 face_min_width=30, face_min_score=0.6, inherit_sec=20.0, identity=None, roi=None):
        """roi - (x0, y0, x1, y1) в долях кадра: детектор людей смотрит только туда (зона прилавка),
        люди там крупнее - сотрудник, у которого из-за прилавка видны только голова и плечи, находится."""
        self.roi = roi
        self.db = db
        self.persons = PersonDetector(os.path.join(ROOT, person_model), score=person_score,
                                      size=person_size)
        self.faces = cv2.FaceDetectorYN.create(os.path.join(ROOT, "face_detection_yunet_2023mar.onnx"),
                                               "", (640, 640), 0.35, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(os.path.join(ROOT, "face_recognition_sface_2021dec.onnx"), "")
        self.tracker = Tracker()
        self.ident = identity or Identity()
        self.face_min_width = face_min_width
        self.face_min_score = face_min_score
        self.inherit_sec = inherit_sec
        self.ended = []          # [(время конца, рамка, track id)] - для наследования имени
        self.checked = {}        # track id -> когда последний раз считали его лицо
        self.recheck_sec = 10.0  # уже узнанного перепроверяем не чаще (лица - главная нагрузка)
        self.alive = {}          # track id -> Track

    def _faces(self, frame):
        pa = sys.modules.get("__main__")      # внутри программы учёта - она сама
        if not hasattr(pa, "detect_faces"):
            import pharmacy_attendance as pa   # прогон по записи (replay.py)
        faces = pa.nms_faces(pa.detect_faces(self.faces, frame, thorough=False))
        return [] if faces is None else list(faces)

    def step(self, frame, t):
        """Кадр -> [(track, имя или None, лицо или None)]. t - секунды."""
        h, w = frame.shape[:2]
        if w > MAX_W:
            frame = cv2.resize(frame, (MAX_W, int(h * MAX_W / w)))
        tracks = self.tracker.update(self.persons.detect(frame, self.roi), t)

        # треки, которые пропали: запоминаем, чтобы новый трек на том же месте получил имя
        ids = {tr.id for tr in self.tracker.tracks}
        for tid in list(self.alive):
            if tid not in ids:
                tr = self.alive.pop(tid)
                self.ended.append((tr.last_t, tr.box.copy(), tid))
        self.ended = [e for e in self.ended if t - e[0] <= self.inherit_sec]
        for tr in tracks:
            if tr.id not in self.alive:
                self.alive[tr.id] = tr
                self._try_inherit(tr)

        # лица нужны только трекам без имени или давно не проверенным
        need = {tr.id for tr in tracks
                if self.ident.name(tr.id) is None or t - self.checked.get(tr.id, -1e9) >= self.recheck_sec}
        out_faces = {}
        for face in (self._faces(frame) if need else []):
            fx, fy, fw, fh = face[:4]
            if fw < self.face_min_width or face[-1] < self.face_min_score:
                continue
            cx, cy = fx + fw / 2, fy + fh / 2
            owner = None
            for tr in tracks:
                x, y, bw, bh = tr.box
                if x <= cx <= x + bw and y <= cy <= y + bh * 0.5:
                    if owner is None or bw * bh < owner.box[2] * owner.box[3]:
                        owner = tr
            if owner is None or owner.id not in need:
                continue
            try:
                feat = self.rec.feature(self.rec.alignCrop(frame, face))
            except cv2.error:
                continue
            self.ident.add(owner.id, face_scores(feat, self.db, self.rec))
            out_faces[owner.id] = face
            if self.ident.name(owner.id):
                self.checked[owner.id] = t
        return frame, [(tr, self.ident.name(tr.id), out_faces.get(tr.id)) for tr in tracks]

    def _try_inherit(self, tr):
        best = None
        for t_end, box, tid in self.ended:
            if self.ident.name(tid) is None:
                continue
            c1 = box[:2] + box[2:] / 2
            dist = np.linalg.norm(tr.center() - c1)
            if dist <= max(box[2], tr.box[2]) * 1.0 and (best is None or dist < best[0]):
                best = (dist, tid)
        if best:
            self.ident.inherit(tr.id, best[1])
