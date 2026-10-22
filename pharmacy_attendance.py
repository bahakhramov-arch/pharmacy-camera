# -*- coding: utf-8 -*-
"""
Учёт сотрудников аптеки по камерам.
Лицо -> приход / уход -> журнал events.csv -> Telegram -> Excel-отчёты.

Установка:  pip install -r requirements.txt
Запуск:     python pharmacy_attendance.py
Рядом должны лежать: reports.py, secrets.env (токен, chat id, пароль NVR),
папка photos/ с фото сотрудников.

Как определяется уход: если человека не видела ни одна камера дольше
LEAVE_TIMEOUT_MIN минут, фиксируется «уход» ровно в момент, когда его
видели в последний раз. Если он снова появился - это новый «приход»
(время перерыва в отработанные часы не входит).

Команды бота (работают только в вашем чате):
  /today /visitors /day [дата] /yesterday /week /prevweek /month /prevmonth /help

Клиенты: на камерах VISITOR_CHANNELS детектор людей (YOLOX) ведёт каждого человека,
и при пересечении линии у двери (DOOR_LINE) пишется «вход» или «выход» в visitors.csv.

Сайт отчётов (посетители и сотрудники): http://127.0.0.1:8080
"""

import os

# Все файлы (журнал, фото, отчёты) - рядом со скриптом, откуда бы его ни запустили.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

import csv
import datetime as dt
import json
import queue
import sys
import threading
import time
import urllib.request
from urllib.parse import quote

import cv2
import numpy as np
import requests

from reports import (build_days, make_report, month_range, read_events,
                     read_visitors, visitor_stats, week_range, workday)
from visitors import DoorCounter, PersonDetector, VisitorLog
from web_reports import start_reports_server

