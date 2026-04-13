"""
DeLorme .dmt export helpers.

A .dmt file is a proprietary Microsoft OLE “compound document” used by DeLorme / Garmin
mapping tools. You cannot build one from lat/lon alone; this module starts from
a minimal blank DeLorme shell (``template.dmt`` beside this file, or embedded in this module) and writes your
LineString geometry into it.

Coordinate encoding matches GPSBabel’s DeLorme .an1 EncodeOrd / DecodeOrd
(line vertices: ``lat = EncodeOrd(latitude)``, ``lon = EncodeOrd(-longitude)``).
See: https://github.com/GPSBabel/gpsbabel/blob/gpsbabel_1_7_0/an1.cc
"""

from __future__ import annotations

import atexit
import base64
import ctypes
import itertools
import os
import re
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Materialized .dmt when using embedded template (Streamlit / copy-paste deploy).
_materialized_template: Optional[Path] = None

# Bumped when DMT export logic changes; copied into the zip as ``_EXPORT_BUILD_INFO.txt`` so you
# can confirm Streamlit deployed the matching ``delorme_streams.py`` (not a cached/old build).
DMT_EXPORT_BUILD_ID = "20260409-olewriter-dmt-v3"

_TEMPLATE_ZLIB_B64 = (
    'eNrt2wd0VOWi9+GdoqJYwIbdXBVBBQt2sUXsoqLYO2rUKCaaYC9g7733a++F2Luo2BV7V+y994I3'
    '33+S4ZjDOQflXM9lfWs9j+u397x7z8ybPXtmEuLk6VFd37pw+CxvF+NYvqgq/qd18mLSDtu6lmvT'
    'pShmyaoi/U9ra+vYzaVxZapK1WmSVLqPyVKnNHmaInVOU6ap0tRpmva7LFr5P/XbX9C0OW/TpenT'
    'DGnG1C3NlGYuPz9KzZbLs6c50pzl7f+V9Vxp7jRP6p7mTT1SzzRfmj8tkHql3mnBtFBaOC2S+qRF'
    '02Jp8bREWjItlZZOy6S+adm0XNtzuyhWSCum2rRS6pdWTqukVdNqafW0RlozrZX6p7XTOmndNCCt'
    'l9ZPA9MGacO0Udo4bZI2TZulzdMWacu0Vdo6bZMGpW3Tdmn7VJd2SDumnVJ92jntkganXVNDaky7'
    'pd1TU2pOQ9Ieac+0V9o77ZP2Tful/dMB6cA0NA1LB6WD0yHp0HRYOjwdkY5MR6Wj0zHp2HRcOj6d'
    'kE5MJ6WT0ynp1HRaOj2dkc5MZ6Wz0znp3HReOj9dUD7/F2Z9Ubo4XZIuTZely9MV6cp0Vbo6XZOu'
    'Tdel69MNaXhqSTemm9LN6ZZ0a7ot3Z7uSHemu9Ld6Z50b7ovjUj3pwfSg2lkeig9nB5Jj6bH0uPp'
    'ifRkeiqNSk+nZ9Kz6bn0fHohvZheSi+nV9Kr6bX0enojvZlGp7dS6Y33nfRuei+9nz5IH6aP0sfp'
    'k/Rp+ix9nr5IX6av0tfpm/Rt+i59n35IP6af0s/pl/RrGpN+Kz3upffZ8pt1RapMVak6TZImTZOl'
    'TmnyNEXqnKZMU6Wp0zSpS+qapk3TpenTDGnG1C3NlGZOs6RZ02xp9jRHmrOi/PrPeq40d5ondU/z'
    'ph6pZ5ovzZ8WSL1S77RgWigtnBZJfdKiabG0eFoiLZmWSkunZVLftGxaLi2fVkgrptq0UuqXVk6r'
    'pFXTamn1tEZaM62V+qe10zpp3TQgrZfWTwPTBmnDtFHaOG2SNk2bpc3TFmnLtFXaOm2TBqVt03Zp'
    '+1SXdkg7pp1Sfdo57ZIGp11TQ2pMu6XdU1NqTkPSHmnPtFfaO+2T9k37pf3TAenANDQNSwelg9Mh'
    '6dB0WDo8HZGOTEelo9Mx6dh0XDo+nZBOTCelk9Mp6dR0Wjo9nZHOTGels9M56dx0Xjo/XVA+/xdm'
    'fVG6OF2SLk2XpcvTFenKdFW6Ol2Trk3XpevTDWl4akk3ppvSzemWdGu6Ld2e7kh3prvS3emedG+6'
    'L41I96cH0oNpZHooPZweSY+mx9Lj6Yn0ZHoqjUpPp2fSs+m59Hx6Ib2YXkovp1fSq+m19Hp6I72Z'
    'Rqe30tvpnfRuei+9nz5IH6aP0sfpk/Rp+ix9nr5IX6av0tfpm/Rt+i59n35IP6af0s/pl/RrGpN+'
    'Kz3uqbX8g1pFqkxVqTpNkiZNk6VOafI0ReqcpkxTpanTNKlL6pqmTdOl6dMMacbULc2UZk6zpFnT'
    'bGn2NEeas7L8+s96rjR3mid1T/OmHqlnmi/NnxZIvVLvtGBaKC2cFkl90qJpsbR4WiItmZZKS6dl'
    'Ut+0bFouLZ9WSCum2rRS6pdWTqukVdNqafW0RlozrZX6p7XTOmndNCCtl9ZPA9MGacO0Udo4bZI2'
    'TZulzdMWacu0Vdo6bZMGpW3Tdmn7VJd2SDumnVJ92jntkganXVNDaky7pd1TU2pOQ9Ieac+0V9o7'
    '7ZP2Tful/dMB6cA0NA1LB6WD0yHp0HRYOjwdkY5MR6Wj0zHp2HRcOj6dkE5MJ6WT0ynp1HRaOj2d'
    'kc5MZ6Wz0znp3HReOj9dUD7/F2Z9Ubo4XZIuTZely9MV6cp0Vbo6XZOuTdel69MNaXhqSTemm9LN'
    '6ZZ0a7ot3Z7uSHemu9Ld6Z50b7ovjUj3pwfSg2lkeig9nB5Jj6bH0uPpifRkeiqNSk+nZ9Kz6bn0'
    'fHohvZheSi+nV9Kr6bX0enojvZlGp7fS2+md9G56L72fPkgfpo/Sx+mT9Gn6LH2evkhfpq/S1+mb'
    '9G36Ln2ffkg/pp/Sz+mX9Gsak34rPe6ptfyPtIpUmapSdZokTZomS53S5GmK1DlNmaZKU6dpUpfU'
    'NU2bpkvTpxnSjKlbminNnGZJs6bZ0uxpjjRnVfn1n/Vcae40T+qe5k09Us80X5o/LZB6pd5pwbRQ'
    'WjgtkvqkRdNiafG0RFoyLZWWTsukvmnZtFxaPq2QVky1aaXUL62cVkmrptXS6mmNtGZaK/VPa6d1'
    '0rppQFovrZ8Gpg3ShmmjtHHaJG2aNkubpy3SlmmrtHXaJg1K26bt0vapLu2Qdkw7pfq0c9olDU67'
    'pobUmHZLu6em1JyGpD3SnmmvtHfaJ+2b9kv7pwPSgWloGpYOSgenQ9Kh6bB0eDoiHZmOSkenY9Kx'
    '6bh0fDohnZhOSienU9Kp6bR0ejojnZnOSmenc9K56bx0frqgfP4vzPqidHG6JF2aLkuXpyvSlemq'
    'dHW6Jl2brkvXpxvS8NSSbkw3pZvTLenWdFu6Pd2R7kx3pbvTPenedF8ake5PD6QH08j0UHo4PZIe'
    'TY+lx9MT6cn0VBqVnk7PpGfTc+n59EJ6Mb2UXk6vpFfTa+n19EZ6M41Ob6W30zvp3fReej99kD5M'
    'H6WP0yfp0/RZ+jx9kb5MX6Wv0zfp2/Rd+j79kH5MP6Wf0y/p1zQm/VZ63FNr+Rc0FakyVaXqNEma'
    'NE2WOqXJ0xSpc5oyTZWmTtOkLqlrmjZNl6ZPM6QZU7c0U5o5zZJmTbOl2dMcac7q8us/67nS3Gme'
    '1D3Nm3qknmm+NH9aIPVKvdOCaaG0cFok9UmLpsXS4mmJtGRaKi2dlkl907JpubR8WiGtmGrTSqlf'
    'WjmtklZNq6XV0xppzbRW6p/WTuukddOAtF5aPw1MG6QN00Zp47RJ2jRtljZPW6Qt01Zp67RNGpS2'
    'Tdul7VNd2iHtmHZK9WnntEsanHZNDakx7ZZ2T02pOQ1Je6Q9015p77RP2jftl/ZPB6QD09A0LB2U'
    'Dk6HpEPTYenwdEQ6Mh2Vjk7HpGPTcen4dEI6MZ2UTk6npFPTaen0dEY6M52Vzk7npHPTeen8dEH5'
    '/F+Y9UXp4nRJujRdli5PV6Qr01Xp6nRNujZdl65PN6ThqSXdmG5KN6db0q3ptnR7uiPdme5Kd6d7'
    '0r3pvjQi3Z8eSA+mkemh9HB6JD2aHkuPpyfSk+mpNCo9nZ5Jz6bn0vPphfRieim9nF5Jr6bX0uvp'
    'jfRmGp3eSm+nd9K76b30fvogfZg+Sh+nT9Kn6bP0efoifZm+Sl+nb9K36bv0ffoh/Zh+Sj+nX9Kv'
    'aUz6rfS4p9byL2crUmWqStVpkjRpmix1SpOnKVLnNGWaKk2dpkldUtc0bZouTZ9mSDOmbmmmNHOa'
    'Jc2aZkuzpznSnJOUX/9Zz5XmTvOk7mne1CP1TPOl+dMCqVfqnRZMC6WF0yKpT1o0LZYWT0ukJdNS'
    'aem0TOqblk3LpeXTCmnFVJtWSv3SymmVtGpaLa2e1khrprVS/7R2Wietmwak9dL6aWDaIG2YNkob'
    'p03SpmmztHnaIm2Ztkpbp23SoLRt2i5tn+rSDmnHtFOqTzunXdLgtGtqSI1pt7R7akrNaUjaI+2Z'
    '9kp7p33Svmm/tH86IB2YhqZh6aB0cDokHZoOS4enI9KR6ah0dDomHZuOS8enE9KJ6aR0cjolnZpO'
    'S6enM9KZ6ax0djonnZvOS+enC8rn/8KsL0oXp0vSpemydHm6Il2ZrkpXp2vStem6dH26IQ1PLenG'
    'dFO6Od2Sbk23pdvTHenOdFe6O92T7k33pRHp/vRAejCNTA+lh9Mj6dH0WHo8PZGeTE+lUenp9Ex6'
    'Nj2Xnk8vpBfTS+nl9Ep6Nb2WXk9vpDfT6PRWeju9k95N76X30wfpw/RR+jh9kj5Nn6XP0xfpy/RV'
    '+jp9k75N36Xv0w/px/RT+jn9kn5NY9Jvpcc9tZb/x0xFqkxVqTpNkiZNk6VOafI0ReqcpkxTpanT'
    'NKlL6pqmTdOl6dMMacbULc2UZk6zpFnTbGn2NEeac9Ly6z/rudLcaZ7UPc2beqSeab40f1og9Uq9'
    '04JpobRwWiT1SYumxdLiaYm0ZFoqLZ2WSX3Tsmm5tHxaIa2YatNKqV9aOa2SVk2rpdXTGmnNtFbq'
    'n9ZO66R104C0Xlo/DUwbpA3TRmnjtEnaNG2WNk9bpC3TVmnrtE0alLZN26XtU13aIe2Ydkr1aee0'
    'Sxqcdk0NqTHtlnZPTak5DUl7pD3TXmnvtE/aN+2X9k8HpAPT0DQsHZQOToekQ9Nh6fB0RDoyHZWO'
    'TsekY9Nx6fh0QjoxnZROTqekU9Np6fR0RjoznZXOTuekc9N56fx0Qfn8X5j1RenidEm6NF2WLk9X'
    'pCvTVenqdE26Nl2Xrk83pOGpJd2Ybko3p1vSrem2dHu6I92Z7kp3p3vSvem+NCLdnx5ID6aR6aH0'
    'cHokPZoeS4+nJ9KT6ak0Kj2dnknPpufS8+mF9GJ6Kb2cXkmvptfS6+mN9GYand5Kb6d30rvpvfR+'
    '+iB9mD5KH6dP0qfps/R5+iJ9mb5KX6dv0rfpu/R9+iH9mH5KP6df0q9pTPqt9Lin1vL/lK1Ilakq'
    'VadJ0qRpstQpTZ6mSJ3TlGmqNHWaJnVJXdO0abo0fZohzZi6pZnSzGmWNGuaLc2e5khzTlZ+/Wc9'
    'V5o7zZO6p3lTj9QzzZfmTwukXql3WjAtlBZOi6Q+adG0WFo8LZGWTEulpdMyqW9aNi2Xlk8rpBVT'
    'bVop9Usrp1XSqmm1tHpaI62Z1kr909ppnbRuGpDWS+ungWmDtGHaKG2cNkmbps3S5mmLtGXaKm2d'
    'tkmD0rZpu7R9qks7pB3TTqk+7Zx2SYPTrqkhNabd0u6pKTWnIWmPtGfaK+2d9kn7pv3S/umAdGAa'
    'moalg9LB6ZB0aDosHZ6OSEemo9LR6Zh0bDouHZ9OSCemk9LJ6ZR0ajotnZ7OSGems9LZ6Zx0bjov'
    'nZ8uKJ//C7O+KF2cLkmXpsvS5emKdGW6Kl2drknXpuvS9emGNDy1pBvTTenmdEu6Nd2Wbk93pDvT'
    'XenudE+6N92XRqT70wPpwTQyPZQeTo+kR9Nj6fH0RHoyPZVGpafTM+nZ9Fx6Pr2QXkwvpZfTK+nV'
    '9Fp6Pb2R3kyj01vp7fROeje9l95PH6QP00fp4/RJ+jR9lj5PX6Qv01fp6/RN+jZ9l75PP6Qf00/p'
    '5/RL+jWNSb+VHvfUWv5ARkWqTFWpOk2SJk2TpU5p8jRF6pymTFOlqdM0qUvqmqZN06Xp0wxpxtQt'
    'zZRmTrOkWdNsafY0R5qzU/v5Z+Jav2jMf0OKmmKVoiHrprZPDPx5MxaTVIy9r4o/eZtZS5+96NR+'
    'eeWiruifr6Cp2DWX+uXSrsVuWTZkVPp6mv/gvuYpKv42f9W/uM5Xs3702EEPv17x349+3Lbu+Hml'
    'v59/waI2sza0PSKDUmnLxm17d8lXslu2bdf2CY3fLdFh/m5/MP/Y9fjn3yPzNhbrZK49/3D20mde'
    'Kko/wrd95qrrXzD/2pllt6JPti/Y9tmaP1LT4fin+QvmH5BLdcUOqantGVA64ubxfC29ioq/fWZs'
    '6r9o/vq2Z16fP/UIzJX5q8ufb5vqL5h/gzz+TcWOuTwkl0uvzcF/dPylX+G0fbZuir9g/k2yZVDb'
    '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'
    'AAAAAAAAAAAAAAAAMPH8P0Bu75w='
)


