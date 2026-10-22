# -*- coding: utf-8 -*-
"""Подсчёт посетителей аптеки по линии у двери.

Детектор людей (YOLOX) находит фигуры, простой трекер ведёт каждую от кадра к кадру,
и когда человек пересекает линию двери - пишется «вход» или «выход» в visitors.csv.
Сотрудников (лицо распознано на этой же камере) в клиенты не записываем.
"""

import csv
import datetime as dt
import os
import threading

import cv2
import numpy as np

from reports import read_visitors, workday

PERSON_CLASS = 0  # COCO: person


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


class PersonDetector:
    """YOLOX (COCO): S - 640×640, точнее; Tiny - 416×416, в ~3 раза быстрее.
    Возвращает [(x, y, w, h, score), ...] в координатах всего кадра."""

    def __init__(self, model_path, score=0.45, nms=0.45, size=640):
        self.net = cv2.dnn.readNet(model_path)
        self.score = score
        self.nms = nms
        self.size = size
        grids, strides = [], []
        for s in (8, 16, 32):
            n = size // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), s))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def detect(self, img, roi=None):
        """roi - (x0, y0, x1, y1) в долях кадра: искать только там (у двери).
        Детектор получает меньшую область, значит, видит людей крупнее."""
        ox = oy = 0
        if roi:
            H, W = img.shape[:2]
            ox, oy = int(roi[0] * W), int(roi[1] * H)
            img = img[oy:int(roi[3] * H), ox:int(roi[2] * W)]
        h, w = img.shape[:2]
        r = min(self.size / h, self.size / w)
        canvas = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        nh, nw = int(h * r), int(w * r)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = canvas.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
        self.net.setInput(blob)
        out = self.net.forward()[0]

        scores = out[:, 4] * out[:, 5 + PERSON_CLASS]
        keep = scores >= self.score
        if not np.any(keep):
            return []
        out, scores = out[keep], scores[keep]
        st, gr = self.strides[keep], self.grids[keep]
        cxcy = (out[:, :2] + gr) * st / r
        wh = np.exp(out[:, 2:4]) * st / r
        boxes = np.concatenate((cxcy - wh / 2, wh), 1)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), self.score, self.nms)
        res = []
        for i in np.array(idx).flatten():
            x, y, bw, bh = boxes[i]
            x, y = max(0.0, x), max(0.0, y)
            bw, bh = min(bw, w - x), min(bh, h - y)
            if bw > 4 and bh > 4:
                res.append((int(x) + ox, int(y) + oy, int(bw), int(bh), float(scores[i])))
        return res


