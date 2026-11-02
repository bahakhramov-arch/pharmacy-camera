# -*- coding: utf-8 -*-
"""Логотип Ishda AI: значок (галочка + точка-голова = человек «я здесь!» и буква i) и надпись.

    python ishda\\brand\\make_logo.py      -> icon.png, icon.svg, logo.png, logo_dark.png
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
C1, C2 = (91, 47, 240), (20, 184, 166)          # фиолетовый -> бирюзовый
INK_LIGHT, INK_DARK = (20, 17, 31), (242, 240, 247)
S = 4                                            # рисуем в 4 раза крупнее и уменьшаем - гладкие края

# геометрия значка в координатах 512x512
HEAD = (262, 150, 46)                            # центр, радиус
CHECK = [(150, 282), (232, 362), (372, 212)]
STROKE = 58
RADIUS = 116


def gradient(size):
    w, h = size
    g = Image.new("RGB", size)
    px = g.load()
    for y in range(h):
        for x in range(w):
            t = (x + y) / (w + h - 2)
            px[x, y] = tuple(int(C1[i] + (C2[i] - C1[i]) * t) for i in range(3))
    return g


def icon(px=512):
    big = px * S
    k = big / 512
    bg = gradient((big // 8, big // 8)).resize((big, big), Image.BICUBIC)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), int(RADIUS * k), fill=255)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)
    pts = [(x * k, y * k) for x, y in CHECK]
    w = int(STROKE * k)
    d.line(pts, fill="white", width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):                       # круглые концы линии
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="white")
    cx, cy, r = (v * k for v in HEAD)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="white")
    return img.resize((px, px), Image.LANCZOS)


def icon_svg():
    pts = " ".join(f"{x},{y}" for x, y in CHECK)
    cx, cy, r = HEAD
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="rgb{C1}"/><stop offset="1" stop-color="rgb{C2}"/></linearGradient></defs>
  <rect width="512" height="512" rx="{RADIUS}" fill="url(#g)"/>
  <polyline points="{pts}" fill="none" stroke="#fff" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff"/>
</svg>
'''


def font(name, size):
    for f in (name, "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(os.path.join(r"C:\Windows\Fonts", f), size)
        except OSError:
            continue
    return ImageFont.load_default()


def logo(ink, bg):
    h = 240 * S
    ic = icon(200).resize((200 * S, 200 * S), Image.LANCZOS)
    f_word = font("segoeuib.ttf", 150 * S)
    f_ai = font("segoeuib.ttf", 64 * S)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    ww = tmp.textbbox((0, 0), "ishda", font=f_word)
    aw = tmp.textbbox((0, 0), "AI", font=f_ai)
    pill_w, pill_h = (aw[2] - aw[0]) + 44 * S, 92 * S
    width = 20 * S + 200 * S + 40 * S + (ww[2] - ww[0]) + 26 * S + pill_w + 30 * S
    img = Image.new("RGBA", (width, h), bg)
    img.paste(ic, (20 * S, 20 * S), ic)
    d = ImageDraw.Draw(img)
    x = 20 * S + 200 * S + 40 * S
    d.text((x - ww[0], h // 2 - (ww[1] + ww[3]) // 2), "ishda", font=f_word, fill=ink)
    px = x + (ww[2] - ww[0]) + 26 * S
    py = h // 2 - pill_h // 2 + 18 * S
    pill = gradient((pill_w // 8, pill_h // 8)).resize((pill_w, pill_h), Image.BICUBIC)
    m = Image.new("L", (pill_w, pill_h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, pill_w - 1, pill_h - 1), pill_h // 2, fill=255)
    img.paste(pill, (px, py), m)
    d.text((px + pill_w // 2 - (aw[0] + aw[2]) // 2, py + pill_h // 2 - (aw[1] + aw[3]) // 2), "AI",
           font=f_ai, fill="white")
    return img.resize((width // S, h // S), Image.LANCZOS)


if __name__ == "__main__":
    icon(512).save(os.path.join(HERE, "icon.png"))
    icon(64).save(os.path.join(HERE, "favicon.png"))
    with open(os.path.join(HERE, "icon.svg"), "w", encoding="utf-8") as f:
        f.write(icon_svg())
    logo(INK_LIGHT, (255, 255, 255, 255)).save(os.path.join(HERE, "logo.png"))
    logo(INK_DARK, (10, 9, 14, 255)).save(os.path.join(HERE, "logo_dark.png"))
    print("ok")
