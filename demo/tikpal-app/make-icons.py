#!/usr/bin/env python3
"""Makes the app and runner icons from the Tikpal logo. Run again when the logo changes; apply.py copies the results in.

    python3 make-icons.py <logo image>

The logo comes with small black rounded corners. iOS rounds icons itself, so they are filled with the gradient.
Upstream's Scripts/make-runner-icons.py lists which sizes the runner needs.
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
# The logo's rounded corners end this far in, as a share of its width.
CORNER = 0.04
RUNNER = [
    ('AppIcon20x20', 20, 2, ''), ('AppIcon20x20', 20, 3, ''),
    ('AppIcon29x29', 29, 2, ''), ('AppIcon29x29', 29, 3, ''),
    ('AppIcon40x40', 40, 2, ''), ('AppIcon40x40', 40, 3, ''),
    ('AppIcon60x60', 60, 2, ''), ('AppIcon60x60', 60, 3, ''),
    ('AppIcon20x20', 20, 1, '~ipad'), ('AppIcon20x20', 20, 2, '~ipad'),
    ('AppIcon29x29', 29, 1, '~ipad'), ('AppIcon29x29', 29, 2, '~ipad'),
    ('AppIcon40x40', 40, 1, '~ipad'), ('AppIcon40x40', 40, 2, '~ipad'),
    ('AppIcon76x76', 76, 1, '~ipad'), ('AppIcon76x76', 76, 2, '~ipad'),
    ('AppIcon83.5x83.5', 83.5, 2, '~ipad'),
]


def fill_corners(image):
    """The gradient runs left to right, so a corner takes its colour from further along its own column."""
    size = image.width
    edge = round(size * CORNER)
    pixels = image.load()
    for x in (*range(edge), *range(size - edge, size)):
        for y in range(edge):
            pixels[x, y] = pixels[x, edge]
            pixels[x, size - 1 - y] = pixels[x, size - 1 - edge]


def main():
    logo = Image.open(sys.argv[1]).convert('RGB')
    fill_corners(logo)
    icon = logo.resize((1024, 1024), Image.LANCZOS)
    icon.save(os.path.join(HERE, 'icon_1024x1024.png'), optimize=True)
    out = os.path.join(HERE, 'RunnerIcon')
    os.makedirs(out, exist_ok=True)
    for name, points, scale, suffix in RUNNER:
        px = round(points * scale)
        tag = f'@{scale}x' if scale > 1 else ''
        icon.resize((px, px), Image.LANCZOS).save(os.path.join(out, f'{name}{tag}{suffix}.png'), optimize=True)


if __name__ == '__main__':
    main()