class DoorCounter:
    """Трекинг людей и подсчёт пересечений линии двери на одной камере.

    line   - отрезок ((x1, y1), (x2, y2)) в долях кадра (0..1) по порогу двери.
             Считается только переход через сам отрезок: прохожие мимо двери не идут в счёт;
    inside - любая точка (x, y) в долях кадра на стороне «внутри аптеки»;
    anchor - точка фигуры, по которой смотрим сторону: "bottom" (ноги, камера под углом)
             или "center" (камера сверху);
    margin - «мёртвая зона» вокруг линии в долях кадра, чтобы дрожание рамки
             на линии не давало лишних входов/выходов.
    """

    def __init__(self, camera, line, inside, anchor="bottom",
                 margin=0.02, max_lost_sec=1.5, min_hits=3,
                 stitch_sec=2.0, stitch_dist=0.5, near_door=0.2):
        """stitch_* - у двери детектор теряет человека (стекло, блики, близко к камере).
        Если трек пропал не дальше near_door (доля ширины кадра) от линии и в течение
        stitch_sec сек появился человек не дальше stitch_dist - это он же."""
        self.camera = camera
        self.line = line
        (x1, y1), (x2, y2) = line
        # +1, если «внутри» - слева от направления p1 -> p2 (в координатах кадра), иначе -1
        cross = (x2 - x1) * (inside[1] - y1) - (y2 - y1) * (inside[0] - x1)
        self.inside_sign = 1 if cross > 0 else -1
        self.anchor = anchor
        self.margin = margin
        self.max_lost_sec = max_lost_sec
        self.min_hits = min_hits
        self.stitch_sec = stitch_sec
        self.stitch_dist = stitch_dist
        self.near_door = near_door
        self.tracks = []
        self.next_id = 1
        # update() идёт в потоке двери, mark_staff()/draw() - в основном цикле
        self.lock = threading.RLock()
        self._now = None

    # --- геометрия
    def _side(self, pt, w, h):
        """+1 - сторона «внутри», -1 - «снаружи»,
        0 - в мёртвой зоне у линии или за концами отрезка (там не считаем)."""
        (x1, y1), (x2, y2) = self.line
        x1, x2, y1, y2 = x1 * w, x2 * w, y1 * h, y2 * h
        dx, dy = x2 - x1, y2 - y1
        len2 = dx * dx + dy * dy or 1.0
        t = ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / len2
        if not -0.05 <= t <= 1.05:
            return 0
        dist = (dx * (pt[1] - y1) - dy * (pt[0] - x1)) / len2 ** 0.5 * self.inside_sign
        if abs(dist) < self.margin * max(w, h):
            return 0
        return 1 if dist > 0 else -1

    def _door_dist(self, pt, w, h):
        """Расстояние от точки до отрезка двери в пикселях."""
        (x1, y1), (x2, y2) = self.line
        x1, x2, y1, y2 = x1 * w, x2 * w, y1 * h, y2 * h
        dx, dy = x2 - x1, y2 - y1
        t = ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / (dx * dx + dy * dy or 1.0)
        t = min(1.0, max(0.0, t))
        return ((pt[0] - x1 - t * dx) ** 2 + (pt[1] - y1 - t * dy) ** 2) ** 0.5

    def _point(self, box):
        x, y, w, h = box[:4]
        return (x + w / 2, y + h) if self.anchor == "bottom" else (x + w / 2, y + h / 2)

    # --- трекинг
    def _match(self, dets):
        """Жадное сопоставление: сначала по перекрытию рамок, потом по расстоянию."""
        pairs = []
        for ti, t in enumerate(self.tracks):
            tx, ty = self._point(t["box"])
            size = max(t["box"][2], t["box"][3])
            for di, d in enumerate(dets):
                iou = _iou(t["box"], d[:4])
                dx, dy = self._point(d)
                dist = ((dx - tx) ** 2 + (dy - ty) ** 2) ** 0.5
                if iou >= 0.2 or dist < 0.6 * size:
                    pairs.append((iou - dist / (size + 1e-6) * 0.1, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d, matches = set(), set(), []
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            matches.append((ti, di))
        return matches, [i for i in range(len(dets)) if i not in used_d]

    def update(self, dets, now, frame_w, frame_h):
        """dets - результат PersonDetector.detect. Возвращает список событий
        [(track, "вход"|"выход"), ...], включая проходы сотрудников (track["staff"])."""
        with self.lock:
            return self._update(dets, now, frame_w, frame_h)

    def _update(self, dets, now, frame_w, frame_h):
        self._now = now
        matches, new = self._match(dets)
        new = self._stitch(dets, new, matches, now, frame_w, frame_h)
        events = []
        for ti, di in matches:
            t = self.tracks[ti]
            t["box"] = dets[di][:4]
            t["last_seen"] = now
            t["hits"] += 1
            side = self._side(self._point(t["box"]), frame_w, frame_h)
            if side == 0:
                continue
            if not t["side"] or side == t["side"]:
                t["side"], t["pending"] = side, 0
                continue
            # Переход засчитываем, когда человека увидели на новой стороне дважды:
            # одиночная рамка (блик в стекле, чужой человек после склейки) не в счёт.
            t["pending"] += 1
            if t["pending"] >= 2:
                if t["hits"] >= self.min_hits:
                    events.append((t, "вход" if side > 0 else "выход"))
                t["side"], t["pending"] = side, 0
        for di in new:
            box = dets[di][:4]
            self.tracks.append({
                "id": self.next_id, "box": box, "last_seen": now, "hits": 1,
                "side": self._side(self._point(box), frame_w, frame_h), "pending": 0,
                "staff": None,
            })
            self.next_id += 1
        self.tracks = [t for t in self.tracks
                       if (now - t["last_seen"]).total_seconds()
                       <= (self.stitch_sec if t.get("near_door") else self.max_lost_sec)]
        for t in self.tracks:
            if t["last_seen"] == now:
                t["near_door"] = (self._door_dist(self._point(t["box"]), frame_w, frame_h)
                                  <= self.near_door * frame_w)
        return events

    def _stitch(self, dets, new, matches, now, w, h):
        """Новые рамки, которые продолжают пропавший у двери трек, добавляем в matches.
        Возвращает оставшиеся по-настоящему новые рамки."""
        used = {ti for ti, _ in matches}
        rest = []
        for di in new:
            px, py = self._point(dets[di])
            best, best_d = None, self.stitch_dist * w
            for ti, t in enumerate(self.tracks):
                gap = (now - t["last_seen"]).total_seconds()
                if ti in used or not t.get("near_door") or not 0 < gap <= self.stitch_sec:
                    continue
                tx, ty = self._point(t["box"])
                d = ((px - tx) ** 2 + (py - ty) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = ti, d
            if best is None:
                rest.append(di)
            else:
                used.add(best)
                matches.append((best, di))
        return rest

    def mark_staff(self, face_box, name):
        """Лицо сотрудника распознано: метим фигуру, в рамке которой это лицо."""
        fx, fy, fw, fh = face_box
        cx, cy = fx + fw / 2, fy + fh / 2
        with self.lock:
            return self._mark_staff(cx, cy, name)

    def _mark_staff(self, cx, cy, name):
        for t in self.tracks:
            x, y, w, h = t["box"]
            if x <= cx <= x + w and y <= cy <= y + h:
                t["staff"] = name
                return True
        return False

    def draw(self, frame):
        h, w = frame.shape[:2]
        (x1, y1), (x2, y2) = self.line
        p1, p2 = (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h))
        cv2.line(frame, p1, p2, (0, 255, 255), 2)
        mx, my = (p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2
        # нормаль к линии в сторону «внутри»
        nx, ny = -(p2[1] - p1[1]) * self.inside_sign, (p2[0] - p1[0]) * self.inside_sign
        k = 50 / ((nx * nx + ny * ny) ** 0.5 or 1)
        tip = (int(mx + nx * k), int(my + ny * k))
        cv2.arrowedLine(frame, (mx, my), tip, (0, 255, 255), 2, tipLength=0.4)
        cv2.putText(frame, "IN", (tip[0] + 5, tip[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        with self.lock:  # только видимые сейчас (пропавшие у двери ждут склейки - их не рисуем)
            visible = [dict(t) for t in self.tracks if t["last_seen"] == self._now]
        for t in visible:
            x, y, bw, bh = map(int, t["box"])
            color = (0, 200, 0) if t["staff"] else (255, 120, 0)
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
            cv2.putText(frame, f"#{t['id']}" + (" staff" if t["staff"] else ""),
                        (x, max(y - 8, 15)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


class VisitorLog:
    """Журнал visitors.csv и счётчики за текущий рабочий день."""

    def __init__(self, log_file, branch):
        self.log_file = log_file
        self.branch = branch
        self.lock = threading.Lock()
        self.entered = 0
        self.exited = 0

    def restore_today(self, day_start=0):
        today = workday(dt.datetime.now(), day_start)
        visits = [v for v in read_visitors(self.log_file)
                  if workday(v["when"], day_start) == today]
        with self.lock:
            self.entered = sum(1 for v in visits if v["type"] == "вход")
            self.exited = sum(1 for v in visits if v["type"] == "выход")
            return self.entered, self.exited

    def new_day(self):
        with self.lock:
            self.entered = self.exited = 0

    def add(self, when, camera, kind):
        with self.lock:
            if kind == "вход":
                self.entered += 1
            else:
                self.exited += 1
            new = not os.path.exists(self.log_file)
            with open(self.log_file, "a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["Дата", "Время", "Филиал", "Камера", "Событие"])
                w.writerow([when.date().isoformat(), when.strftime("%H:%M:%S"),
                            self.branch, camera, kind])

    def counts(self):
        """(зашло, вышло, сейчас внутри). «Внутри» - оценка: вход минус выход."""
        with self.lock:
            return self.entered, self.exited, max(0, self.entered - self.exited)
