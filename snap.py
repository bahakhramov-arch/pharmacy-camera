# -*- coding: utf-8 -*-
"""Кадр «прямо сейчас» со всех камер регистратора -> папка snaps\\ и владельцу в Telegram.

    python snap.py          (каналы 1-16)
    python snap.py 1,2,5    (только эти)
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import cv2

import pharmacy_attendance as pa

OUT_DIR = "snaps"


def grab(ch):
    url = (f"rtsp://{quote(pa.NVR_LOGIN, safe='')}:{quote(pa.NVR_PASSWORD, safe='')}"
           f"@{pa.NVR_IP}:{pa.NVR_PORT}/cam/realmonitor?channel={ch}&subtype=0")
    cap = pa.open_capture(url)
    frame = None
    if cap.isOpened():
        for _ in range(15):                 # первые кадры бывают серыми - берём свежий
            ok, f = cap.read()
            if ok:
                frame = f
    cap.release()
    return ch, frame


def main():
    chans = ([int(c) for c in sys.argv[1].split(",") if c.strip()]
             if len(sys.argv) > 1 else range(1, 17))
    os.makedirs(OUT_DIR, exist_ok=True)
    with ThreadPoolExecutor(8) as ex:
        results = sorted(ex.map(grab, chans))
    for ch, f in results:
        if f is None:
            print(f"Камера {ch}: нет картинки")
            continue
        path = os.path.join(OUT_DIR, f"cam{ch}.jpg")
        cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(path)
        print(f"Камера {ch}: {path}")
        if pa.BOT_TOKEN and pa.CHAT_IDS:
            with open(path, "rb") as fh:
                pa._post("sendPhoto", data={"chat_id": pa.CHAT_IDS[0],
                                            "caption": f"{pa.BRANCH}, камера {ch}"},
                         files={"photo": (f"cam{ch}.jpg", fh)})


if __name__ == "__main__":
    main()
