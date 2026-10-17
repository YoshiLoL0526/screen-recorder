"""Genera el icono de la app (`assets/icon.ico` + `assets/icon.png`) con Pillow.

Cuadrado redondeado con el degradado del tema (violeta → turquesa), un marco de «pantalla» y el
punto rojo de grabación. Se dibuja a 1024 px y se reduce a cada tamaño del .ico (16–256), así
que no depende de ningún archivo externo y se puede regenerar en cualquier momento.

Uso:  uv run python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 1024
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

PRIMARY = (124, 92, 255)  # $primary del tema
ACCENT = (45, 212, 191)  # $accent
REC = (255, 84, 112)  # $rec
INK = (11, 13, 20)


def _gradient(size: int) -> Image.Image:
    """Degradado diagonal entre PRIMARY y ACCENT."""
    small = Image.new("RGB", (2, 2))
    small.putpixel((0, 0), PRIMARY)
    small.putpixel((1, 0), tuple((p + a) // 2 for p, a in zip(PRIMARY, ACCENT)))
    small.putpixel((0, 1), tuple((p + a) // 2 for p, a in zip(PRIMARY, ACCENT)))
    small.putpixel((1, 1), ACCENT)
    return small.resize((size, size), Image.Resampling.BILINEAR)


def draw_icon(size: int = SIZE) -> Image.Image:
    s = size / 1024
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40 * s, 40 * s, 984 * s, 984 * s), radius=220 * s, fill=255)
    icon.paste(_gradient(size), (0, 0), mask)

    draw = ImageDraw.Draw(icon)
    # «Pantalla»: marco oscuro con el punto de grabación dentro y un pie.
    draw.rounded_rectangle((190 * s, 250 * s, 834 * s, 690 * s), radius=70 * s, fill=INK + (235,))
    draw.ellipse((402 * s, 360 * s, 622 * s, 580 * s), fill=REC)
    draw.rounded_rectangle((452 * s, 690 * s, 572 * s, 780 * s), radius=10 * s, fill=INK + (235,))
    draw.rounded_rectangle((340 * s, 760 * s, 684 * s, 820 * s), radius=30 * s, fill=INK + (235,))
    return icon


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = draw_icon()
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(ASSETS / "icon.png")
    icon.save(ASSETS / "icon.ico", sizes=ICO_SIZES)
    print(f"icono generado en {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    main()
