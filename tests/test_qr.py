"""QR rendering: geometry, colors, auto style selection, and decode round-trips."""

from __future__ import annotations

import pytest

from dsh_cf_tunnel import qr

URLS = [
    "https://example-abc-def.trycloudflare.com/?token=abcDEF123",
    "https://malpractice-communist-them-temporarily.trycloudflare.com/?token=-XSP7lI_i_S_0iKpiioMeqEvUAcLa-vJrqg95egV60Q",
    "https://tiny-qr.trycloudflare.com/?token=x",
]


def decode_art(art: str, style: str) -> str | None:
    """Decode rendered art, skipping the caller when the decode extras are absent."""
    from . import qr_support

    return qr_support.decode_art(art, style)


def test_matrix_carries_the_quiet_zone() -> None:
    """The matrix is square and padded by the requested border on every side."""
    rows = qr.qr_matrix(URLS[0], border=2)
    assert len(rows[0]) == len(rows)
    assert set(rows[0]) == {"0"} and set(rows[-1]) == {"0"}
    assert all(row.startswith("00") and row.endswith("00") for row in rows)


def test_border_widens_the_matrix() -> None:
    """A wider quiet zone makes the matrix wider by twice the difference."""
    narrow = qr.qr_matrix(URLS[0], border=2)
    wide = qr.qr_matrix(URLS[0], border=4)
    assert len(wide[0]) == len(narrow[0]) + 4


def test_style_widths() -> None:
    """The background style needs twice the columns of the half-block styles."""
    rows = qr.qr_matrix(URLS[0], border=2)
    assert qr.qr_columns(rows, "background") == 2 * qr.qr_columns(rows, "half")
    assert qr.qr_columns(rows, "plain") == qr.qr_columns(rows, "half")


def test_half_block_styles_pack_two_module_rows() -> None:
    """Every half-block style puts two module rows into one text row."""
    rows = qr.qr_matrix(URLS[0], border=2)
    assert len(qr.qr_art(rows, "plain").split("\n")) == len(rows) // 2
    assert len(qr.qr_art(rows, "half").split("\n")) == len(rows) // 2
    assert len(qr.qr_art(rows, "background").split("\n")) == len(rows)


def test_colorless_art_has_no_escape_sequences() -> None:
    """The plain style must stay readable in a log file."""
    rows = qr.qr_matrix(URLS[0], border=2)
    assert "\x1b" not in qr.qr_art(rows, "plain")


def test_colored_art_paints_every_module() -> None:
    """Colored styles never leave a cell to the terminal theme."""
    rows = qr.qr_matrix(URLS[0], border=2)
    for style in ("background", "half"):
        art = qr.qr_art(rows, style)
        assert art.startswith("\x1b[") and art.endswith(qr.ANSI_RESET)
        assert "38;2;0;0;0" in art or "48;2;0;0;0" in art


def test_background_art_runs_are_merged() -> None:
    """One escape sequence per color run, never one per cell, keeps the art printable."""
    rows = qr.qr_matrix(URLS[1], border=2)
    cells = len(rows) * len(rows[0])
    art = qr.qr_art(rows, "background")
    assert art.count("\x1b[") < cells / 2
    assert len(art) < cells * 10


def test_sgr_uses_16_colors_when_truecolor_is_absent() -> None:
    """The fallback pair is what qrencode's ANSI output relies on."""
    assert qr.sgr(qr.QR_DARK, None, True) == "\x1b[38;2;0;0;0m"
    assert qr.sgr(None, qr.QR_LIGHT, True) == "\x1b[48;2;255;255;255m"
    assert qr.sgr(qr.QR_DARK, None, False) == "\x1b[30m"
    assert qr.sgr(None, qr.QR_LIGHT, False) == "\x1b[47m"
    assert qr.sgr(None, None, True) == ""


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"COLORTERM": "truecolor"}, True),
        ({"COLORTERM": "24bit"}, True),
        ({"TERM": "xterm-direct"}, True),
        ({"TERM": "xterm-256color"}, False),
        ({}, False),
    ],
)
def test_truecolor_detection(monkeypatch: pytest.MonkeyPatch, environment: dict, expected: bool) -> None:
    """Only terminals that advertise 24-bit color get exact black and white."""
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert qr.supports_truecolor() is expected


class _Size:
    """What shutil.get_terminal_size returns, for the style-selection tests."""

    def __init__(self, columns: int) -> None:
        self.columns = columns


def test_auto_style_follows_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto picks plain when redirected, background when it fits, half when it does not."""
    rows = qr.qr_matrix(URLS[0], border=2)
    monkeypatch.setattr(qr.sys.stdout, "isatty", lambda: False)
    assert qr.resolve_qr_style("auto", rows) == "plain"
    monkeypatch.setattr(qr.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(qr.shutil, "get_terminal_size", lambda fallback=None: _Size(200))
    assert qr.resolve_qr_style("auto", rows) == "background"
    monkeypatch.setattr(qr.shutil, "get_terminal_size", lambda fallback=None: _Size(20))
    assert qr.resolve_qr_style("auto", rows) == "half"
    assert qr.resolve_qr_style("plain", rows) == "plain"


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("style", ["plain", "half", "background"])
def test_art_decodes_back_to_the_url(url: str, style: str) -> None:
    """Every style survives a terminal round trip: each module is painted where it belongs."""
    rows = qr.qr_matrix(url, border=2)
    assert decode_art(qr.qr_art(rows, style), style) == url


@pytest.mark.parametrize("url", URLS)
def test_16_color_art_decodes_back_to_the_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """The 16-color fallback is as scannable as truecolor."""
    monkeypatch.setattr(qr, "supports_truecolor", lambda: False)
    for style in ("half", "background"):
        rows = qr.qr_matrix(url, border=2)
        art = qr.qr_art(rows, style)
        assert "38;2" not in art and "48;2" not in art
        assert decode_art(art, style) == url


def test_png_carries_the_same_url(tmp_path) -> None:
    """The PNG fallback is a real, decodable image."""
    from . import qr_support

    numpy = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    target = tmp_path / "qr.png"
    qr.write_qr_png(URLS[1], str(target))
    assert target.stat().st_size > 0
    assert qr_support.decode(numpy.asarray(Image.open(target).convert("L"))) == URLS[1]
