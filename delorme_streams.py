"""
DeLorme .dmt export helpers.

A .dmt file is a proprietary Microsoft OLE “compound document” used by DeLorme / Garmin
mapping tools. You cannot build one from lat/lon alone; this module starts from a minimal
valid shell (optional template.dmt beside this file, or the built-in zlib payload below)
and writes your LineString geometry into it.

Coordinate encoding matches GPSBabel’s DeLorme .an1 EncodeOrd / DecodeOrd.
See: https://github.com/GPSBabel/gpsbabel/blob/gpsbabel_1_7_0/an1.cc
"""

from __future__ import annotations

import atexit
import base64
import ctypes
import os
import re
import struct
import tempfile
import zlib
import itertools
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Temp file for the materialized .dmt when using built-in or .zlib payload.
_materialized_template: Optional[Path] = None

_ZLIB_B64 = (
    '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'
    'Gf2hiJZ/LW186PXfSusf/WNh43kWau1xKhZur43PoVp+Bk/QynMqFm6gpTeuX6k9/8Upav18mKKOn4ta/XzQrv+gtdcp7fpdU/61'
    '/Fmu1c/QGOqrh8aptfryjiU+o9aeGbX89PZx0cpnjO8J2viupY3vVhpP0PLD+ojKqXLCGOJ1eRIbH9bkhyFfHgJ30e43K4b7p5iq'
    '5uepcYT2PAWmxs54fUKtf2TU6jum9jiljaeHGmN522tstH+E1h8KaOWJiCV/vb1ctPGeMIbxPkFrv/5ae8XWvjlj6N+1Yum/xvxQ'
    'RetvqbX+4K2lR/n3Sos3+gvKYyM9yk8j/3UaT9Hy0xnnY+N5l2r5o7x20eZ743n7aPODrm881dLbJ//1+W6api/009hF44TafJIw'
    'hvtf0cobqs13+nyp6yvrtPlqaSz6kIn1uSGaftNWy2+Kpu+sm/yv58+hMZRvaSz6YEmt/O219DViaK8ftfpZqtVnsBav94/gOOr7'
    'Siz9Q9eH+sXRH4z6HhJLe+yPof1+jEVf1p9/mpZ/A43bxxC/VKv/pbG0R0z1E6yNPxwfhv6ZWZsfdHli18b3lcn/Wr5c1tLP0tiI'
    'D9Xkg3cs8k2Xvy7a/GFcf1G7Xp8/FsYx/+zS+GIs88MrbT68nfPP88dDLR7nw8sav9LYeJ4PmnzX8/8w5V/PZ8b1KbT8MP8aWvl0'
    '/aeGph/m1Fi3bw5r9kyUxgnjyE/nkl/ICbX50ehvfbT+nWSS2h5zJ56B789byGGSyheBB2r9tYvWn89r8uZ0bpX986jycF4edXx3'
    'zqvaw8/Y3r8J93craKFVwCMKW2jEJNU/gfHeJSyUHvK7UUq9f5uyan0lqqD2D/fKFtoG3E9YaDj4D15Vt9Da6VDeWhbqCP4Gw39Y'
    'fKbkhQ0t9ATYr6mFFoC/Yo2/hbrOllw3wEKl5kiu085CjuDvMPy3T4AfdbPQCvCPjOlpoduw/yuon4VeLAB/1CALPQb/SqXhFnq+'
    'SLJttOqPOTXBQuOWSO4z1UJ1lkouPMtCJcGfEz7Pouw3G7XYQilXgL9nuYWWAc9cbaG3wCvXW2gR+ItObubrV8H+t+0WegZ8YbeF'
    'BqyG8bDPQj8Djzug8v5DKs87onL3YyqHnlD9WStPW6g6cPJgC00FfnmOywec/byFXNf+a05+Xk3/nq+/C3yDeR/wAuZtwB2Zl63B'
    '9znV+NRavJ4+6Jxa/hla/EaNF2jphzOPjSO/bVp+57Ty4vNV1tKX1jhIq9+TzD5rVbZp9YnpvzuvpjfaC9vDSWufG1p+RnvZtPZG'
    'PqjlN0O7v94+w+Noz+EaH9T4pHa93j4HtfZ5qcUb/XGqVl+Yf73zavtMOa+239EY+K6WHq+fo91vjhav36/8efX53mvlvxFHf+0Y'
    'A5/T6utZLPGNtPG4MYb+Z4slfrjWH4I0bhQHx3X9cK3/d9TYyC+Nlj7PWrW8eWLJr3QM+eH9F2j1M1yrv+Ha+D6p8Y0Yxv++WOTX'
    'nWC1P8wJVtMvDVbvr/NrLb1FK/9rLf1RLX017f7pNS6gcVft+q4xpJ+qzSfLNMbxW565byzcVUs/R+OtWvpgLd6i1XdurT0Sa+0b'
    'rD3fnTjqf2kc7TVGe/4xWnqdm2vX6/VfLY769tTiPbX6MOq/nZZfO62++2r13VfrPzrrz7dNex58vj4x1I/e//X+ejeW8bI0Bt4X'
    'R36xjZfEmvwzxs8z7f42Lb9nsdxvTgzlO/cF3DWG8TpVGy/LYpEfW7X76/IiNPjP+pXOU2Phjtp8rM9X2bX5NLs2n+rys6N2f0sM'
    '91+m6VNTNW6nsX8s8Yk1DtbGg1F/JbT+6q/Vv7/W3/Xx1TeW8dUnBnm2TGufZVp59fbT+3Ns/T2m+UOfL2yxxOvjJa75Zqt2/VKN'
    'j8bQX8dq6ZfFMt718XUnhv49VSvvWC1+rHZ9bPOL3j5HtecNDf6zPLmr9e9nmj6L/FQbL3RRjd+qcfiFP1+/TRtfkavV+a/TarX8'
    'M4G9z6r9PclptT/vO6Hai6ePqvblxMPq/Q4ftJAV0sc7oHINtm8TAOfaa6FHcL3jLgsFALfZZqF1YC9v22ShW2BfH1un7s+4uspC'
    'x8Ee92V7PRLs+V5sz9cBPjPfQuvB/s89h/MH/8DBGRZKCrx0qoUKgT+h9iQL9QJ/w6pxFtoB/oiCozk98I7hFroM/otkQyzUGnjN'
    'AAudAH+HpZ+FEgFX6WWhLuAfORRooQvgP0nQ1UKZgMd2tNAg8LeY2lnoBfhn9gRYqAPwDy0sdAf8OdmbWcgD+GgjCzUC/4+x35fA'
    'P1ShjupPMvZXDQZ/00ey0A3wR62vYqHUwHUrqv7PwuXU9f3ypVV/2NPi3J+AaxdV/WvWQhYaAJw0v+pvu5fHQhHAxvtCJbT1C1yf'
    'Kaf5103MY6aq/svy09X1hiVQP485/2FQ30nzWmgUtGeDfBZaDv1lIpf3FfS/cwXU/m48H9pHAwpb6MoG6C9cH0e3wPuPxSy0BvZH'
    'Zef6W7wH39dkhv1WU0paaOJhyR5c/xOPw/vVZSy08rS6/+vgOfB3cfuNh/1iLhUslAr2lyXj9p50Q3KmSha6dlv1Z16C/Wy5uL+s'
    'g/1uTataaNMTWM8RFlr7HOqb+9vJn8EfW91CIa8lm2uo+/OM/X654H3OzjW5PWC/3yXmBmHq/saksL/wEHOdcDW+M/Bu5llaejPs'
    'X5zJvExjk7afEvdjFqyl7q/0Z84L75+61bZQN9jfuYg50kHdD/oQ9oMOqGuh9GbJ7bT9pMZ+/x/c1fcf6lrV908qecP7nszF4kvO'
    '3NhCORPCfvEmFqqaRN0f2y255K4sfyakgvddm3P7ppFM/ty+6SW3ZPn1BPbjUksLZcum7p8OhP28a1j+7ckjeXlr1ofyw/uWbXg+'
    'LKzuH34I79smbM/yoKS6/7g87EcuyPI3qLxkt048X1SSvJm5UlWV3WA/dEa+vg7sl67B98tSSy0f7r8+EqDuz3bh560N6UO4vq7W'
    'VPcnY/zGpur+8SBmG/AC5jR1VM4TC5+MI35jDPkhG/cvA5yb2Rn2/3sZ8fB+wHDmPvXV/lMF3if43l/lE9xfttVX978XAE7G9ZkA'
    '2PEvsGcsHP8fxt9r/Jg5op7av7A+RrH+0La+2r+XAi9j/aMacJ5eavtEs36zCdqz9SgL9YT+58n6VVLo7ycWWGg7vL++bI2FPlWB'
    '9yV2WigtjKcmrJ9WhvG2hPXnNjAe09yzUCsYr0dfWag5vB9QJ5zLC+8HVHZ1pQwFJTdI6Er34P2AZulcaVIueB85jytVyg79r5Qr'
    'OcL7+ymru9LeDCCfmrpSUFqoj46ulMMPxkNfV3KA9wW2j3KlVSgvZ7pSd5Cn+5e7UjmQt/atrpQJ5LH1sCt5gLzeeN6VvECe37rt'
    'ShEu8D7CC1d64wT5h7hSARO8T+joRjNhvgmyuinvB7RP5KbMjyP83Cgf7LfPlMmNBsL++Q053MjlE+6fdqOmsJ/+UUE3EsD1i7jR'
    'uw+SLxZ1ox+A3xRzo0rAx5gLxcLbvpKX/g2M5Z/7lTzmK7nPF3I3jZt/Y672lVzhK7lADFwT2DMO/lD06/jhF/Kufziv/Eo+VVQd'
    'PxHM0e/V9l/zXpUP2d7j+yRuNOwdnO9S0o22vpV8oIwbbYD3d9ZUdKPRYE9EkZtyPsyIum40A+yPlk3dqMcL8D+0caNO8H7Pwm5u'
    '5AP2zPt+bjQX7J3NI9zoI9hDmae4UV54v2fLfDeqcRPsgzVutOWa5BU73cjxCvpz3MgP7LM2F93oJdhvz++60dozZ+B7MG404iS8'
    'zxXhRvWPgb/TzZ2uwfs7QxO7K+/vrMjoTsPg/Z2eBdwpG7y/8305d7oD5/lUqO1OwzdD/bV0p2po33Z3V/yXRwa70yywj9dNcicz'
    '+HeuL3Sn9mBfF9jorvhPAve7U13wdxQJdqfXYM/XuOVO6cHef/jcXd0fGOKu7C865eRBRyH9JG8Pyg/+AOcUHuQF96+cyYPigX9p'
    'bB4Pxb+2q6gH1YL3mRqV8aB++ySXquRBVaB9hgoPir6M76d5UIb7+L6fByWH988i63pQa5ifGzf0oBnw/uCdJh503hvtLw+6mhzt'
    'K36ezHCeTVsPagD21KMO/PylJB/q4kHTwP4pE+hBPqAvVujpQZPhPJa1vT3IvS28L9zXg47D+S+5+3vQ3G6gHw72oGPAnUd40JAu'
    'cL9xHnQNrj81xYOKdYTzlGZ70GE4T+bIQn6edmDvLfegMDhfZt8qD6oE3GOtB40Drr3egwKBL65X0x9iLhQHY/pNcXB/5hp4/o0W'
    '76rFN2bup/F84DLMy7Xn2QHsoMWvW/dnnh8L39XSX9Xix6xT6/M/zUPi4G5xcIF1av3m0OK/lutr9fetucBf4PmxsGcc7Kw9TwqN'
    'K2jpde6mlWeIxt1i4PlfwUPiuN/cOHjIV3Lzb/w8f4WX/4085G9mQ97siIWPaXz1L/DyWOLfrPuzfIyN9fRJOP4EcJY4OM//8X+U'
    'y/yF9Oc1vq9d/06Lf6e17/lY+oOefyctvn8M95+vze/LY7k+j9Y/XbX0ZbT4LDGk1/srPv/K9ao+97P2/Be18kesV+eDIhs9aBKc'
    'V/l+swc1DED/IOfXEvzrBz3ogT/MP8EedAPO90t2w4Nawfl+VZ54KOexDXvnofg/q0Z70M/gzyzt7klVwZ95K4Enda8O522k9qRX'
    'Avyb2T1pFPgznxX2pAMVJT8o70le4M/0r+1Jk8GfWcrfk/qDP3NkZ08aDP5Myw+etAr8mSNHe5IN/JmZZ3pSTtDPVy/zpLywHmLZ'
    '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'
    'cN/aJPYt6H8B5uD9OA=='
)

