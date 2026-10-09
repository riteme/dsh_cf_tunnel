"""Shared test helpers: rasterize terminal QR art back into a bitmap.

The renderer is verified by decoding its output rather than by comparing strings, so a
polarity, half-row, or module-geometry mistake cannot pass unnoticed.
"""

from __future__ import annotations

import re

import pytest

from dsh_cf_tunnel import qr

ANSI_SGR = re.compile(r"\x1b\[([0-9;]*)m")
DARK = 0
LIGHT = 255
CELL = 4
BASIC_COLORS = {30: (0, 0, 0), 37: (255, 255, 255), 40: (0, 0, 0), 47: (255, 255, 255)}

zxingcpp = pytest.importorskip("zxingcpp", reason="the QR decode tests need zxing-cpp")
numpy = pytest.importorskip("numpy", reason="the QR decode tests need numpy")
Image = pytest.importorskip("PIL.Image", reason="the QR decode tests need pillow")


def parse_sgr(params: str) -> tuple[tuple[int, int, int] | None, tuple[int, int, int] | None]:
    """Return the foreground and background one SGR sequence sets, truecolor or 16-color."""
    values = [int(value) for value in params.split(";") if value != ""]
    foreground = background = None
    index = 0
    while index < len(values):
        value = values[index]
        if value in (38, 48) and values[index + 1] == 2:
            color = tuple(values[index + 2 : index + 5])
            foreground, background = (color, background) if value == 38 else (foreground, color)
            index += 5
        elif value in BASIC_COLORS:
            foreground, background = (
                (BASIC_COLORS[value], background) if value in (30, 37) else (foreground, BASIC_COLORS[value])
            )
            index += 1
        else:
            index += 1
    return foreground, background


def shade(color: tuple[int, int, int] | None) -> int:
    """Map one module color onto a bitmap shade."""
    assert color is not None, "a cell was painted without an explicit color"
    return DARK if sum(color) < 384 else LIGHT


def cells(art: str) -> list[list[tuple]]:
    """Split colored art into lines of (foreground, background, character) cells."""
    lines: list[list[tuple]] = [[]]
    foreground = background = None
    position = 0
    while position < len(art):
        match = ANSI_SGR.match(art, position)
        if match is not None:
            foreground, background = parse_sgr(match.group(1))
            position = match.end()
            continue
        char = art[position]
        if char == "\n":
            lines.append([])
        else:
            lines[-1].append((foreground, background, char))
        position += 1
    return lines


def to_pixels(rows: list[list[int]]):
    """Scale a module bitmap up to cell-sized pixels."""
    height, width = len(rows), len(rows[0])
    assert all(len(row) == width for row in rows), "ragged module bitmap"
    pixels = numpy.zeros((height * CELL, width * CELL), dtype=numpy.uint8)
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            pixels[y * CELL : (y + 1) * CELL, x * CELL : (x + 1) * CELL] = value
    return pixels


def module_rows(art: str, style: str) -> list[list[int]]:
    """Repaint colored art into module rows, exactly as a terminal would."""
    rows: list[list[int]] = []
    for line in cells(art):
        if style == "background":
            module_row: list[int] = []
            for _, background, char in line:
                module_row += [shade(background)] * len(char)
            rows.append(module_row)
            continue
        top_row, bottom_row = [], []
        for foreground, background, _ in line:
            top_row.append(shade(foreground))
            bottom_row.append(shade(background))
        rows += [top_row, bottom_row]
    return rows


def plain_module_rows(art: str) -> list[list[int]]:
    """Repaint colorless half-block art into module rows."""
    rows: list[list[int]] = []
    for line in art.split("\n"):
        top_row, bottom_row = [], []
        for char in line:
            top_row.append(DARK if char in (qr.BLOCK_FULL, qr.BLOCK_UPPER) else LIGHT)
            bottom_row.append(DARK if char in (qr.BLOCK_FULL, qr.BLOCK_LOWER) else LIGHT)
        rows += [top_row, bottom_row]
    return rows


def decode(pixels) -> str | None:
    """Read the URL out of a bitmap."""
    result = zxingcpp.read_barcode(numpy.asarray(Image.fromarray(pixels, mode="L")))
    return result.text if result is not None else None


def decode_art(art: str, style: str) -> str | None:
    """Read the URL out of rendered terminal art of any style."""
    rows = plain_module_rows(art) if style == "plain" else module_rows(art, style)
    return decode(to_pixels(rows))
