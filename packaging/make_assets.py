"""Generate the Windows icons and Inno Setup wizard artwork."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = Path(r"C:\Windows\Fonts")

ACCENT = (106, 169, 255)
ACCENT_DARK = (52, 112, 188)
TEXT = (233, 239, 247)
MUTED = (139, 152, 169)


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def make_icon() -> Image.Image:
    scale = 4
    size = 256 * scale
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    margin = round(size * 0.035)
    radius = round(size * 0.22)
    box = (margin, margin, size - margin, size - margin)

    # Vertical background gradient clipped to the rounded square.
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(size):
        color = lerp((30, 42, 60), (8, 13, 20), y / (size - 1))
        gd.line((0, y, size, y), fill=(*color, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    canvas.alpha_composite(Image.composite(gradient, Image.new("RGBA", canvas.size, (0, 0, 0, 0)), mask))

    # Outer accent border.
    draw.rounded_rectangle(
        box,
        radius=radius,
        outline=(*ACCENT, 255),
        width=max(2, round(size * 0.010)),
    )

    # Waveform bars.
    bar_width = round(size * 0.058)
    heights = [0.22, 0.40, 0.60, 0.40, 0.23]
    offsets = [-0.17, -0.085, 0.0, 0.085, 0.17]
    colors = [
        (96, 160, 255),
        (121, 181, 255),
        (201, 221, 255),
        (121, 181, 255),
        (96, 160, 255),
    ]
    center = size / 2
    for offset, height_fraction, color in zip(offsets, heights, colors):
        x = center + offset * size
        bar_height = height_fraction * size
        draw.rounded_rectangle(
            (
                x - bar_width / 2,
                center - bar_height / 2,
                x + bar_width / 2,
                center + bar_height / 2,
            ),
            radius=bar_width / 2,
            fill=(*color, 255),
        )

    return canvas.resize((256, 256), Image.Resampling.LANCZOS)


def make_wizard_large() -> Image.Image:
    width, height = 164, 314
    scale = 2
    w, h = width * scale, height * scale
    image = Image.new("RGB", (w, h), (9, 14, 21))
    draw = ImageDraw.Draw(image)
    for y in range(h):
        color = lerp((20, 31, 47), (7, 11, 17), y / (h - 1))
        draw.line((0, y, w, y), fill=color)

    # Soft blue glow near the top.
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((w * 0.08, -h * 0.16, w * 0.92, h * 0.36), fill=(26, 66, 112))
    glow = glow.filter(ImageFilter.GaussianBlur(42 * scale))
    image = Image.blend(image, Image.blend(image, glow, 0.42), 0.75)
    draw = ImageDraw.Draw(image)

    icon = make_icon().resize((86 * scale, 86 * scale), Image.Resampling.LANCZOS)
    icon_x = (w - icon.width) // 2
    image.paste(icon, (icon_x, 42 * scale), icon)

    title_font = font("segoeuib.ttf", 23 * scale)
    subtitle_font = font("segoeui.ttf", 9 * scale)
    title = "SenseMaker"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_w = title_box[2] - title_box[0]
    draw.text(((w - title_w) / 2, 144 * scale), title, font=title_font, fill=TEXT)

    subtitle = "LOCAL AUDIO TRANSCRIPTION"
    sub_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    sub_w = sub_box[2] - sub_box[0]
    draw.text(((w - sub_w) / 2, 178 * scale), subtitle, font=subtitle_font, fill=MUTED)

    draw.line(
        (w * 0.28, 202 * scale, w * 0.72, 202 * scale),
        fill=(*ACCENT_DARK, 255),
        width=1 * scale,
    )

    # Subtle waveform at the bottom.
    bars = [(0.12, 0.22), (0.20, 0.34), (0.28, 0.46), (0.36, 0.58), (0.44, 0.46), (0.52, 0.34), (0.60, 0.22)]
    for frac_x, frac_h in bars:
        x = w * (0.19 + frac_x)
        bar_h = h * frac_h * 0.42
        draw.rounded_rectangle(
            (x - 2 * scale, h * 0.82 - bar_h / 2, x + 2 * scale, h * 0.82 + bar_h / 2),
            radius=2 * scale,
            fill=(*lerp((31, 47, 68), (50, 73, 102), frac_h), 255),
        )

    return image.resize((width, height), Image.Resampling.LANCZOS)


def make_wizard_small() -> Image.Image:
    width, height = 55, 58
    scale = 3
    w, h = width * scale, height * scale
    image = Image.new("RGB", (w, h), (13, 20, 29))
    icon = make_icon().resize((46 * scale, 46 * scale), Image.Resampling.LANCZOS)
    image.paste(icon, ((w - icon.width) // 2, (h - icon.height) // 2), icon)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = make_icon()
    icon.save(ASSETS / "icon.png")
    icon.save(
        ASSETS / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    make_wizard_large().save(ASSETS / "wizard-large.bmp")
    make_wizard_small().save(ASSETS / "wizard-small.bmp")
    print("Generated icon.png, icon.ico, wizard-large.bmp, wizard-small.bmp")


if __name__ == "__main__":
    main()
