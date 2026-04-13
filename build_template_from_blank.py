"""
Regenerate ``template.dmt`` from ``blank.dmt`` (minimal DeLorme shell).

Requires: ``pip install extract-msg`` (GPL-3.0; dev-only — not a runtime dependency).

Run from the repo root::

    python scripts/build_template_from_blank.py

Edits ``template.dmt`` in place: expands Annotate.Filenames / ActiveFilenames buffers and
adds empty centerline draw streams (``Our CL CL (2)``, ``Other CL N CL (2)``) at a fixed
byte size so the app can replace geometry with olefile.

Afterward, refresh the copy baked into ``delorme_streams.py``::

    python _gen_embed.py

Then paste ``_embed_chunk.py`` over the ``_TEMPLATE_ZLIB_B64 = ( ... )`` block in
``delorme_streams.py`` (or skip if you only use a sidecar ``template.dmt``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import olefile  # noqa: E402
from extract_msg import OleWriter  # noqa: E402

from delorme_streams import (  # noqa: E402
    ANNOTATE_LINE_HEADER96,
    build_annotate_line_stream,
    pad_stream,
    kml_abgr_to_colorref,
)

# Same 96-byte header the app uses when encoding lines (real XMap object, not the old placeholder).
_HEADER96 = ANNOTATE_LINE_HEADER96

BLANK = _REPO / "blank.dmt"
OUT = _REPO / "template.dmt"

# How many centerline slots the app can fill (combined .dmt export).
N_CENTERLINE_SLOTS = 16
STREAM_SIZE = 65536
FN_PAD = 512
AF_PAD = 256


def _slot_name(i: int) -> str:
    if i == 0:
        return "Our CL CL (2)"
    return f"Other CL {i} CL (2)"


def main() -> None:
    if not BLANK.is_file():
        raise SystemExit(f"Missing {BLANK}")

    coords = [(35.0, -100.0), (35.001, -100.001)]
    cref = kml_abgr_to_colorref("ffffffff")
    body = build_annotate_line_stream(coords, cref, _HEADER96)
    padded = pad_stream(body, STREAM_SIZE)

    with olefile.OleFileIO(str(BLANK)) as ole:
        w = OleWriter()
        w.fromOleFile(ole)

    w.editEntry(
        ["DeLormeComponents", "DeLorme.Annotate.Workspace", "Annotate.Filenames"],
        data=b"\x00" * FN_PAD,
    )
    w.editEntry(
        ["DeLormeComponents", "DeLorme.Annotate.Workspace", "Annotate.ActiveFilenames"],
        data=b"\x00" * AF_PAD,
    )
    for i in range(N_CENTERLINE_SLOTS):
        w.addEntry(
            ["DeLormeComponents", "DeLorme.Annotate.Workspace", _slot_name(i)],
            data=padded,
        )

    w.write(str(OUT))
    print(f"Wrote {OUT} ({os.path.getsize(OUT)} bytes), {N_CENTERLINE_SLOTS} CL slots.")


if __name__ == "__main__":
    main()
