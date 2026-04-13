import io
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st
from lxml import etree

from delorme_streams import (
    DMT_EXPORT_BUILD_ID,
    build_an1_bytes,
    build_dmt_bytes,
    kml_abgr_to_colorref,
    kml_abgr_to_hex_display,
    resolve_template_dmt_path,
)

APP_TITLE = "KMZ/KML to CSV Centerline Generator"


def local_tag(el: etree._Element) -> str:
    t = el.tag
    return t.split("}")[-1] if "}" in t else t


def read_kml_from_kmz(kmz_bytes: bytes) -> Optional[bytes]:
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        preferred = ["doc.kml", "root.kml", "index.kml"]
        names = z.namelist()
        target = next((p for p in preferred if p in names), None)
        if target is None:
            target = next((n for n in names if n.lower().endswith(".kml")), None)
        return z.read(target) if target else None


def parse_coordinates_text(coord_text: str) -> List[Tuple[float, float]]:
    coords: List[Tuple[float, float]] = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                coords.append((lat, lon))
            except ValueError:
                continue
    return coords


def _styleurl_to_id(styleurl: str) -> str:
    return styleurl.strip().split("#")[-1].split("/")[-1]


def _child_by_local_tag(parent: etree._Element, local: str) -> Optional[etree._Element]:
    """Namespace-agnostic first direct child with this local tag name."""
    for ch in parent:
        if local_tag(ch) == local:
            return ch
    return None


def _linestyle_color_from_style_element(style_el: etree._Element) -> Optional[str]:
    ls = _child_by_local_tag(style_el, "LineStyle")
    if ls is None:
        return None
    c = _child_by_local_tag(ls, "color")
    if c is not None and c.text and c.text.strip():
        return c.text.strip()
    return None


def collect_style_colors(root: etree._Element) -> Dict[str, str]:
    """Map style id -> KML aabbggrr color string (two passes so StyleMap can reference any Style)."""
    out: Dict[str, str] = {}
    for el in root.iter():
        if local_tag(el) != "Style":
            continue
        sid = el.get("id")
        if not sid:
            continue
        col = _linestyle_color_from_style_element(el)
        if col:
            out[sid] = col
    for el in root.iter():
        if local_tag(el) != "StyleMap":
            continue
        sid = el.get("id")
        if not sid:
            continue
        for pair in el:
            if local_tag(pair) != "Pair":
                continue
            key_el = _child_by_local_tag(pair, "key")
            if key_el is None or (key_el.text or "").strip() != "normal":
                continue
            su = _child_by_local_tag(pair, "styleUrl")
            if su is None or not su.text:
                continue
            ref = _styleurl_to_id(su.text)
            if ref in out:
                out[sid] = out[ref]
            break
    return out


def ancestor_placemark(el: Optional[etree._Element]) -> Optional[etree._Element]:
    e = el
    while e is not None:
        if local_tag(e) == "Placemark":
            return e
        e = e.getparent()
    return None


def effective_kml_color_for_linestring(
    linestring_el: etree._Element,
    style_colors: Dict[str, str],
) -> str:
    """Resolve KML LineStyle color (aabbggrr); default opaque red (visible on the map)."""
    default = "ff0000ff"
    pm = ancestor_placemark(linestring_el)
    if pm is None:
        return default

    for child in pm:
        if local_tag(child) == "Style":
            col = _linestyle_color_from_style_element(child)
            if col:
                return col

    su = _child_by_local_tag(pm, "styleUrl")
    if su is not None and su.text:
        ref = _styleurl_to_id(su.text)
        if ref in style_colors:
            return style_colors[ref]

    e = pm.getparent()
    while e is not None:
        if local_tag(e) in ("Folder", "Document"):
            su = _child_by_local_tag(e, "styleUrl")
            if su is not None and su.text:
                ref = _styleurl_to_id(su.text)
                if ref in style_colors:
                    return style_colors[ref]
        e = e.getparent()

    return default


def _coordinate_elements_for_polylines(root: etree._Element) -> List[etree._Element]:
    """
    Namespace-agnostic: KML 2.0/2.1/2.2 and default-prefix documents all use different xmlns URIs;
    fixed-prefix searches miss ``http://earth.google.com/kml/2.0`` and many exports.
    """
    # LineString → coordinates
    els = root.xpath(
        ".//*[local-name()='LineString']/*[local-name()='coordinates']"
        "[string-length(normalize-space(.)) > 0]"
    )
    # Polygon outer ring (common for “outline” exports)
    els.extend(
        root.xpath(
            ".//*[local-name()='Polygon']"
            "/*[local-name()='outerBoundaryIs']"
            "//*[local-name()='LinearRing']"
            "/*[local-name()='coordinates']"
            "[string-length(normalize-space(.)) > 0]"
        )
    )
    return els