def _embedded_template_dmt_bytes() -> bytes:
    """Decompress the blank-based shell shipped inside this module."""
    return zlib.decompress(base64.b64decode("".join(_TEMPLATE_ZLIB_B64)))


def kml_abgr_to_colorref(kml_color: Optional[str]) -> int:
    """
    KML LineStyle <color> is eight hex digits aabbggrr (alpha, blue, green, red).
    Windows COLORREF uses the same 24-bit layout: 0x00bbggrr.

    Default is **red** (not white): white centerlines are invisible on typical map backgrounds.
    """
    # Opaque red in COLORREF (0x00bbggrr): rr=0xFF
    default = 0x000000FF
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
        # Matches GPSBabel an1.cc line vertices: lat = EncodeOrd(lat), lon = EncodeOrd(-lon).
        lon_i = encode_ord_deg(-lon)
        lat_i = encode_ord_deg(lat)
        pair = struct.pack("<II", lon_i, lat_i)
        if i == 0:
            parts.append(PREFIX_FIRST + pair)
        else:
            parts.append(PREFIX_MID + pair)
    parts.append(PREFIX_TERM)
    parts.append(TAIL3)
    return b"".join(parts)


def pad_stream(data: bytes, target_len: int, pad_byte: int = 0) -> bytes:
    if len(data) > target_len:
        raise ValueError(
            f"Encoded line ({len(data)} bytes) exceeds template stream size ({target_len} bytes). "
            "Use a template .dmt whose matching line stream is larger, or simplify the line."
        )
    if len(data) == target_len:
        return data
    pb = pad_byte & 0xFF
    return data + bytes([pb]) * (target_len - len(data))


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

    **Order:** (1) ``template.dmt`` beside this file if present (local override);
    (2) otherwise the shell embedded in this module (zlib+base64), written once to a
    temp file. No separate Git/binary step is required for Streamlit if you only ship
    ``delorme_streams.py``.
    """
    global _materialized_template

    p = template_dmt_path()
    if p.is_file():
        return p

    if _materialized_template is not None and _materialized_template.is_file():
        return _materialized_template

    raw = _embedded_template_dmt_bytes()
    fd, name = tempfile.mkstemp(suffix=".dmt", prefix="kmz_cl_template_")
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    tmp_path = Path(name)
    _materialized_template = tmp_path

    def _cleanup(path: Path = tmp_path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_cleanup)
    return tmp_path


_ANNOTATE_WORKSPACE = "DeLormeComponents/DeLorme.Annotate.Workspace"
_STREAM_ANNOTATE_FILENAMES = f"{_ANNOTATE_WORKSPACE}/Annotate.Filenames"
_STREAM_ANNOTATE_ACTIVE_FILENAMES = f"{_ANNOTATE_WORKSPACE}/Annotate.ActiveFilenames"
_DEFAULT_EMBED_TXT_STREAM = f"{_ANNOTATE_WORKSPACE}/Centerline.txt"


def embed_centerline_txt_stream(
    dmt_bytes: bytes,
    centerline_txt_bytes: bytes,
    *,
    stream_path: str = _DEFAULT_EMBED_TXT_STREAM,
) -> bytes:
    """
    Add/replace a ``Centerline.txt`` payload *inside* the .dmt OLE container.

    The USER asked to store the generated TXT "where the draw layer lives"; in DeLorme
    projects that is under ``DeLormeComponents/DeLorme.Annotate.Workspace``.

    This does **not** make XMap automatically import/activate the text; it's a convenient,
    portable place to stash the exact TXT you would otherwise import via Draw → Import.
    """
    import os
    import tempfile

    import olefile

    try:
        from extract_msg import OleWriter  # type: ignore
    except Exception as e:  # pragma: no cover
        # Streamlit typically cannot `pip install` at runtime (no internet / locked env).
        # Make the fix copy-pasteable: ensure requirements.txt includes extract-msg.
        raise RuntimeError(
            "Embedding Centerline.txt into .dmt requires the Python package `extract-msg`.\n\n"
            "Fix: add this line to your repo's requirements.txt and redeploy:\n"
            "extract-msg==0.48.11\n"
        ) from e

    # OleWriter works with files; materialize to a temp path, edit, then read back.
    fd_in, tmp_in = tempfile.mkstemp(suffix=".dmt", prefix="kmz_cl_in_")
    os.close(fd_in)
    fd_out, tmp_out = tempfile.mkstemp(suffix=".dmt", prefix="kmz_cl_out_")
    os.close(fd_out)
    try:
        with open(tmp_in, "wb") as f:
            f.write(dmt_bytes)

        with olefile.OleFileIO(tmp_in) as ole:
            w = OleWriter()
            w.fromOleFile(ole)

        parts = _ole_stream_path_to_parts(stream_path)
        # Replace if present, else add.
        try:
            w.editEntry(parts, data=centerline_txt_bytes)
        except Exception:
            w.addEntry(parts, data=centerline_txt_bytes)

        w.write(tmp_out)
        with open(tmp_out, "rb") as f:
            return f.read()
    finally:
        for p in (tmp_in, tmp_out):
            try:
                os.unlink(p)
            except OSError:
                pass


def _annotate_filename_type_codes(n: int) -> List[int]:
    """
    Per-layer kind dword in ``Annotate.Filenames``. Kind ``6`` is the draw / centerline style
    used for the template’s primary layer. Using ``1`` / ``0`` for 2nd+ layers matched an old
    guess; in practice kind ``0`` leaves later centerlines **inactive** in XMap (nothing in the
    Draw list / map). Use ``6`` for **every** exported layer so all polylines show.
    """
    if n <= 0:
        return []
    return [6] * n


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
    # Do not append a trailing dword: in practice it is read as an extra (kind=1,len=0) layer
    # entry and can confuse DeLorme’s annotate list. The buffer is padded with 0x00 to
    # the fixed stream size (terminates with kind=0,len=0).
    return b"".join(parts)


def build_annotate_active_filenames(active_display_name: str) -> bytes:
    """
    Binary body for ``Annotate.ActiveFilenames``: active layer is the first centerline.

    Replaces the template default that points at ``C:\\...\\Final AGMs63.an1``, which
    breaks display when that file does not exist.
    """
    s = active_display_name.encode("ascii")
    return struct.pack("<II", 1, len(s)) + s


def _ole_stream_path_to_parts(stream_path: str) -> List[str]:
    """``DeLormeComponents/...`` -> ``['DeLormeComponents', ...]`` for OleWriter."""
    return [p for p in stream_path.replace("\\", "/").split("/") if p]


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

    display_names = [stream_path_str(sp).split("/")[-1] for sp in stream_paths]
    fn_body = build_annotate_filenames_centerlines_only(display_names)
    af_body = build_annotate_active_filenames(display_names[0])
    fn_padded = pad_stream(fn_body, annotate_filenames_size, pad_byte=0)
    af_padded = pad_stream(af_body, annotate_active_filenames_size, pad_byte=0)
    line_payloads: List[bytes] = []
    for j in range(n):
        u = perm[j]
        payload = build_annotate_line_stream(coords_list[u], colorrefs[u], headers[j])
        line_payloads.append(pad_stream(payload, sizes[j]))

    # Prefer extract-msg OleWriter (same family as the template build script): rebuilding the
    # compound document tends to open reliably in XMap. olefile's in-place write_stream can
    # leave a container some Garmin builds refuse to load.
    try:
        from extract_msg import OleWriter

        ole_read = olefile.OleFileIO(str(template_path))
        try:
            writer = OleWriter()
            writer.fromOleFile(ole_read)
        finally:
            ole_read.close()
        for j in range(n):
            writer.editEntry(_ole_stream_path_to_parts(stream_paths[j]), data=line_payloads[j])
        writer.editEntry(_ole_stream_path_to_parts(_STREAM_ANNOTATE_FILENAMES), data=fn_padded)
        writer.editEntry(
            _ole_stream_path_to_parts(_STREAM_ANNOTATE_ACTIVE_FILENAMES),
            data=af_padded,
        )
        fd, tmp_path = tempfile.mkstemp(suffix=".dmt")
        os.close(fd)
        try:
            writer.write(tmp_path)
            with open(tmp_path, "rb") as f:
                return f.read(), note
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except ImportError:
        pass
    except Exception:
        if not note:
            note = "OleWriter failed; used olefile fallback. Install extract-msg for best results."
        else:
            note = note + " OleWriter failed; used olefile fallback."

    _fd, tmp = tempfile.mkstemp(suffix=".dmt")
    os.close(_fd)
    try:
        shutil.copyfile(str(template_path), tmp)
        with olefile.OleFileIO(tmp, write_mode=True) as ole_w:
            for j in range(n):
                ole_w.write_stream(stream_paths[j], line_payloads[j])
            ole_w.write_stream(_STREAM_ANNOTATE_FILENAMES, fn_padded)
            ole_w.write_stream(_STREAM_ANNOTATE_ACTIVE_FILENAMES, af_padded)
        with open(tmp, "rb") as f:
            return f.read(), note
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