# Эмодзи и кириллица в print не должны ронять программу на консоли Windows / при записи в файл.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_env_file(path="secrets.env"):
    """Читает строки КЛЮЧ=значение из файла (без сторонних библиотек).
    Переменные окружения, заданные в системе, имеют приоритет."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()

# =====================================================================
#                             НАСТРОЙКИ
# =====================================================================

# --- Секреты: берутся из secrets.env или из переменных окружения ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
NVR_PASSWORD = os.environ.get("NVR_PASSWORD", "")

BRANCH = "Достлик"

# --- Камеры: только NVR по RTSP. Веб-камера ноутбука не используется. ---
NVR_IP = "192.168.1.99"
NVR_PORT = 554
NVR_LOGIN = "admin"
STAFF_CHANNELS = [5]                   # учёт сотрудников
VISITOR_CHANNELS = [1]                 # подсчёт клиентов (вход)
CHANNELS = sorted(set(STAFF_CHANNELS + VISITOR_CHANNELS))
STREAM_TYPE = 0                        # 0 - основной поток, 1 - облегчённый (меньше нагрузка)
CUSTOM_CAMERAS = {}

# --- Смены и опоздания ---
DEFAULT_SHIFT = "09:00"
SHIFTS = {
    "nurbonu": "08:00",                # смена 08:00-23:00
    # "Азиза Каримова": "08:00",
    # "Ночной сотрудник": "21:00",     # ночные смены поддерживаются
}
GRACE_MINUTES = 5                      # допуск: опоздание считается, если позже на > N минут

# --- Рабочий день ---
# Час, когда начинается новый рабочий день. 0 = полночь.
# Если есть ночные смены, поставьте час, когда в аптеке точно никто не работает
# (например 5): тогда работа в 02:00 засчитается в предыдущий день.
DAY_START_HOUR = 0

# --- Уход и перерывы ---
LEAVE_TIMEOUT_MIN = 30                 # не виден дольше N минут = ушёл
SEND_RETURN_MESSAGES = True            # писать «вернулся» после перерыва

# --- Посетители: вход / выход по линии у двери (камеры VISITOR_CHANNELS) ---
COUNT_VISITORS = True
VISITORS_FILE = "visitors.csv"
# Линия - отрезок по порогу двери в долях кадра ((0, 0) - левый верхний угол,
# (1, 1) - правый нижний) и точка на стороне «внутри аптеки».
# Проще всего задать мышкой:  python door_setup.py  (сохранит в DOOR_LINES_FILE).
# В окне камеры линия рисуется жёлтым, стрелка IN смотрит внутрь.
DOOR_LINES_FILE = "door_lines.json"
DOOR_DEFAULT = {"line": [[0.60, 0.62], [0.80, 1.00]],   # примерно: порог двери на камере 1
                "inside": [0.90, 0.75]}                 # (улица слева, аптека справа)
DOOR_ANCHOR = "bottom"                 # "bottom" - ноги (камера под углом), "center" - камера сверху
PERSON_SCORE = 0.45                    # уверенность детектора людей
PERSON_MODEL = "tiny"                  # "tiny" - для ноутбука (i5 U-серии), ~3x быстрее;
                                       # "s" - точнее, если компьютер мощнее (door fps 5)
DOOR_ROI = (0.45, 0.2, 1.0, 1.0)       # для "tiny": искать людей только у двери (x0, y0, x1, y1
                                       # в долях кадра) - так фигура крупнее; "s" смотрит весь кадр
DOOR_DETECT_INTERVAL_SEC = 0.2         # ~5 кадров/сек на камеру двери

# --- Отчёты ---
REPORTS_DIR = "reports"
AUTO_REPORT_TIME = "08:00"             # понедельник: отчёт за прошлую неделю,
                                       # 1-е число: за прошлый месяц
DAILY_SUMMARY_TIME = "21:00"           # итог дня в Telegram ("" = выключить)
REPORTS_WEB = True                     # сайт отчётов в браузере
REPORTS_HOST = os.environ.get("REPORTS_HOST", "0.0.0.0")
REPORTS_PORT = int(os.environ.get("REPORTS_PORT", "8080"))

# --- Распознавание ---
PHOTOS_DIR = "photos"
MATCH_THRESHOLD = 0.32                 # чуть мягче официальных 0.363: камера ≠ селфи
MATCH_MARGIN = 0.06                    # лучший кандидат должен опережать второго на столько,
                                       # иначе похожих сотрудников путает (baha/nurbonu ≈ 0.27)
CONFIRM_FRAMES = 3                     # столько анализов подряд один и тот же человек
MIN_FACE_WIDTH = 36                    # меньше - SFace даёт случайные совпадения, только рамка
MAX_FRAME_WIDTH = 1920
# YuNet обучен на 640×640. Фиксированный letterbox стабильнее, чем каждый раз
# другой размер (на OpenCV 5 смена input size сбивает детектор на живом видео).
# Крупный план (лицо на весь кадр) ловим вторым проходом с полями.
DETECT_SCORE = 0.35
YUNET_SIZE = 640
DETECT_INTERVAL_SEC = 0.15             # анализировать не чаще, чем раз в N сек на камеру
                                       # (снижает нагрузку на процессор; 0 = каждый кадр)

# --- Прочее ---
LOG_FILE = "events.csv"
LAST_SEEN_FILE = "last_seen.json"
REPORT_STATE_FILE = "report_state.json"
SHOW_WINDOW = True                     # False = работать без окна (для фонового запуска)
WINDOW_TRANSLIT = False                # True = в окне писать имена латиницей (OpenCV не рисует кириллицу); на журнал/Telegram не влияет
SEND_START_MESSAGE = True

# =====================================================================

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
          "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]

MODELS = {
    "face_detection_yunet_2023mar.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
# имя -> (файл, ссылка, размер входа, искать только в DOOR_ROI).
# На записях у двери: s по всему кадру 6/6 проходов, tiny в DOOR_ROI 5/6, s в DOOR_ROI хуже (4/6).
PERSON_MODELS = {
    "s": ("object_detection_yolox_2022nov.onnx",
          "https://github.com/opencv/opencv_zoo/raw/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx",
          640, False),
    "tiny": ("yolox_tiny.onnx",
             "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
             416, True),
}


# ------------------------- Вспомогательное -------------------------

def _safe(err):
    """Текст ошибки без токена бота (в URL запроса он виден)."""
    s = str(err)
    return s.replace(BOT_TOKEN, "***") if BOT_TOKEN else s


def _is_filled(value):
    return bool(value) and "ВСТАВЬТЕ" not in value


def camera_label(ch):
    return f"Камера {ch}"


def build_cameras():
    if CUSTOM_CAMERAS:
        return dict(CUSTOM_CAMERAS)
    cams = {}
    pwd = quote(NVR_PASSWORD, safe="")
    login = quote(NVR_LOGIN, safe="")
    for ch in CHANNELS:
        cams[camera_label(ch)] = (f"rtsp://{login}:{pwd}@{NVR_IP}:{NVR_PORT}"
                                  f"/cam/realmonitor?channel={ch}&subtype={STREAM_TYPE}")
    return cams


def download_models():
    path, url, _, _ = PERSON_MODELS[PERSON_MODEL]
    for path, url in list(MODELS.items()) + ([(path, url)] if COUNT_VISITORS else []):
        if not os.path.exists(path):
            print(f"Скачиваю модель {path} ...")
            tmp = path + ".part"
            # Через временный файл: оборванная загрузка не оставит битую модель.
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, path)


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m",
                      "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "sch",
                      "", "y", "", "e", "yu", "ya"]))


def ascii_label(text):
    """cv2.putText не умеет кириллицу (рисует «???») - для надписей в окне транслитерируем."""
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low else t)
        else:
            out.append(ch if ord(ch) < 128 else "?")
    return "".join(out)


def read_image(path):
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def fmt_dur(sec):
    m = int(round(sec / 60))
    return f"{m // 60} ч {m % 60:02d} мин"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print("Не удалось сохранить", path, e)


# ------------------------- Распознавание -------------------------

def load_employees(detector, recognizer):
    db = {}
    if not os.path.isdir(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"Создана папка {PHOTOS_DIR}. Положите туда фото сотрудников.")
        return db
    for fname in sorted(os.listdir(PHOTOS_DIR)):
        if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        name = os.path.splitext(fname)[0].split("_")[0].strip()
        img = read_image(os.path.join(PHOTOS_DIR, fname))
        if img is None:
            print(f"  ! Не удалось открыть {fname}")
            continue
        faces = detect_faces(detector, img)
        if faces is None or len(faces) == 0:
            print(f"  ! На фото {fname} лицо не найдено, пропускаю "
                  f"(другое фото анфас, светлее, без фильтра)")
            continue
        face = max(faces, key=lambda f: f[2] * f[3])
        try:
            aligned = recognizer.alignCrop(img, face)
            if aligned is None or aligned.size == 0:
                print(f"  ! На фото {fname} не удалось выровнять лицо, пропускаю")
                continue
            feature = recognizer.feature(aligned)
        except Exception as e:
            print(f"  ! На фото {fname} сбой распознавания: {e}")
            continue
        db.setdefault(name, []).append(feature)
    summary = ", ".join(f"{n} ({len(f)} фото)" for n, f in db.items()) or "-"
    print(f"Загружено сотрудников: {len(db)} -> {summary}")
    return db


def _letterbox(img, size=YUNET_SIZE):
    h, w = img.shape[:2]
    scale = min(size / float(w), size / float(h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    return canvas, scale


def _clahe_bgr(img):
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def _yunet_on(detector, img, score):
    work, scale = _letterbox(img)
    detector.setScoreThreshold(score)
    detector.setInputSize((YUNET_SIZE, YUNET_SIZE))
    try:
        _, faces = detector.detect(work)
    except cv2.error:
        return None
    if faces is None or len(faces) == 0:
        return None
    faces = faces.copy()
    faces[:, :4] /= scale
    faces[:, 4:14] /= scale
    return faces


def detect_faces(detector, img, thorough=True):
    """Ищет лица: letterbox 640×640, затем (если thorough) крупный план (поля) и тёмный кадр.
    Для живого видео thorough включают лишь изредка: на пустом кадре дополнительные
    проходы утраивают нагрузку на процессор."""
    if img is None or img.size == 0:
        return None
    faces = _yunet_on(detector, img, DETECT_SCORE)
    if faces is not None or not thorough:
        return faces
    h, w = img.shape[:2]
    pad = int(max(h, w) * 0.5)
    padded = cv2.copyMakeBorder(img, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=(114, 114, 114))
    faces = _yunet_on(detector, padded, DETECT_SCORE)
    if faces is not None:
        faces = faces.copy()
        faces[:, 0] -= pad
        faces[:, 1] -= pad
        faces[:, 4:14:2] -= pad
        faces[:, 5:14:2] -= pad
        return faces
    return _yunet_on(detector, _clahe_bgr(img), max(0.2, DETECT_SCORE - 0.1))


def identify(feature, db, recognizer):
    per_name = {}
    for name, features in db.items():
        per_name[name] = max(recognizer.match(feature, f, cv2.FaceRecognizerSF_FR_COSINE)
                             for f in features)
    if not per_name:
        return None, 0.0
    ranked = sorted(per_name.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score >= MATCH_THRESHOLD and best_score - second >= MATCH_MARGIN:
        return best_name, best_score
    return None, best_score


def nms_faces(faces, iou_thr=0.45):
    """Убирает дубли рамок YuNet (одно лицо — несколько детектов)."""
    if faces is None or len(faces) == 0:
        return faces
    order = sorted(range(len(faces)), key=lambda i: faces[i][2] * faces[i][3], reverse=True)
    keep = []
    for i in order:
        x, y, w, h = faces[i][:4]
        a1, a2, a3, a4 = x, y, x + w, y + h
        ok = True
        for j in keep:
            xx, yy, ww, hh = faces[j][:4]
            ix1, iy1 = max(a1, xx), max(a2, yy)
            ix2, iy2 = min(a3, xx + ww), min(a4, yy + hh)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            union = w * h + ww * hh - inter
            if union and inter / union >= iou_thr:
                ok = False
                break
        if ok:
            keep.append(i)
    return faces[keep]


# ------------------------- Telegram -------------------------

def _post(method, **kw):
    for _ in range(3):
        try:
            r = requests.post(f"{API}/{method}", timeout=60, **kw)
            if r.ok:
                return True
            print("Ошибка Telegram:", r.text)
            if r.status_code in (400, 401, 403, 404):
                return False
        except Exception as e:
            print("Telegram недоступен:", _safe(e))
        time.sleep(3)
    return False


_tg_q = queue.Queue()
_tg_worker_started = False
_tg_worker_lock = threading.Lock()


def _tg_worker():
    while True:
        job = _tg_q.get()
        if job is None:
            return
        try:
            job()
        except Exception as e:
            print("Ошибка Telegram:", _safe(e))


def _ensure_tg_worker():
    global _tg_worker_started
    with _tg_worker_lock:
        if not _tg_worker_started:
            threading.Thread(target=_tg_worker, daemon=True).start()
            _tg_worker_started = True


def send_telegram(text, wait=False):
    """wait=True — дождаться отправки (старт системы, ответы на команды)."""
    done = threading.Event()
    ok_box = [False]

    def job(t=text):
        ok_box[0] = _post("sendMessage", data={"chat_id": CHAT_ID, "text": t})
        if not ok_box[0]:
            print("Не отправилось в Telegram:", t[:120])
        done.set()

    if wait:
        job()
        return ok_box[0]
    _ensure_tg_worker()
    _tg_q.put(job)
    return True


def _send_doc(path, caption):
    # Файл читаем в память целиком: при повторной попытке отправки
    # открытый файловый объект был бы уже «дочитан» и ушёл бы пустым.
    with open(path, "rb") as f:
        content = f.read()
    _post("sendDocument", data={"chat_id": CHAT_ID, "caption": caption},
          files={"document": (os.path.basename(path), content)})


def check_telegram():
    """Проверяет токен и что бот может писать в CHAT_ID."""
    try:
        r = requests.get(f"{API}/getMe", timeout=20)
        data = r.json()
    except Exception as e:
        print("Telegram недоступен:", _safe(e))
        return False
    if not r.ok or not data.get("ok"):
        print("Неверный BOT_TOKEN. Ответ:", data)
        return False
    uname = data["result"].get("username", "")
    print(f"Telegram бот @{uname} отвечает")
    ok = send_telegram("🟢 Проверка связи: бот работает", wait=True)
    if not ok:
        print("Бот не смог написать в CHAT_ID. Откройте чат с ботом и нажмите Start / /start.")
        return False
    return True


# ------------------------- Учёт прихода / ухода -------------------------

class Attendance:
    def __init__(self):
        self.lock = threading.RLock()
        self.log_lock = threading.Lock()
        raw = load_json(LAST_SEEN_FILE, {})
        self.last_seen = {}
        for n, s in raw.items():
            try:
                self.last_seen[n] = dt.datetime.fromisoformat(s)
            except ValueError:
                pass
        self.present = set()
        self.arrived_today = set()
        self._quiet = {}
        self._last_save = 0.0
        self._recover()

    # --- журнал
    def log_event(self, when, name, kind, late=0):
        with self.log_lock:
            new = not os.path.exists(LOG_FILE)
            with open(LOG_FILE, "a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["Дата", "Время", "Сотрудник", "Филиал", "Событие", "Опоздание"])
                w.writerow([when.date().isoformat(), when.strftime("%H:%M:%S"),
                            name, BRANCH, kind, late])

    def snapshot(self):
        with self.lock:
            return dict(self.last_seen)

    def present_snapshot(self):
        with self.lock:
            return set(self.present)

    def save_state(self, force=False):
        if force or time.time() - self._last_save > 30:
            self._last_save = time.time()
            save_json(LAST_SEEN_FILE,
                      {n: t.isoformat() for n, t in self.snapshot().items()})

    # --- восстановление после перезапуска
    def _recover(self):
        now = dt.datetime.now()
        today = workday(now, DAY_START_HOUR)
        events = read_events(LOG_FILE)
        last = {}
        for e in events:
            last[e["name"]] = e
            if e["type"] == "приход" and workday(e["when"], DAY_START_HOUR) == today:
                self.arrived_today.add(e["name"])
        for name, e in last.items():
            if e["type"] != "приход":
                continue
            ls = self.last_seen.get(name)
            if workday(e["when"], DAY_START_HOUR) == today:
                self.present.add(name)
                # Нет сохранённой отметки - отсчитываем таймаут ухода от момента запуска,
                # иначе такой человек не «уйдёт» никогда.
                if ls is None or ls < e["when"]:
                    self.last_seen[name] = now
            else:  # остался «на работе» с прошлого дня: закрываем по последней отметке
                same_day = ls is not None and workday(ls, DAY_START_HOUR) == workday(e["when"], DAY_START_HOUR)
                t = ls if same_day and ls > e["when"] else e["when"]
                self.log_event(t, name, "уход")
        if self.present:
            print("После перезапуска считаются на месте: " + ", ".join(sorted(self.present)))

    def _late(self, name, now):
        h, m = map(int, SHIFTS.get(name, DEFAULT_SHIFT).split(":"))
        shift_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        # Берём ближайшую к «сейчас» смену: так работает и для ночных смен
        # (пришёл в 21:05 к смене 21:00; пришёл в 08:50 к смене 09:00 - не опоздал).
        if shift_dt - now > dt.timedelta(hours=12):
            shift_dt -= dt.timedelta(days=1)
        elif now - shift_dt > dt.timedelta(hours=12):
            shift_dt += dt.timedelta(days=1)
        late = int((now - shift_dt).total_seconds() // 60)
        return late if late > GRACE_MINUTES else 0

    # --- события
    def seen(self, name, now):
        """Вызывается, когда сотрудник уверенно распознан."""
        with self.lock:
            self.last_seen[name] = now
            if name in self.present:
                if time.time() - self._quiet.get(name, 0) > 300:
                    self._quiet[name] = time.time()
                    print(f"{name}: распознан(а), но уже числится на месте - "
                          f"повторное сообщение не отправляется (время прихода: /today)")
                return
            first = name not in self.arrived_today
            self.present.add(name)
            self.arrived_today.add(name)
            late = self._late(name, now) if first else 0
            self.log_event(now, name, "приход", late)
        t = now.strftime("%H:%M")
        if first:
            text = (f"⚠️ {name}, филиал {BRANCH}, пришёл(ла) в {t} (опоздание {late} мин)"
                    if late else f"✅ {name}, филиал {BRANCH}, пришёл(ла) в {t}")
        else:
            text = f"🔄 {name}, филиал {BRANCH}, вернулся(ась) в {t}"
        print(text)
        if first or SEND_RETURN_MESSAGES:
            send_telegram(text)

    def _depart(self, name, when):
        with self.lock:
            self.present.discard(name)
            self.log_event(when, name, "уход")
        d = workday(when, DAY_START_HOUR)
        info = build_days(read_events(LOG_FILE), d, d, day_start=DAY_START_HOUR).get((d, name))
        text = f"🔴 {name}, филиал {BRANCH}, ушёл(ла) в {when:%H:%M}"
        if info:
            text += (f"\nПришёл(ла) в {info['first_in']:%H:%M}, "
                     f"отработано за день: {fmt_dur(info['worked_sec'])}")
        print(text)
        send_telegram(text)

    def check_departures(self, now):
        with self.lock:
            gone = []
            for n in self.present:
                ls = self.last_seen.setdefault(n, now)
                if (now - ls).total_seconds() > LEAVE_TIMEOUT_MIN * 60:
                    gone.append((n, ls))
        for n, ls in gone:
            # Пока шли к этому месту, человека могли снова увидеть - тогда не уходим.
            with self.lock:
                if n not in self.present or self.last_seen.get(n, ls) != ls:
                    continue
            self._depart(n, ls)

    def new_day(self, now):
        """Смена рабочего дня. Тех, кто прямо сейчас в кадре (работает через границу
        дня), молча переносим в новый день: уход в 23:59:59 и приход в 00:00:00.
        Остальных закрываем по последней отметке."""
        boundary = (dt.datetime.combine(workday(now, DAY_START_HOUR), dt.time())
                    + dt.timedelta(hours=DAY_START_HOUR))
        with self.lock:
            names = list(self.present)
        carry = []
        for n in names:
            ls = self.last_seen.get(n, now)
            if (now - ls).total_seconds() <= 60:
                carry.append(n)
            else:
                self._depart(n, ls)
        with self.lock:
            self.arrived_today = set(carry)
            for n in carry:
                self.log_event(boundary - dt.timedelta(seconds=1), n, "уход")
                self.log_event(boundary, n, "приход", 0)
        self.save_state(force=True)


# ------------------------- Отчёты -------------------------

def send_report(att, start, end, title, fname, caption):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, fname)
    ok = make_report(path, read_events(LOG_FILE), start, end, title,
                     last_seen=att.snapshot(), now=dt.datetime.now(),
                     day_start=DAY_START_HOUR, visits=read_visitors(VISITORS_FILE))
    if ok:
        _send_doc(path, caption)
    else:
        _post("sendMessage", data={"chat_id": CHAT_ID,
                                   "text": f"За период {start:%d.%m}–{end:%d.%m.%Y} данных нет."})


def day_job(att, d):
    send_report(att, d, d, f"Отчёт за {d:%d.%m.%Y}, филиал {BRANCH}",
                f"День_{d:%Y-%m-%d}.xlsx", f"📊 Отчёт за {d:%d.%m.%Y}")


def parse_day(arg, today):
    """'21.10', '21.10.2026' или '2026-10-21' -> дата; пусто -> сегодня."""
    if not arg:
        return today
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m"):
        try:
            d = dt.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
        if fmt == "%d.%m":
            d = d.replace(year=today.year)
            if d > today:
                d = d.replace(year=today.year - 1)
        return d
    return None


def week_job(att, d):
    s, e = week_range(d)
    send_report(att, s, e, f"Отчёт за неделю {s:%d.%m.%Y} – {e:%d.%m.%Y}, филиал {BRANCH}",
                f"Неделя_{s:%Y-%m-%d}.xlsx", f"📊 Отчёт за неделю {s:%d.%m}–{e:%d.%m.%Y}")


def month_job(att, d):
    s, e = month_range(d)
    send_report(att, s, e, f"Отчёт за {MONTHS[s.month - 1]} {s.year}, филиал {BRANCH}",
                f"Месяц_{s:%Y-%m}.xlsx", f"📊 Отчёт за {MONTHS[s.month - 1]} {s.year}")


def visitors_text(visitors):
    now = dt.datetime.now()
    d = workday(now, DAY_START_HOUR)
    st = visitor_stats(read_visitors(VISITORS_FILE), d, d, DAY_START_HOUR)
    peak = st["peak_hour"]
    peak_s = (f"{peak:02d}:00 ({st['by_hour'][peak]} чел.)" if peak is not None else "нет данных")
    return (f"👥 Посетители сегодня, {d:%d.%m.%Y}, филиал {BRANCH}:\n"
            f"Зашло в аптеку: {st['total']}\n"
            f"Вышло из аптеки: {st['exits']}\n"
            f"Сейчас внутри: ≈{visitors.counts()[2]}\n"
            f"Час пик: {peak_s}")


def today_text(att):
    now = dt.datetime.now()
    d = workday(now, DAY_START_HOUR)
    days = build_days(read_events(LOG_FILE), d, d, att.snapshot(), now, DAY_START_HOUR)
    if not days:
        return f"Сегодня ({d:%d.%m.%Y}) отметок пока нет."
    present = att.present_snapshot()
    lines = [f"📋 Сегодня, {d:%d.%m.%Y}, филиал {BRANCH}:"]
    for (_, n), i in sorted(days.items(), key=lambda kv: kv[0][1]):
        state = "на месте" if n in present else "ушёл(ла)"
        lines.append(f"• {n}: {state}, с {i['first_in']:%H:%M}, "
                     f"отработано {fmt_dur(i['worked_sec'])}"
                     + (f", опоздание {i['late']} мин" if i["late"] else ""))
    return "\n".join(lines)


def handle_command(text, att, visitors):
    if not text.startswith("/"):
        return
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    today = workday(dt.datetime.now(), DAY_START_HOUR)
    if cmd == "/today":
        send_telegram(today_text(att), wait=True)
    elif cmd == "/visitors":
        send_telegram(visitors_text(visitors), wait=True)
    elif cmd == "/day":
        d = parse_day(parts[1] if len(parts) > 1 else "", today)
        if d is None:
            send_telegram("Не понял дату. Пример: /day 21.10 или /day 2026-10-21", wait=True)
        else:
            day_job(att, d)
    elif cmd == "/yesterday":
        day_job(att, today - dt.timedelta(days=1))
    elif cmd == "/week":
        week_job(att, today)
    elif cmd == "/prevweek":
        week_job(att, today - dt.timedelta(days=7))
    elif cmd == "/month":
        month_job(att, today)
    elif cmd == "/prevmonth":
        month_job(att, today.replace(day=1) - dt.timedelta(days=1))
    elif cmd in ("/start", "/help"):
        send_telegram("Команды:\n/today - кто сегодня на работе\n"
                      "/visitors - сколько клиентов зашло и вышло сегодня\n"
                      "/day - Excel за сегодня (или /day 21.10 - за дату)\n"
                      "/yesterday - Excel за вчера\n"
                      "/week - Excel за текущую неделю\n/prevweek - за прошлую неделю\n"
                      "/month - Excel за текущий месяц\n/prevmonth - за прошлый месяц",
                      wait=True)


def bot_loop(att, visitors):
    offset = None
    try:  # пропускаем старые команды
        res = requests.get(f"{API}/getUpdates", params={"offset": -1}, timeout=15).json()
        if res.get("result"):
            offset = res["result"][-1]["update_id"] + 1
    except Exception:
        pass
    while True:
        try:
            params = {"timeout": 25}
            if offset:
                params["offset"] = offset
            data = requests.get(f"{API}/getUpdates", params=params, timeout=40).json()
            if not data.get("ok"):
                desc = str(data.get("description", data))
                print("Ошибка опроса бота:", desc)
                if "Conflict" in desc or "terminated by other" in desc:
                    print("Уже запущен другой экземпляр программы с этим ботом. "
                          "Закройте второе окно и перезапустите один раз.")
                    time.sleep(15)
                else:
                    time.sleep(5)
                continue
            for u in data["result"]:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
                    continue
                try:
                    handle_command(msg.get("text", ""), att, visitors)
                except Exception as e:
                    print("Ошибка команды:", _safe(e))
        except Exception as e:
            print("Ошибка опроса бота:", _safe(e))
            time.sleep(5)


def check_schedule(att, visitors, now, sent):
    """Автоотчёты и итог дня. sent - словарь уже отправленного (хранится в файле)."""
    hhmm = now.strftime("%H:%M")
    d = workday(now, DAY_START_HOUR)
    changed = False
    if hhmm >= AUTO_REPORT_TIME:
        if d.weekday() == 0 and f"week:{d}" not in sent:
            sent[f"week:{d}"] = 1
            changed = True
            threading.Thread(target=week_job, args=(att, d - dt.timedelta(days=7)),
                             daemon=True).start()
        if d.day == 1 and f"month:{d}" not in sent:
            sent[f"month:{d}"] = 1
            changed = True
            prev = d - dt.timedelta(days=1)
            threading.Thread(target=month_job, args=(att, prev), daemon=True).start()
    if DAILY_SUMMARY_TIME and hhmm >= DAILY_SUMMARY_TIME and f"day:{d}" not in sent:
        sent[f"day:{d}"] = 1
        changed = True
        send_telegram("🌙 Итог дня\n" + today_text(att) + "\n\n" + visitors_text(visitors))
    if changed:
        for k in [k for k in sent if k[-10:] < (d - dt.timedelta(days=40)).isoformat()]:
            del sent[k]
        save_json(REPORT_STATE_FILE, sent)


# ------------------------- Камеры -------------------------

def open_capture(source):
    """Только RTSP NVR через FFmpeg. Без DirectShow / веб-камеры."""
    # Таймауты: без них зависший RTSP-поток вешает поток камеры навсегда (переподключения не будет).
    try:
        cap = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG,
                               [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000,
                                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000])
    except (TypeError, cv2.error, AttributeError):  # старый OpenCV без параметров
        cap = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class CameraReader(threading.Thread):
    """Читает камеру в фоне, держит свежий кадр, сам переподключается."""

    def __init__(self, name, source):
        super().__init__(daemon=True)
        self.name = name
        self.source = source
        self.frame = None
        self.latest = None   # последний кадр для потока двери (get() его не забирает)
        self.seq = 0
        self.lock = threading.Lock()
        self.running = True

    def run(self):
        while self.running:
            cap = open_capture(self.source)
            if not cap.isOpened():
                print(f"[{self.name}] не открывается, повтор через 5 сек "
                      f"(проверьте IP, пароль, номер камеры, сеть)")
                time.sleep(5)
                continue
            print(f"[{self.name}] подключена")
            fails = 0
            while self.running:
                ok, frame = cap.read()
                if not ok or frame is None or frame.size == 0:
                    fails += 1
                    if fails > 30:
                        print(f"[{self.name}] видео оборвалось, переподключаюсь...")
                        break
                    time.sleep(0.1)
                    continue
                fails = 0
                with self.lock:
                    self.frame = self.latest = frame
                    self.seq += 1
            cap.release()
            time.sleep(3)

    def get(self):
        with self.lock:
            f, self.frame = self.frame, None
        return f

    def peek(self):
        with self.lock:
            return self.seq, self.latest


class DoorWorker(threading.Thread):
    """Подсчёт входов/выходов у двери в своём потоке: распознавание лиц в основном цикле
    не тормозит детектор людей (иначе ~2 анализа/с, и люди у двери «проскакивают»)."""

    def __init__(self, reader, door, visitors):
        super().__init__(daemon=True)
        self.reader = reader
        self.door = door
        self.visitors = visitors
        path, _, size, use_roi = PERSON_MODELS[PERSON_MODEL]
        self.detector = PersonDetector(path, score=PERSON_SCORE, size=size)
        self.roi = DOOR_ROI if use_roi else None
        self.fps = 0.0
        self.running = True

    def run(self):
        last_seq, n, t_fps = -1, 0, time.time()
        while self.running:
            t0 = time.time()
            seq, frame = self.reader.peek()
            if frame is None or seq == last_seq:
                time.sleep(0.02)
                continue
            last_seq = seq
            fh, fw = frame.shape[:2]
            if fw > MAX_FRAME_WIDTH:  # тот же размер, что в основном цикле: рамки лиц для
                fh, fw = int(fh * MAX_FRAME_WIDTH / fw), MAX_FRAME_WIDTH  # mark_staff совпадут
                frame = cv2.resize(frame, (fw, fh))
            try:
                events = self.door.update(self.detector.detect(frame, self.roi),
                                          dt.datetime.now(), fw, fh)
            except Exception as e:
                print(f"[{self.reader.name}] ошибка подсчёта у двери: {e}")
                time.sleep(1)
                continue
            for track, kind in events:
                if track["staff"]:
                    print(f"{track['staff']}: {kind} через дверь ({self.reader.name}), "
                          f"в клиенты не записан")
                    continue
                self.visitors.add(dt.datetime.now(), self.reader.name, kind)
                n_in, n_out, inside = self.visitors.counts()
                print(f"Клиент: {kind} ({self.reader.name}). Сегодня зашло {n_in}, "
                      f"вышло {n_out}, внутри ≈{inside}")
            n += 1
            if time.time() - t_fps >= 5:
                self.fps = n / (time.time() - t_fps)
                n, t_fps = 0, time.time()
            time.sleep(max(0.0, DOOR_DETECT_INTERVAL_SEC - (time.time() - t0)))


def check_settings():
    """Возвращает список незаполненных обязательных настроек."""
    missing = []
    if not _is_filled(BOT_TOKEN):
        missing.append("BOT_TOKEN")
    if not _is_filled(CHAT_ID):
        missing.append("CHAT_ID")
    if not CUSTOM_CAMERAS and not _is_filled(NVR_PASSWORD):
        missing.append("NVR_PASSWORD")
    return missing


def main():
    missing = check_settings()
    if missing:
        print("Не заполнено: " + ", ".join(missing))
        env_path = os.path.join(BASE_DIR, "secrets.env")
        if not os.path.exists(env_path):
            print(f"Файл не найден: {env_path}")
            near = [f for f in os.listdir(BASE_DIR) if f.lower().startswith("secrets")]
            if near:
                print("Рядом есть: " + ", ".join(near) + " - переименуйте нужный ровно в secrets.env "
                      "(Windows часто прячет «.txt» на конце имени)")
        else:
            print(f"Файл {env_path} найден, но в нём нет этих строк. Нужно: КЛЮЧ=значение, "
                  "например BOT_TOKEN=123:ABC")
        return
    download_models()
    detector = cv2.FaceDetectorYN.create(
        "face_detection_yunet_2023mar.onnx", "", (YUNET_SIZE, YUNET_SIZE),
        DETECT_SCORE, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create("face_recognition_sface_2021dec.onnx", "")

    db = load_employees(detector, recognizer)
    if not db:
        print("Нет фото сотрудников. Добавьте их в папку photos и запустите снова.")
        return

    if not check_telegram():
        print("Камеры запущу, но сообщения в Telegram могут не приходить.")

    att = Attendance()
    visitors = VisitorLog(VISITORS_FILE, BRANCH)
    n_in, n_out = visitors.restore_today(DAY_START_HOUR)
    if n_in or n_out:
        print(f"За сегодня уже записано посетителей: вход {n_in}, выход {n_out}")
    doors = {}
    if COUNT_VISITORS:
        saved = load_json(DOOR_LINES_FILE, {})
        for ch in VISITOR_CHANNELS:
            cfg = saved.get(camera_label(ch))
            if not cfg:
                cfg = DOOR_DEFAULT
                print(f"[{camera_label(ch)}] линия двери не настроена, взята примерная. "
                      f"Настройте точно: python door_setup.py")
            doors[camera_label(ch)] = DoorCounter(camera_label(ch), cfg["line"], cfg["inside"],
                                                  anchor=DOOR_ANCHOR)
    sent = load_json(REPORT_STATE_FILE, {})
    threading.Thread(target=bot_loop, args=(att, visitors), daemon=True).start()

    reports_url = ""
    if REPORTS_WEB:
        reports_url = start_reports_server({
            "branch": BRANCH,
            "day_start": DAY_START_HOUR,
            "events_file": LOG_FILE,
            "visitors_file": VISITORS_FILE,
            "password": os.environ.get("REPORTS_PASSWORD", ""),
            "live_state": lambda: {
                "staff_present": list(att.present_snapshot()),
                "last_seen": att.snapshot(),
                "visitors_inside": visitors.counts()[2],
                "visitors_today": visitors.counts()[0],
                "visitors_exited": visitors.counts()[1],
            },
        }, host=REPORTS_HOST, port=int(REPORTS_PORT))
        print(f"Сайт отчётов: {reports_url}")

    cameras = build_cameras()
    readers = [CameraReader(n, s) for n, s in cameras.items()]
    for r in readers:
        r.start()
    door_workers = {r.name: DoorWorker(r, doors[r.name], visitors)
                    for r in readers if r.name in doors}
    for wk in door_workers.values():
        wk.start()

    today = workday(dt.datetime.now(), DAY_START_HOUR)
    streaks = {r.name: {} for r in readers}
    last_proc = {r.name: 0.0 for r in readers}
    last_thorough = {r.name: 0.0 for r in readers}
    last_tick = 0.0

    src_info = (f"NVR {NVR_IP}:{NVR_PORT}; сотрудники: {STAFF_CHANNELS}; "
                f"клиенты (линия у двери): {VISITOR_CHANNELS}")
    print(f"Система запущена. Филиал: {BRANCH}. Источник: {src_info}. Выход: Q")
    if SEND_START_MESSAGE:
        extra = f"\nСайт отчётов: {reports_url}" if reports_url else ""
        send_telegram(f"🟢 Система учёта запущена, филиал {BRANCH}, камер: {len(readers)}\n"
                      f"Сотрудники: {', '.join(sorted(db))}\n"
                      f"Команды: /today /visitors /day /week /month /help{extra}", wait=True)

    try:
        while True:
            now = dt.datetime.now()
            wd = workday(now, DAY_START_HOUR)
            if wd != today:
                att.new_day(now)
                visitors.new_day()
                today = wd

            if time.time() - last_tick >= 1:
                last_tick = time.time()
                att.check_departures(now)
                check_schedule(att, visitors, now, sent)
                att.save_state()

            got_any = False
            for r in readers:
                frame = r.get()
                if frame is None:
                    continue
                got_any = True

                h0, w0 = frame.shape[:2]
                if w0 > MAX_FRAME_WIDTH:
                    k = MAX_FRAME_WIDTH / w0
                    frame = cv2.resize(frame, (MAX_FRAME_WIDTH, int(h0 * k)))

                door = doors.get(r.name)

                if time.time() - last_proc[r.name] >= DETECT_INTERVAL_SEC:
                    last_proc[r.name] = time.time()
                    deep = time.time() - last_thorough[r.name] >= 1.5
                    if deep:
                        last_thorough[r.name] = time.time()
                    faces = nms_faces(detect_faces(detector, frame, thorough=deep))
                    new_streaks = {}
                    n_faces = 0 if faces is None else len(faces)
                    best_lbl = ""
                    for face in (faces if faces is not None else []):
                        x, y, w, h = map(int, face[:4])
                        if w < MIN_FACE_WIDTH:
                            if SHOW_WINDOW:
                                cv2.rectangle(frame, (x, y), (x + w, y + h), (180, 180, 0), 1)
                            continue
                        try:
                            aligned = recognizer.alignCrop(frame, face)
                            if aligned is None or aligned.size == 0:
                                continue
                            feature = recognizer.feature(aligned)
                        except Exception:
                            continue
                        name, score = identify(feature, db, recognizer)
                        if name:
                            cnt = streaks[r.name].get(name, 0) + 1
                            new_streaks[name] = cnt
                            label = f"{ascii_label(name) if WINDOW_TRANSLIT else name} {score:.2f}"
                            if cnt >= CONFIRM_FRAMES:
                                att.seen(name, dt.datetime.now())
                            # У двери человек проходит быстро - метим фигуру сразу,
                            # иначе сотрудник успеет пересечь линию и попадёт в клиенты.
                            if door:
                                door.mark_staff((x, y, w, h), name)
                        else:
                            label = f"? {score:.2f}"
                        best_lbl = label
                        if SHOW_WINDOW:
                            color = (0, 200, 0) if name else (0, 0, 220)
                            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                            cv2.putText(frame, label, (x, max(y - 10, 20)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                    streaks[r.name] = new_streaks
                    if SHOW_WINDOW:
                        hud = f"faces: {n_faces}"
                        if best_lbl:
                            hud += f"  {best_lbl}"
                        cv2.putText(frame, hud, (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

                if SHOW_WINDOW:
                    if door:
                        door.draw(frame)
                        n_in, n_out, inside = visitors.counts()
                        cv2.putText(frame, f"in: {n_in}  out: {n_out}  inside: {inside}  "
                                           f"door fps: {door_workers[r.name].fps:.1f}",
                                    (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                    cv2.imshow(ascii_label(r.name), frame)

            if SHOW_WINDOW:
                if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                    break
            if not got_any:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        att.save_state(force=True)
        for r in readers:
            r.running = False
        for wk in door_workers.values():
            wk.running = False
        cv2.destroyAllWindows()
        print("Остановлено.")


if __name__ == "__main__":
    main()