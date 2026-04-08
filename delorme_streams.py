"""
Build DeLorme XMap / Street Atlas style OLE stream payloads for LineString objects.

Coordinate encoding matches GPSBabel's DeLorme .an1 implementation (EncodeOrd / DecodeOrd).
See: https://github.com/GPSBabel/gpsbabel/blob/gpsbabel_1_7_0/an1.cc
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def kml_abgr_to_colorref(kml_color: Optional[str]) -> int:
    """
    KML LineStyle <color> is eight hex digits aabbggrr (alpha, blue, green, red).
    Windows COLORREF uses the same 24-bit layout: 0x00bbggrr.
    """
    default = 0x00FFFFFF
    if not kml_color:
        return default
    s = kml_color.strip().lower().replace("#", "")
    if len(s) == 8:
        _aa, bb, gg, rr = s[0:2], s[2:4], s[4:6], s[6:8]
    elif len(s) == 6:
        bb, gg, rr = s[0:2], s[2:4], s[4:6]
    else:
        return default
    try:
        r = int(rr, 16)
        g = int(gg, 16)
        b = int(bb, 16)
        return r | (g << 8) | (b << 16)
    except ValueError:
        return default


def kml_abgr_to_hex_display(kml_color: Optional[str]) -> str:
    """CSV-friendly #RRGGBB from KML LineStyle color."""
    cref = kml_abgr_to_colorref(kml_color)
    r = cref & 0xFF
    g = (cref >> 8) & 0xFF
    b = (cref >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def encode_ord_deg(deg: float) -> int:
    """GPSBabel EncodeOrd: int32(0x80000000 - int(deg * 2**23))."""
    scaled = int(round(float(deg) * 8388608.0))
    raw = ctypes.c_int32(0x80000000 - scaled).value
    return raw & 0xFFFFFFFF


PREFIX_FIRST = bytes.fromhex("0000000100000000")
PREFIX_MID = bytes.fromhex("6f00000100000000")
PREFIX_TERM = bytes.fromhex("6f000004000000000000000300000000")
TAIL3 = bytes.fromhex("000000")


def build_annotate_line_stream(
    coords_lat_lon: Sequence[Tuple[float, float]],
    colorref: int,
    header_template: bytes,
) -> bytes:
    """
    coords: (latitude, longitude) in WGS84 degrees.
    header_template: first 96 bytes copied from an existing DeLorme stream of the same layout.
    """
    if not coords_lat_lon:
        raise ValueError("No coordinates for line stream.")
    if len(header_template) < 96:
        raise ValueError("Header template must be at least 96 bytes.")

    header = bytearray(header_template[:96])
    # COLORREF little-endian at offset 72
    struct.pack_into("<I", header, 72, colorref & 0xFFFFFFFF)
    n = len(coords_lat_lon)
    if n < 256:
        header[95] = n

    parts: List[bytes] = [bytes(header)]
    for i, (lat, lon) in enumerate(coords_lat_lon):
        lon_i = encode_ord_deg(lon)
        lat_i = encode_ord_deg(lat)
        pair = struct.pack("<II", lon_i, lat_i)
        if i == 0:
            parts.append(PREFIX_FIRST + pair)
        else:
            parts.append(PREFIX_MID + pair)
    parts.append(PREFIX_TERM)
    parts.append(TAIL3)
    return b"".join(parts)


def pad_stream(data: bytes, target_len: int) -> bytes:
    if len(data) > target_len:
        raise ValueError(
            f"Encoded line ({len(data)} bytes) exceeds template stream size ({target_len} bytes). "
            "Use a template .dmt whose matching line stream is larger, or simplify the line."
        )
    if len(data) == target_len:
        return data
    return data + b"\x00" * (target_len - len(data))


_CL_RE = re.compile(r"^(.+) CL \(2\)$")


def is_draw_line_stream(name: str) -> bool:
    if not _CL_RE.match(name):
        return False
    lower = name.lower()
    if "note" in lower:
        return False
    if "combined access" in lower:
        return False
    if "agm" in lower and "final" in lower:
        return False
    return True


def stream_path_str(path: str | Sequence[str]) -> str:
    if isinstance(path, str):
        return path
    return "/".join(path)


def sort_cl_stream_names(names: Iterable[str]) -> List[str]:
    """Order: 'Our CL …' first, then 'Other CL 1', 'Other CL 2', …, then the rest alphabetically."""

    def key(n: str) -> Tuple[int, int, str]:
        if n.startswith("Our CL"):
            return (0, 0, n)
        m = re.match(r"^Other CL (\d+)", n)
        if m:
            return (1, int(m.group(1)), n)
        return (2, 0, n)

    return sorted({n for n in names if is_draw_line_stream(n)}, key=key)


def template_dmt_path() -> Path:
    return Path(__file__).resolve().parent / "template.dmt"


def list_annotate_cl_stream_paths(ole) -> List[str]:
    out: List[Tuple[str, str]] = []
    for s in ole.listdir():
        if not s:
            continue
        if isinstance(s, (list, tuple)):
            full = "/".join(str(x) for x in s)
            last = str(s[-1])
        else:
            full = str(s)
            last = full.split("/")[-1]
        if is_draw_line_stream(last):
            out.append((last, full))
    names_sorted = sort_cl_stream_names([t[0] for t in out])
    rank = {n: i for i, n in enumerate(names_sorted)}
    out.sort(key=lambda t: rank[t[0]])
    return [full for _, full in out]


def build_dmt_bytes(
    template_path: Path,
    ordered_lat_lon_lines: Sequence[Sequence[Tuple[float, float]]],
    colorrefs: Sequence[int],
) -> bytes:
    """
    Clone template_path OLE file and replace draw line streams in sort order
    (Our CL first, then Other CL 1, …) with encoded geometry. Streams are padded
    with zeros to match existing stream sizes.
    """
    import os
    import shutil
    import tempfile

    import olefile

    if len(ordered_lat_lon_lines) != len(colorrefs):
        raise ValueError("Each line must have a color.")

    with olefile.OleFileIO(str(template_path)) as ole:
        stream_paths = list_annotate_cl_stream_paths(ole)
        if len(stream_paths) < len(ordered_lat_lon_lines):
            raise ValueError(
                f"Template has {len(stream_paths)} draw line stream(s), but "
                f"{len(ordered_lat_lon_lines)} line(s) were produced. "
                "Add empty draw objects in XMap and save a larger template, or merge lines."
            )
        stream_paths = stream_paths[: len(ordered_lat_lon_lines)]
        headers = []
        sizes = []
        for sp in stream_paths:
            data = ole.openstream(sp).read()
            sizes.append(len(data))
            headers.append(data[:96])

    _fd, tmp = tempfile.mkstemp(suffix=".dmt")
    os.close(_fd)
    try:
        shutil.copyfile(str(template_path), tmp)
        with olefile.OleFileIO(tmp, write_mode=True) as ole_w:
            for sp, coords, cref, hdr, sz in zip(
                stream_paths, ordered_lat_lon_lines, colorrefs, headers, sizes
            ):
                payload = build_annotate_line_stream(coords, cref, hdr)
                padded = pad_stream(payload, sz)
                ole_w.write_stream(sp, padded)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