def _coords_from_gx_track(track_el: etree._Element) -> List[Tuple[float, float]]:
    """Google Earth ``gx:Track``: collect ``gx:coord`` points in document order (namespace-agnostic)."""
    out: List[Tuple[float, float]] = []
    for coord_el in track_el.iter():
        if local_tag(coord_el) != "coord":
            continue
        t = (coord_el.text or "").strip().split()
        if len(t) >= 2:
            try:
                lon = float(t[0])
                lat = float(t[1])
                out.append((lat, lon))
            except ValueError:
                continue
    return out


def extract_linestrings_with_colors(
    root: etree._Element,
) -> Tuple[List[List[Tuple[float, float]]], List[str]]:
    style_colors = collect_style_colors(root)
    lines: List[List[Tuple[float, float]]] = []
    colors: List[str] = []

    for coord_el in _coordinate_elements_for_polylines(root):
        coords = parse_coordinates_text(coord_el.text or "")
        if not coords:
            continue
        geom_parent = coord_el.getparent()
        if geom_parent is None:
            continue
        kml_abgr = effective_kml_color_for_linestring(geom_parent, style_colors)
        lines.append(coords)
        colors.append(kml_abgr)

    # gx:Track (often used instead of LineString)
    for track_el in root.xpath(".//*[local-name()='Track']"):
        coords = _coords_from_gx_track(track_el)
        if len(coords) < 2:
            continue
        kml_abgr = effective_kml_color_for_linestring(track_el, style_colors)
        lines.append(coords)
        colors.append(kml_abgr)

    return lines, colors


def lines_to_dataframe(
    lines: List[List[Tuple[float, float]]],
    line_colors: Sequence[str],
) -> pd.DataFrame:
    rows = []
    for idx, coords in enumerate(lines):
        color = line_colors[idx] if idx < len(line_colors) else "#FFFFFF"
        for lat, lon in coords:
            rows.append(
                {
                    "Latitude": lat,
                    "Longitude": lon,
                    "Icon": "none",
                    "LineStringColor": color,
                }
            )
        if idx < len(lines) - 1:
            rows.append(
                {
                    "Latitude": None,
                    "Longitude": None,
                    "Icon": "",
                    "LineStringColor": "",
                }
            )
    return pd.DataFrame(rows, columns=["Latitude", "Longitude", "Icon", "LineStringColor"])


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def lines_to_txt_bytes(lines: List[List[Tuple[float, float]]]) -> bytes:
    buf = io.StringIO()
    for line in lines:
        buf.write("Begin Line\n")
        buf.write("Latitude,Longitude\n")
        for lat, lon in line:
            buf.write(f"{lat},{lon}\n")
        buf.write("End Line\n\n")
    return buf.getvalue().encode("utf-8")


def process_upload(uploaded) -> Tuple[List[List[Tuple[float, float]]], List[str]]:
    raw = uploaded.read()
    if uploaded.name.lower().endswith(".kmz"):
        kml_bytes = read_kml_from_kmz(raw)
        if kml_bytes is None:
            raise ValueError("No KML file found inside the KMZ.")
    else:
        kml_bytes = raw

    if kml_bytes.startswith(b"\xef\xbb\xbf"):
        kml_bytes = kml_bytes[3:]

    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(kml_bytes, parser=parser)
    lines, kml_abgr_colors = extract_linestrings_with_colors(root)
    return lines, kml_abgr_colors