def _builtin_shell_dmt_bytes() -> bytes:
    """Expand the built-in zlib (base64 above) to a full .dmt OLE file bytes."""
    return zlib.decompress(base64.b64decode(_ZLIB_B64))


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
    """Path to template.dmt if present beside this module (may not exist on cloud)."""
    return Path(__file__).resolve().parent / "template.dmt"


def bundled_template_zlib_path() -> Path:
    return Path(__file__).resolve().parent / "template.dmt.zlib"


def resolve_template_dmt_path() -> Path:
    """
    Path to a readable DeLorme OLE file we can patch.

    Order: ``template.dmt`` beside this module (optional override), else
    ``template.dmt.zlib``, else the built-in zlib payload (base64 in this file).
    """
    global _materialized_template

    base = Path(__file__).resolve().parent
    plain = base / "template.dmt"
    if plain.is_file():
        return plain

    if _materialized_template is not None and _materialized_template.is_file():
        return _materialized_template

    zpath = bundled_template_zlib_path()
    if zpath.is_file():
        raw = zlib.decompress(zpath.read_bytes())
    else:
        raw = _builtin_shell_dmt_bytes()
    fd, name = tempfile.mkstemp(suffix=".dmt", prefix="kmz_cl_template_")
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    tmp_path = Path(name)
    _materialized_template = tmp_path

    def _cleanup(p: Path = tmp_path) -> None:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_cleanup)
    return tmp_path


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
