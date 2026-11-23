"""Draw the app icons (the red 读 tile) for phones' home screens.

Run once:  python tools/make_icons.py   -> site/icons/*.png
Needs Pillow and a Chinese font (Windows: Microsoft YaHei / Noto Serif SC).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import ROOT

OUT = ROOT / "site" / "icons"
RED = (180, 67, 47)
FONTS = [r"C:\Windows\Fonts\NotoSerifSC-VF.ttf", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"]


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONTS:
        if Path(f).exists():
            fnt = ImageFont.truetype(f, size)
            try:
                fnt.set_variation_by_name("Bold")
            except Exception:
                pass
            return fnt
    raise SystemExit("No Chinese font found")


def draw(size: int, *, maskable: bool) -> Image.Image:
    """maskable: full-bleed background (Android crops it into a circle/squircle)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:
        d.rectangle([0, 0, size, size], fill=RED)
        glyph = int(size * 0.46)          # keep the character inside the safe zone
    else:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=RED)
        glyph = int(size * 0.62)
    d.text((size / 2, size / 2), "读", font=font(glyph), fill="white", anchor="mm")
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, maskable=False).save(OUT / "icon-192.png")
    draw(512, maskable=False).save(OUT / "icon-512.png")
    draw(512, maskable=True).save(OUT / "icon-maskable-512.png")
    draw(180, maskable=True).convert("RGB").save(OUT / "apple-touch-icon.png")  # iOS rounds corners itself
    print("icons ->", OUT)


if __name__ == "__main__":
    main()