def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    st.title(APP_TITLE)
    st.caption(
        "Exports CSV/TXT per upload plus optional combined TXT and a **best-effort** combined .dmt. "
        "**Reliable XMap path:** Draw → Import → choose the TXT file(s), Enter — repeat per line if needed, "
        "then set colors in Draw. The app cannot run XMap for you; .saf / final save steps stay in XMap."
    )
    with st.expander("Recommended XMap workflow (matches manual import)"):
        st.markdown(
            """
1. Unzip and use **`ALL_CENTERLINES_FOR_XMAP_IMPORT.txt`** (one import for every line) **or** each `* CL.txt` separately.
2. **Draw → Import** → pick the TXT → **Enter**. Repeat if you used per-file TXTs.
3. **Draw** → select a line → set **color** (KML colors are in the CSV for reference; TXT import does not carry color).
4. **Map files → Transfer → Create**, save **.saf**, then **save the .dmt**.

The generated **.dmt** encodes the same geometry/colors and also stashes a copy of the combined TXT *inside* the .dmt as `DeLormeComponents/DeLorme.Annotate.Workspace/Centerline.txt`. If it misbehaves, rely on TXT import + save in XMap (we cannot fully replicate Garmin’s internal project format).
            """
        )

    uploads = st.file_uploader(
        "Upload KMZ or KML",
        type=["kmz", "kml"],
        accept_multiple_files=True,
    )
    if not uploads:
        st.info("Awaiting file upload.")
        return

    per_file: List[Tuple[str, str, int, int, List[List[Tuple[float, float]]], List[str]]] = []
    all_lines: List[List[Tuple[float, float]]] = []
    all_kml_abgr: List[str] = []

    for uploaded in uploads:
        base_name = Path(uploaded.name).stem
        try:
            lines, kml_abgr = process_upload(uploaded)
        except Exception as e:
            st.error(f"Error processing `{uploaded.name}`: {e}")
            continue
        if not lines:
            st.warning(f"No LineString geometries found in `{uploaded.name}`.")
            continue
        start = len(all_lines)
        all_lines.extend(lines)
        all_kml_abgr.extend(kml_abgr)
        end = len(all_lines)
        per_file.append((uploaded.name, base_name, start, end, lines, kml_abgr))

    if not all_lines:
        st.info("No valid LineString data found.")
        return

    n_lines = len(all_lines)
    colorrefs = [kml_abgr_to_colorref(c) for c in all_kml_abgr]

    if n_lines == 1:
        dmt_filename = f"{per_file[0][1]}.dmt"
    else:
        dmt_filename = "Our CL and adjacent CLs.dmt"

    dmt_bytes: Optional[bytes] = None
    try:
        tpl = resolve_template_dmt_path()
        dmt_bytes, dmt_note = build_dmt_bytes(
            tpl,
            all_lines,
            colorrefs,
            centerline_txt_bytes=lines_to_txt_bytes(all_lines),
        )
        if dmt_note:
            st.info(dmt_note)
    except Exception as e:
        st.warning(f"Could not build combined .dmt (DeLorme file): {e}")

    zip_buffer = io.BytesIO()
    processed_any = False

    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for original_name, base_name, _start, _end, lines, kml_abgr in per_file:
            csv_name = f"{base_name} CL.csv"
            txt_name = f"{base_name} CL.txt"

            hex_colors = [kml_abgr_to_hex_display(c) for c in kml_abgr]
            df = lines_to_dataframe(lines, hex_colors)
            processed_any = True

            with st.expander(f"Preview: {original_name}", expanded=(len(per_file) == 1)):
                st.dataframe(df, width="stretch")

            zf.writestr(csv_name, dataframe_to_csv_bytes(df))
            zf.writestr(txt_name, lines_to_txt_bytes(lines))
            # One native .an1 per KML LineString (each file holds a single polyline).
            for li, (line_coords, abgr) in enumerate(zip(lines, kml_abgr), start=1):
                cref = kml_abgr_to_colorref(abgr)
                try:
                    an1_payload = build_an1_bytes(line_coords, cref)
                except ValueError as exc:
                    st.warning(f"`{original_name}`: skipped .an1 for line {li}: {exc}")
                    continue
                an1_name = (
                    f"{base_name}.an1"
                    if len(lines) == 1
                    else f"{base_name}_line{li}.an1"
                )
                zf.writestr(an1_name, an1_payload)

        if len(all_lines) > 0:
            zf.writestr(
                "ALL_CENTERLINES_FOR_XMAP_IMPORT.txt",
                lines_to_txt_bytes(all_lines),
            )

        if dmt_bytes:
            zf.writestr(dmt_filename, dmt_bytes)

        if processed_any:
            zf.writestr(
                "_EXPORT_BUILD_INFO.txt",
                (
                    f"dmt_export_build={DMT_EXPORT_BUILD_ID}\n"
                    "Per-file ``*.an1`` / ``*_lineN.an1`` files are native DeLorme draw layers "
                    "(one .an1 per KML LineString; copy into "
                    "C:\\DeLorme Docs\\Draw\\ or open from XMap). "
                    "Per-file and combined TXT are for Draw→Import (same format as your manual workflow). "
                    "A copy of the combined TXT is also embedded in the .dmt as "
                    "DeLormeComponents/DeLorme.Annotate.Workspace/Centerline.txt. "
                    "Combined TXT = all polylines in KMZ order.\n"
                ).encode("utf-8"),
            )

    if not processed_any:
        st.info("No valid LineString data found to export.")
        return

    zip_buffer.seek(0)
    st.download_button(
        label="Download CSV + TXT + AN1" + (" + DMT" if dmt_bytes else "") + " (zipped)",
        data=zip_buffer,
        file_name="Centerline_Files.zip",
        mime="application/zip",
    )


if __name__ == "__main__":
    main()
