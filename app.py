import io
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st
from lxml import etree

from delorme_streams import (
    build_dmt_bytes,
    kml_abgr_to_colorref,
    kml_abgr_to_hex_display,
    resolve_template_dmt_path,
)

APP_TITLE = "KMZ/KML to CSV Centerline Generator"
KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
KML_URI = "http://www.opengis.net/kml/2.2"
KML = f"{{{KML_URI}}}"


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


def _linestyle_color_from_style_element(style_el: etree._Element) -> Optional[str]:
    ls = style_el.find(f"{KML}LineStyle")
    if ls is None:
        return None
    c = ls.find(f"{KML}color")
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
        for pair in el.findall(f"{KML}Pair"):
            key_el = pair.find(f"{KML}key")
            if key_el is None or (key_el.text or "").strip() != "normal":
                continue
            su = pair.find(f"{KML}styleUrl")
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
    """Resolve KML LineStyle color (aabbggrr); default opaque white."""
    default = "ffffffff"
    pm = ancestor_placemark(linestring_el)
    if pm is None:
        return default

    for child in pm:
        if local_tag(child) == "Style":
            col = _linestyle_color_from_style_element(child)
            if col:
                return col

    su = pm.find(f"{KML}styleUrl")
    if su is not None and su.text:
        ref = _styleurl_to_id(su.text)
        if ref in style_colors:
            return style_colors[ref]

    e = pm.getparent()
    while e is not None:
        if local_tag(e) in ("Folder", "Document"):
            su = e.find(f"{KML}styleUrl")
            if su is not None and su.text:
                ref = _styleurl_to_id(su.text)
                if ref in style_colors:
                    return style_colors[ref]
        e = e.getparent()

    return default


def extract_linestrings_with_colors(
    root: etree._Element,
) -> Tuple[List[List[Tuple[float, float]]], List[str]]:
    style_colors = collect_style_colors(root)
    lines: List[List[Tuple[float, float]]] = []
    colors: List[str] = []
    for coord_el in root.findall(".//kml:LineString/kml:coordinates", namespaces=KML_NS):
        if not coord_el.text:
            continue
        coords = parse_coordinates_text(coord_el.text)
        if not coords:
            continue
        ls = coord_el.getparent()
        if ls is None:
            continue
        kml_abgr = effective_kml_color_for_linestring(ls, style_colors)
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

    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(kml_bytes, parser=parser)
    lines, kml_abgr_colors = extract_linestrings_with_colors(root)
    return lines, kml_abgr_colors


def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    st.title(APP_TITLE)
    st.caption(
        "Upload one or more KMZ/KML files. LineString coordinates and line colors from the file "
        "(LineStyle / styleUrl) are exported to CSV, TXT, and a combined DeLorme .dmt in the zip."
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
        dmt_bytes = build_dmt_bytes(tpl, all_lines, colorrefs)
    except FileNotFoundError as e:
        st.error(
            "The DeLorme template file is missing from the deployment. "
            "Commit `template.dmt.zlib` (or `template.dmt`) in the repo next to `delorme_streams.py`."
        )
        st.caption(str(e))
    except Exception as e:
        st.warning(f"Could not build .dmt: {e}")

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
                st.dataframe(df, use_container_width=True)

            zf.writestr(csv_name, dataframe_to_csv_bytes(df))
            zf.writestr(txt_name, lines_to_txt_bytes(lines))

        if dmt_bytes:
            zf.writestr(dmt_filename, dmt_bytes)

    if not processed_any:
        st.info("No valid LineString data found to export.")
        return

    zip_buffer.seek(0)
    st.download_button(
        label="Download CSV + TXT" + (" + DMT" if dmt_bytes else "") + " (zipped)",
        data=zip_buffer,
        file_name="Centerline_Files.zip",
        mime="application/zip",
    )


if __name__ == "__main__":
    main()
