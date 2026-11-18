"""Genera los iconos de AveDescargas (assets/mediagrabber-icon.png y assets/mediagrabber.ico).

Uso: python tools/make_icons.py
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")


def draw_icon(size=512):
    scale = size / 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Fondo con degradado vertical azul.
    gradient = Image.new("RGBA", (size, size))
    gd = ImageDraw.Draw(gradient)
    top, bottom = (18, 150, 230), (8, 70, 150)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        gd.line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(110 * scale), fill=255)
    image.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(image)
    s = lambda v: int(v * scale)  # noqa: E731
    # Botón de reproducción.
    draw.polygon([(s(170), s(110)), (s(170), s(330)), (s(360), s(220))], fill=(255, 255, 255, 255))
    # Flecha de descarga.
    draw.rectangle([s(226), s(300), s(286), s(380)], fill=(255, 207, 74, 255))
    draw.polygon([(s(176), s(370)), (s(336), s(370)), (s(256), s(450))], fill=(255, 207, 74, 255))
    # Línea de tiempo.
    draw.rounded_rectangle([s(90), s(462), s(422), s(476)], radius=s(7), fill=(255, 255, 255, 200))
    return image


def main():
    os.makedirs(ASSETS, exist_ok=True)
    icon = draw_icon(512)
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "mediagrabber-icon.png"))
    icon.save(os.path.join(ASSETS, "mediagrabber.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Iconos generados en", ASSETS)


if __name__ == "__main__":
    main()
