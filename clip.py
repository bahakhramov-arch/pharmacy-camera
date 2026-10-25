# -*- coding: utf-8 -*-
"""Кусок записи с регистратора -> mp4 -> владельцу в Telegram (первый CHAT_ID).
Удобно проверить, кого камера «узнала» в такое-то время.

    python clip.py 13:22 13:24            (сегодня, камеры сотрудников из STAFF_CHANNELS)
    python clip.py 13:22 13:24 6          (только камера 6)
    python clip.py 13:22 13:24 5,6 24.10  (за 24 октября)

Видео сохраняется в папку clips\\ и отправляется в Telegram (до 50 МБ).
"""

import datetime as dt
import os
import sys

import cv2
import requests
from requests.auth import HTTPDigestAuth

import pharmacy_attendance as pa

OUT_DIR = "clips"
OUT_WIDTH = 800
OUT_FPS = 6           # меньше кадров - файл легче (Telegram берёт до 50 МБ)


def download(ch, start, end, path):
    url = (f"http://{pa.NVR_IP}/cgi-bin/loadfile.cgi?action=startLoad&channel={ch}"
           f"&startTime={start:%Y-%m-%d%%20%H:%M:%S}&endTime={end:%Y-%m-%d%%20%H:%M:%S}&subtype=0")
    with requests.get(url, auth=HTTPDigestAuth(pa.NVR_LOGIN, pa.NVR_PASSWORD),
                      stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return os.path.getsize(path)


def to_mp4(src, dst):
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    if not 1 <= fps <= 60:
        fps = 15
    step = max(1, round(fps / OUT_FPS))
    out, n, i = None, 0, -1
    while True:
        ok, f = cap.read()
        if not ok:
            break
        i += 1
        if i % step:
            continue
        h, w = f.shape[:2]
        if w > OUT_WIDTH:
            f = cv2.resize(f, (OUT_WIDTH, int(h * OUT_WIDTH / w)))
        if out is None:
            for codec in ("mp4v",):
                out = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*codec), fps / step, f.shape[1::-1])
                if out.isOpened():
                    break
        out.write(f)
        n += 1
    cap.release()
    if out is not None:
        out.release()
    return n


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    t1, t2 = pa.settings.norm_time(sys.argv[1]), pa.settings.norm_time(sys.argv[2])
    if not t1 or not t2:
        print("Не понял время. Пример: python clip.py 13:22 13:24")
        return
    chans = ([int(c) for c in sys.argv[3].split(",") if c.strip()]
             if len(sys.argv) > 3 else pa.STAFF_CHANNELS)
    day = dt.date.today()
    if len(sys.argv) > 4:
        d, m = map(int, sys.argv[4].split("."))
        day = day.replace(month=m, day=d)
    start = dt.datetime.combine(day, dt.time(*map(int, t1.split(":"))))
    end = dt.datetime.combine(day, dt.time(*map(int, t2.split(":"))))
    if end <= start:
        end += dt.timedelta(days=1)
    os.makedirs(OUT_DIR, exist_ok=True)
    for ch in chans:
        base = os.path.join(OUT_DIR, f"cam{ch}_{start:%Y%m%d_%H%M}-{end:%H%M}")
        print(f"Камера {ch}: качаю {start:%d.%m %H:%M}-{end:%H:%M}...")
        try:
            size = download(ch, start, end, base + ".dav")
        except Exception as e:
            print("  не скачалось:", pa._safe(e))
            continue
        frames = to_mp4(base + ".dav", base + ".mp4")
        os.remove(base + ".dav")
        if not frames:
            print(f"  записи нет (скачано {size} байт)")
            continue
        mb = os.path.getsize(base + ".mp4") / 1e6
        print(f"  готово: {base}.mp4 ({frames} кадров, {mb:.1f} МБ)")
        if pa.BOT_TOKEN and pa.CHAT_IDS and mb < 50:
            with open(base + ".mp4", "rb") as f:
                ok = pa._post("sendVideo", data={
                    "chat_id": pa.CHAT_IDS[0], "supports_streaming": "true",
                    "caption": f"{pa.BRANCH}, камера {ch}: {start:%d.%m %H:%M}-{end:%H:%M}"},
                    files={"video": (os.path.basename(base) + ".mp4", f)})
            print("  отправлено в Telegram" if ok else "  в Telegram не отправилось")


if __name__ == "__main__":
    main()
