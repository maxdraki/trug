"""Terminal QR rendering for ``trug share``.

Nobody types a 32-character token into a phone, so the share link is a QR or it
is nothing. This lives in the server image rather than on the host because the
host is a Mac or a Pi with no Python packaging story worth inflicting on
someone; ``trug share`` runs it through the container's own interpreter, the
same trick the doctor's repair path uses. The running server never imports it.

Two failure modes drive the design, because both produce a QR that looks fine
and scans never:

* **No quiet zone.** Scanners need four light modules of margin. Trimming them
  makes the output tidier and the QR useless.
* **A terminal narrower than the symbol.** The rows wrap, the pattern shears,
  and it reads as a crash. Better to say "too narrow" and let the caller print
  the plain URL.

Rendering uses the upper-half block with *explicit* foreground and background
colours, one character per module and two module rows per line. Explicit colours
mean the QR is dark-on-light on a dark terminal too, where an inherited-colour
QR comes out inverted and most scanners refuse it. One character per module (not
the usual two spaces) is what keeps a realistic share URL inside 80 columns.
"""

from __future__ import annotations

import argparse

import segno

#: Light modules of margin required on every side (QR spec).
QUIET_ZONE = 4

#: Upper-half block: the top half takes the foreground colour, the bottom half
#: the background, so one character carries two module rows.
_HALF = "▀"
RESET = "\x1b[0m"
_WHITE_FG, _BLACK_FG = "\x1b[97m", "\x1b[30m"
_WHITE_BG, _BLACK_BG = "\x1b[107m", "\x1b[40m"

#: A rendered dark module, for callers that want to assert one is present.
DARK = _BLACK_FG


def _matrix(url: str) -> list[list[int]]:
    """The QR modules with the quiet zone already padded in, 1 = dark.

    Error correction ``L`` deliberately: a LAN URL is short-lived and displayed
    on a clean screen, so the extra correction levels buy nothing and cost
    columns — the difference between fitting an 80-column terminal and not.
    """
    qr = segno.make(url, error="l", mode="byte")
    rows = [[1 if m else 0 for m in row] for row in qr.matrix]
    width = len(rows[0]) + 2 * QUIET_ZONE
    blank = [0] * width
    padded = [[0] * QUIET_ZONE + row + [0] * QUIET_ZONE for row in rows]
    return [blank[:] for _ in range(QUIET_ZONE)] + padded + [blank[:] for _ in range(QUIET_ZONE)]


def qr_size(url: str) -> int:
    """Total width in terminal columns, quiet zone included."""
    return len(_matrix(url)[0])


def render_qr(url: str, cols: int) -> str | None:
    """The QR as printable lines, or ``None`` if it will not fit in ``cols``."""
    matrix = _matrix(url)
    if len(matrix[0]) > cols:
        return None
    # Pad to an even number of rows so the final line has a bottom half to draw.
    if len(matrix) % 2:
        matrix.append([0] * len(matrix[0]))

    lines = []
    for top, bottom in zip(matrix[::2], matrix[1::2]):
        line = []
        for t, b in zip(top, bottom):
            fg = _BLACK_FG if t else _WHITE_FG
            bg = _BLACK_BG if b else _WHITE_BG
            line.append(f"{fg}{bg}{_HALF}")
        lines.append("".join(line) + RESET)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trug.share",
        description="Render a URL as a terminal QR code for `trug share`.",
    )
    parser.add_argument("url")
    parser.add_argument(
        "--cols",
        type=int,
        default=80,
        help="terminal width; the QR is suppressed rather than wrapped if it won't fit",
    )
    args = parser.parse_args(argv)
    out = render_qr(args.url, args.cols)
    if out is None:
        # Silent by design: exit 1 tells the caller to print the plain URL, and
        # anything on stdout here would double up with that fallback.
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
