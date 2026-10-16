"""Draw the 9Sigma Editor icon (assets/icons/sigma.ico) from the product mark.

    .venv\\Scripts\\python.exe tools\\make_icon.py

The same drawing as ``frontend/src/components/auth/ProductMark.tsx``: a deep navy rounded square
with a gold border and a gold play button. The shortcuts, the app window and the Setup EXE use
the .ico this writes; it is committed, so a build does not need to run this.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "icons" / "sigma.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
NAVY_TOP = (0x16, 0x22, 0x3D)
NAVY_BOTTOM = (0x0B, 0x12, 0x20)
GOLD = (0xFF, 0xC0, 0x00)
SCALE = 4  # drawn large and scaled down for smooth edges


def draw(size: int) -> Image.Image:
    big = size * SCALE
    unit = big / 64.0
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # The diagonal navy gradient, clipped to the rounded square.
    gradient = Image.new("RGBA", (big, big))
    pixels = gradient.load()
    for y in range(big):
        for x in range(big):
            t = (x + y) / (2.0 * big)
            pixels[x, y] = tuple(
                round(top + (bottom - top) * t)
                for top, bottom in zip(NAVY_TOP, NAVY_BOTTOM, strict=True)
            ) + (255,)
    mask = Image.new("L", (big, big), 0)
    box = (2 * unit, 2 * unit, 62 * unit, 62 * unit)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=14 * unit, fill=255)
    image.paste(gradient, (0, 0), mask)

    pen = ImageDraw.Draw(image)
    border = max(1, round(3 * unit)) if size >= 24 else max(1, round(4 * unit))
    pen.rounded_rectangle(box, radius=14 * unit, outline=GOLD, width=border)
    triangle = [(25 * unit, 19 * unit), (45 * unit, 32 * unit), (25 * unit, 45 * unit)]
    pen.polygon(triangle, fill=GOLD)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    largest = draw(max(SIZES))
    largest.save(OUTPUT, format="ICO", sizes=[(s, s) for s in SIZES],
                 append_images=[draw(s) for s in SIZES if s != max(SIZES)])
    print(f"  wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB, sizes {SIZES})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
