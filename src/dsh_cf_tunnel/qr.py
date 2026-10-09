"""Terminal and PNG QR rendering. segno is the only third-party dependency."""

from __future__ import annotations

import itertools
import os
import shutil
import sys

# Terminal QR rendering. Dark modules are always painted black on white: relying on
# the terminal theme instead both inverts the code on a dark theme and leaves the
# seams a half-block character shows when a font's block glyphs do not tile its cell.
QR_DARK = (0, 0, 0)


QR_LIGHT = (255, 255, 255)


BLOCK_UPPER = "\u2580"


BLOCK_LOWER = "\u2584"


BLOCK_FULL = "\u2588"


ANSI_RESET = "\x1b[0m"


QR_STYLES = ("auto", "background", "half", "plain")


def qr_matrix(url: str, border: int) -> list[str]:
    """Return the QR module rows as '0'/'1' strings, padded with the quiet zone."""
    import segno  # imported lazily so --help works without the optional dependency

    matrix = ["".join("1" if module else "0" for module in row) for row in segno.make(url, error="m").matrix]
    width = len(matrix[0])
    blank = "0" * (width + 2 * border)
    return [blank] * border + ["0" * border + row + "0" * border for row in matrix] + [blank] * border


def supports_truecolor() -> bool:
    """Whether the terminal advertises 24-bit color, so modules get exact black and white."""
    colorterm = os.environ.get("COLORTERM", "").strip().lower()
    return colorterm in {"truecolor", "24bit"} or "direct" in os.environ.get("TERM", "").lower()


def sgr(foreground: tuple[int, int, int] | None, background: tuple[int, int, int] | None, truecolor: bool) -> str:
    """Build one SGR sequence setting the colors that are not None.

    The 16-color fallback uses the same black/white pair qrencode's ANSI output
    relies on, which every terminal understands but a themed palette may remap.
    """
    if truecolor:
        parts = [f"38;2;{foreground[0]};{foreground[1]};{foreground[2]}"] if foreground is not None else []
        parts += [f"48;2;{background[0]};{background[1]};{background[2]}"] if background is not None else []
    else:
        parts = ["30" if sum(foreground) < 384 else "37"] if foreground is not None else []
        parts += ["40" if sum(background) < 384 else "47"] if background is not None else []
    return f"\x1b[{';'.join(parts)}m" if parts else ""


def render_plain(rows: list[str]) -> str:
    """Colorless half blocks, one text row per two module rows: the log-friendly variant.

    Light modules stay terminal background, so the polarity follows the theme and a
    font whose block glyphs do not tile the cell can leave seams between rows.
    """
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        lines.append(
            "".join(
                BLOCK_FULL if (a == "1" and b == "1") else BLOCK_UPPER if a == "1" else BLOCK_LOWER if b == "1" else " "
                for a, b in zip(top, bottom)
            )
        )
    return "\n".join(lines)


def render_half(rows: list[str], truecolor: bool) -> str:
    """Compact colored half blocks: the upper half paints the top module, the cell background the bottom one.

    Painting both colors makes every cell opaque, so the code keeps its polarity on a
    dark or a light terminal and needs no seam-free block glyphs from the font.
    """
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        cells = []
        emitted: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None
        for a, b in zip(top, bottom):
            colors = (QR_DARK if a == "1" else QR_LIGHT, QR_DARK if b == "1" else QR_LIGHT)
            if colors != emitted:
                cells.append(sgr(colors[0], colors[1], truecolor))
                emitted = colors
            cells.append(BLOCK_UPPER)
        lines.append("".join(cells) + ANSI_RESET)
    return "\n".join(lines)


def render_background(rows: list[str], truecolor: bool) -> str:
    """Two spaces per module on an explicit black or white background: the most scannable variant.

    Colors cover the whole cell, so modules come out square and contiguous; the run
    length encoding keeps one escape sequence per run of same-colored modules.
    """
    lines = []
    for row in rows:
        cells = []
        for module, group in itertools.groupby(row):
            cells.append(sgr(None, QR_DARK if module == "1" else QR_LIGHT, truecolor))
            cells.append("  " * sum(1 for _ in group))
        lines.append("".join(cells) + ANSI_RESET)
    return "\n".join(lines)


def qr_columns(rows: list[str], style: str) -> int:
    """Terminal columns a style needs for a module grid."""
    return 2 * len(rows[0]) if style == "background" else len(rows[0])


def resolve_qr_style(requested: str, rows: list[str]) -> str:
    """Pick the rendering style: auto takes the least ambiguous style a terminal can show."""
    if requested != "auto":
        return requested
    if not sys.stdout.isatty():
        return "plain"
    return "background" if qr_columns(rows, "background") <= shutil.get_terminal_size((80, 24)).columns else "half"


def qr_art(rows: list[str], style: str) -> str:
    """Render the module grid as terminal art in the requested style."""
    if style == "background":
        return render_background(rows, supports_truecolor())
    if style == "half":
        return render_half(rows, supports_truecolor())
    return render_plain(rows)


def write_qr_png(url: str, path: str) -> None:
    """Write the QR code as a PNG image; segno writes PNG itself, so Pillow is not needed."""
    import segno  # imported lazily so --help works without the optional dependency

    segno.make(url, error="m").save(path, scale=8, border=4)
