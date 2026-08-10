"""``trug share``'s QR renderer.

The QR is the whole point of `trug share` on a phone — nobody types a 32-char
token. So the two ways it can silently fail both get a test: dropping the quiet
zone (renders beautifully, scans never) and overflowing the terminal (wraps into
noise that also scans never, and looks like a crash).
"""

from __future__ import annotations

import re

from trug import share

URL = "http://192.168.1.5:8000/?token=" + "a1B2c3D4" * 4  # a realistic 32-char token

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _visible(line: str) -> str:
    return ANSI.sub("", line)


def test_renders_a_block_qr_that_fits_the_terminal():
    out = share.render_qr(URL, cols=80)
    assert out is not None
    lines = out.splitlines()
    assert len(lines) > 8
    widths = {len(_visible(line)) for line in lines}
    assert len(widths) == 1, "every row must be the same width or the QR is skewed"
    assert widths.pop() <= 80


def test_returns_none_when_the_terminal_is_too_narrow():
    # A narrow ssh window: better to print the plain URL than a wrapped QR that
    # reads as a crash and can't be scanned anyway.
    assert share.render_qr(URL, cols=20) is None


def test_keeps_the_mandatory_quiet_zone():
    # Four light modules on every side. Without them scanners fail, and the
    # failure looks like "the QR is broken" rather than "the margin is missing".
    out = share.render_qr(URL, cols=200)
    assert out is not None
    lines = out.splitlines()
    # Two modules per character row, so a 4-module border is 2 blank rows.
    assert share.qr_size(URL) == len(_visible(lines[0]))
    for row in (lines[0], lines[1], lines[-1], lines[-2]):
        assert share.DARK not in row, "quiet zone row contains a dark module"


def test_size_accounts_for_the_border():
    # The number people reach for is the symbol size; the number that matters for
    # a terminal is the symbol plus both borders.
    assert share.qr_size(URL) >= 2 * share.QUIET_ZONE + 21


def test_render_is_pure_text_and_resets_colour():
    out = share.render_qr(URL, cols=80)
    assert out is not None
    # Colours are set explicitly rather than inherited, so the QR scans on a dark
    # terminal as well as a light one — and every line puts the terminal back.
    for line in out.splitlines():
        assert line.endswith(share.RESET)


def test_rendered_output_reconstructs_the_exact_qr_matrix():
    """Read the rendered block back and check it is the QR segno produced.

    Everything between the encoder and the phone is hand-rolled here — the
    half-block packing, the colour pairs, the border padding — and every bug in
    that layer produces a QR that looks perfect and scans never. So the output is
    parsed back into modules and compared against the encoder's own matrix.
    """
    import segno

    out = share.render_qr(URL, cols=200)
    assert out is not None

    # Each cell is "<fg><bg>▀": the foreground carries the upper module, the
    # background the lower one.
    cell = re.compile(r"\x1b\[(9?7|30)m\x1b\[(107|40)m▀")
    rendered: list[list[int]] = []
    for line in out.splitlines():
        top, bottom = [], []
        for fg, bg in cell.findall(line):
            top.append(1 if fg == "30" else 0)
            bottom.append(1 if bg == "40" else 0)
        rendered.append(top)
        rendered.append(bottom)

    expected = [[1 if m else 0 for m in row] for row in segno.make(URL, error="l", mode="byte").matrix]
    pad = share.QUIET_ZONE
    width = len(expected[0]) + 2 * pad
    want = (
        [[0] * width for _ in range(pad)]
        + [[0] * pad + row + [0] * pad for row in expected]
        + [[0] * width for _ in range(pad)]
    )
    # An odd module count means the last line's bottom half is padding, not data.
    assert rendered[: len(want)] == want
    assert all(cellv == 0 for row in rendered[len(want):] for cellv in row)


def test_main_prints_the_qr_and_exits_zero(capsys):
    assert share.main([URL, "--cols", "80"]) == 0
    assert share.DARK in capsys.readouterr().out


def test_main_exits_nonzero_without_printing_when_too_narrow(capsys):
    # The shell falls back to the plain URL on a non-zero exit, so printing a
    # partial QR here would double up with the fallback.
    assert share.main([URL, "--cols", "20"]) == 1
    assert capsys.readouterr().out == ""
