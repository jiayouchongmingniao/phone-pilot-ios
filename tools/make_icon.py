#!/usr/bin/env python3
"""Draws the placeholder app icon: a coral disc on Yay's dark paper colour, 1024 x 1024, opaque RGB PNG (the App Store
rejects icons with alpha). Standard library only, so it runs anywhere: python3 tools/make_icon.py <out.png>"""
import struct
import sys
import zlib

SIZE = 1024
PAPER = (0x17, 0x1C, 0x19)
CORAL = (0xDF, 0x79, 0x5D)
INK = (0xFA, 0xF7, 0xEF)


def pixel(x, y):
    cx = cy = SIZE / 2
    d2 = (x - cx) ** 2 + (y - cy) ** 2
    if d2 <= 120 ** 2:
        return INK  # the "lens" in the middle
    if d2 <= 330 ** 2:
        return CORAL
    return PAPER


def png(path):
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)  # filter: none
        for x in range(SIZE):
            rows.extend(pixel(x, y))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)))  # 8-bit RGB
        f.write(chunk(b"IDAT", zlib.compress(bytes(rows), 9)))
        f.write(chunk(b"IEND", b""))


if __name__ == "__main__":
    png(sys.argv[1])
