"""
DeLorme .dmt export helpers.

A .dmt file is a proprietary Microsoft OLE “compound document” used by DeLorme / Garmin
mapping tools. You cannot build one from lat/lon alone; this module starts from
``template.dmt`` (a minimal blank DeLorme shell beside this file) and writes your
LineString geometry into it.

Coordinate encoding matches GPSBabel’s DeLorme .an1 EncodeOrd / DecodeOrd.
See: https://github.com/GPSBabel/gpsbabel/blob/gpsbabel_1_7_0/an1.cc
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
import tempfile
import itertools
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


def max_vertices_for_stream_size(stream_size: int) -> int:
    """How many lat/lon points fit in a draw stream of this byte size (header + vertices + terminator)."""
    # build_annotate_line_stream: 96 + 16 * (n_points + 1) + 3  (terminator block + tail)
    avail = stream_size - 96 - 3
    if avail < 32:
        return 0
    blocks = avail // 16
    return max(0, blocks - 1)


def uniform_sample_coords(
    coords: Sequence[Tuple[float, float]], max_points: int
) -> List[Tuple[float, float]]:
    """Reduce vertex count while keeping first/last and spreading samples along the polyline."""
    if len(coords) <= max_points:
        return list(coords)
    if max_points < 2:
        return [coords[0], coords[-1]]
    n = len(coords)
    idx = [round(i * (n - 1) / (max_points - 1)) for i in range(max_points)]
    out: List[Tuple[float, float]] = []
    for i in idx:
        p = coords[i]
        if not out or p != out[-1]:
            out.append(p)
    return out


def _find_stream_permutation(
    coords_list: List[List[Tuple[float, float]]],
    colorrefs: Sequence[int],
    headers: Sequence[bytes],
    sizes: Sequence[int],
) -> Optional[Tuple[int, ...]]:
    """
    Return permutation p where stream slot j gets user line index p[j], or None if impossible.
    Tries all permutations for n <= 8; otherwise one greedy: longest lines to largest streams.
    """
    n = len(coords_list)
    if n == 0:
        return ()

    def fits(perm: Tuple[int, ...]) -> bool:
        for j in range(n):
            u = perm[j]
            ln = len(build_annotate_line_stream(coords_list[u], colorrefs[u], headers[j]))
            if ln > sizes[j]:
                return False
        return True

    if n <= 8:
        best: Optional[Tuple[int, ...]] = None
        best_score: Optional[int] = None
        for perm in itertools.permutations(range(n)):
            if not fits(perm):
                continue
            # Prefer assignment closest to upload order (line i → stream i).
            score = sum(abs(perm[j] - j) for j in range(n))
            if best_score is None or score < best_score:
                best_score = score
                best = perm
        return best

    stream_order = sorted(range(n), key=lambda j: sizes[j], reverse=True)
    line_order = sorted(range(n), key=lambda i: len(coords_list[i]), reverse=True)
    perm_slots = [0] * n
    for rank in range(n):
        perm_slots[stream_order[rank]] = line_order[rank]
    perm = tuple(perm_slots)
    return perm if fits(perm) else None


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
    """Path to ``template.dmt`` beside this module (committed blank-based shell)."""
    return Path(__file__).resolve().parent / "template.dmt"


def resolve_template_dmt_path() -> Path:
    """
    Path to the DeLorme OLE shell used for .dmt export.

    Expects ``template.dmt`` next to this module (see repo). Regenerate from ``blank.dmt``
    using ``scripts/build_template_from_blank.py`` if you replace the blank shell.
    """
    p = template_dmt_path()
    if p.is_file():
        return p
    raise FileNotFoundError(
        f"Missing {p.name} next to delorme_streams.py. "
        "Copy the repository template or run scripts/build_template_from_blank.py."
    )


_ANNOTATE_WORKSPACE = "DeLormeComponents/DeLorme.Annotate.Workspace"
_STREAM_ANNOTATE_FILENAMES = f"{_ANNOTATE_WORKSPACE}/Annotate.Filenames"
_STREAM_ANNOTATE_ACTIVE_FILENAMES = f"{_ANNOTATE_WORKSPACE}/Annotate.ActiveFilenames"


def _annotate_filename_type_codes(n: int) -> List[int]:
    """
    Per-layer type dword values observed in a stock DeLorme template for draw objects:
    first object 6, second 1, third+ 0. Must match ``n`` display names.
    """
    if n <= 0:
        return []
    out: List[int] = []
    for i in range(n):
        if i == 0:
            out.append(6)
        elif i == 1:
            out.append(1)
        else:
            out.append(0)
    return out


def build_annotate_filenames_centerlines_only(display_names: Sequence[str]) -> bytes:
    """
    Binary body for ``Annotate.Filenames``: only in-document centerline layers.

    The stock template also lists an external ``.an1`` path, Notes, Combined Access,
    and Final AGMs — those entries make XMap prefer missing files and hide embedded
    centerlines. This builder lists **only** the given display names (OLE stream
    leaf titles like ``Our CL CL (2)``).
    """
    n = len(display_names)
    if n == 0:
        raise ValueError("Need at least one centerline display name.")
    kinds = _annotate_filename_type_codes(n)
    parts: List[bytes] = []
    for kind, name in zip(kinds, display_names):
        s = name.encode("ascii")
        parts.append(struct.pack("<II", kind, len(s)))
        parts.append(s)
    # Trailing dword observed in template streams (value 1).
    parts.append(struct.pack("<I", 1))
    return b"".join(parts)


def build_annotate_active_filenames(active_display_name: str) -> bytes:
    """
    Binary body for ``Annotate.ActiveFilenames``: active layer is the first centerline.

    Replaces the template default that points at ``C:\\...\\Final AGMs63.an1``, which
    breaks display when that file does not exist.
    """
    s = active_display_name.encode("ascii")
    return struct.pack("<II", 1, len(s)) + s


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
) -> Tuple[bytes, str]:
    """
    Clone template_path OLE file and replace draw line streams with encoded geometry.

    Lines are matched to template streams by **permutation**: the longest polyline is
    written to the largest stream slot when needed, so order in the KMZ zip may differ
    from Our CL / Other CL stream names in the .dmt.

    If no assignment fits, vertices are **uniformly subsampled** along each line until
    everything fits (see returned note string).

    Returns ``(file_bytes, note)`` where ``note`` is non-empty if subsampling occurred.
    """
    import os
    import shutil
    import tempfile

    import olefile

    if len(ordered_lat_lon_lines) != len(colorrefs):
        raise ValueError("Each line must have a color.")

    n = len(ordered_lat_lon_lines)

    with olefile.OleFileIO(str(template_path)) as ole:
        stream_paths = list_annotate_cl_stream_paths(ole)
        if len(stream_paths) < n:
            raise ValueError(
                f"Template has {len(stream_paths)} draw line stream(s), but "
                f"{n} line(s) were produced. "
                "Add empty draw objects in XMap and save a larger template, or merge lines."
            )
        stream_paths = stream_paths[:n]
        headers = []
        sizes = []
        for sp in stream_paths:
            data = ole.openstream(sp).read()
            sizes.append(len(data))
            headers.append(data[:96])
        annotate_filenames_size = len(ole.openstream(_STREAM_ANNOTATE_FILENAMES).read())
        annotate_active_filenames_size = len(
            ole.openstream(_STREAM_ANNOTATE_ACTIVE_FILENAMES).read()
        )

    coords_list: List[List[Tuple[float, float]]] = [list(line) for line in ordered_lat_lon_lines]
    note = ""
    attempts = 0
    while True:
        perm = _find_stream_permutation(coords_list, colorrefs, headers, sizes)
        if perm is not None:
            break
        u = max(range(n), key=lambda i: len(coords_list[i]))
        if len(coords_list[u]) <= 2:
            raise ValueError(
                "Cannot fit these lines into the DeLorme template (streams too small). "
                "Use a custom template.dmt with larger draw objects, or fewer/shorter lines."
            )
        new_n = max(2, (len(coords_list[u]) * 2) // 3)
        coords_list[u] = uniform_sample_coords(coords_list[u], new_n)
        attempts += 1
        if attempts == 1:
            note = (
                "Some lines were simplified (fewer vertices) so they fit the built-in "
                "DeLorme template size limits."
            )
        if attempts > 300:
            raise ValueError(
                "Could not fit geometry into the DeLorme template after simplification."
            )

    _fd, tmp = tempfile.mkstemp(suffix=".dmt")
    os.close(_fd)
    try:
        shutil.copyfile(str(template_path), tmp)
        with olefile.OleFileIO(tmp, write_mode=True) as ole_w:
            for j in range(n):
                u = perm[j]
                payload = build_annotate_line_stream(coords_list[u], colorrefs[u], headers[j])
                padded = pad_stream(payload, sizes[j])
                ole_w.write_stream(stream_paths[j], padded)
            # Drop sample-template layer list + external .an1 pointer so XMap shows
            # embedded centerlines only (see build_annotate_filenames_centerlines_only).
            display_names = [stream_path_str(sp).split("/")[-1] for sp in stream_paths]
            fn_body = build_annotate_filenames_centerlines_only(display_names)
            af_body = build_annotate_active_filenames(display_names[0])
            ole_w.write_stream(
                _STREAM_ANNOTATE_FILENAMES,
                pad_stream(fn_body, annotate_filenames_size),
            )
            ole_w.write_stream(
                _STREAM_ANNOTATE_ACTIVE_FILENAMES,
                pad_stream(af_body, annotate_active_filenames_size),
            )
        with open(tmp, "rb") as f:
            return f.read(), note
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
