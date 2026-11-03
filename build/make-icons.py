#!/usr/bin/env python3
"""Generate src/icon.png (80x80) and src/largeIcon.png (130x130). Needs Pillow.   python build/make-icons.py"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
FONTS = [
    r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    sys.exit("no bold TTF font found; edit FONTS in build/make-icons.py")


def draw(px):
    k = 8                       # supersampling
    s = px * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # rounded gradient tile
    tile = Image.new("RGBA", (s, s))
    d = ImageDraw.Draw(tile)
    for y in range(s):
        t = y / (s - 1)
        d.line([(0, y), (s, y)], fill=(int(24 - 14 * t), int(42 - 25 * t), int(62 - 36 * t), 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(tile, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # "TVer"
    f = font(int(s * 0.33))
    w = d.textlength("TVer", font=f)
    d.text(((s - w) / 2, s * 0.17), "TVer", font=f, fill=(255, 255, 255, 255))

    # green "OFF" pill
    pill_w, pill_h = s * 0.56, s * 0.25
    x0, y0 = (s - pill_w) / 2, s * 0.58
    d.rounded_rectangle([x0, y0, x0 + pill_w, y0 + pill_h], radius=pill_h / 2, fill=(23, 168, 107, 255))
    f2 = font(int(pill_h * 0.66))
    w2 = d.textlength("OFF", font=f2)
    bbox = d.textbbox((0, 0), "OFF", font=f2)
    d.text((x0 + (pill_w - w2) / 2, y0 + (pill_h - (bbox[3] - bbox[1])) / 2 - bbox[1]), "OFF", font=f2, fill=(255, 255, 255, 255))
    return img.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    draw(80).save(os.path.join(OUT, "icon.png"), optimize=True)
    draw(130).save(os.path.join(OUT, "largeIcon.png"), optimize=True)
    print("wrote src/icon.png (80x80) and src/largeIcon.png (130x130)")
