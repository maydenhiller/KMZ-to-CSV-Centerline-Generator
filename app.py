import io
import zipfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st
from lxml import etree

from delorme_streams import (
    build_dmt_bytes,
    color_name_for_index,
    colorref_for_line_index,
    template_dmt_path,
)

APP_TITLE = "KMZ/KML to CSV Centerline Generator"
KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


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


def extract_linestrings(root: etree._Element) -> List[List[Tuple[float, float]]]:
    lines: List[List[Tuple[float, float]]] = []
    for el in root.findall(".//kml:LineString/kml:coordinates", namespaces=KML_NS):
        if el.text:
            coords = parse_coordinates_text(el.text)
            if coords:
                lines.append(coords)
    return lines


def lines_to_dataframe(
    lines: List[List[Tuple[float, float]]],
    line_colors: Sequence[str],
) -> pd.DataFrame:
    rows = []
    for idx, coords in enumerate(lines):
        color = line_colors[idx] if idx < len(line_colors) else "Red"
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


def process_upload(uploaded) -> List[List[Tuple[float, float]]]:
    raw = uploaded.read()
    if uploaded.name.lower().endswith(".kmz"):
        kml_bytes = read_kml_from_kmz(raw)
        if kml_bytes is None:
            raise ValueError("No KML file found inside the KMZ.")
    else:
        kml_bytes = raw

    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(kml_bytes, parser=parser)
    lines = extract_linestrings(root)
    return lines


def palette_index_for_line(n: int, center: int, line_index: int) -> int:
    order = [center] + [i for i in range(n) if i != center]
    return order.index(line_index)


def ordered_lines_for_dmt(
    lines: List[List[Tuple[float, float]]],
    centerline_index: int,
) -> Tuple[List[List[Tuple[float, float]]], List[int]]:
    """DeLorme streams are filled with the primary centerline first (red), then the rest."""
    n = len(lines)
    order = [centerline_index] + [i for i in range(n) if i != centerline_index]
    ordered = [lines[i] for i in order]
    colorrefs = [colorref_for_line_index(j) for j in range(n)]
    return ordered, colorrefs


def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    st.title(APP_TITLE)
    st.caption(
        "Upload one or more KMZ/KML files. The app extracts LineString coordinates, "
        "exports CSV and TXT, and can build a combined DeLorme transfer (.dmt) when "
        f"`template.dmt` is present next to the app (see project folder)."
    )

    uploads = st.file_uploader(
        "Upload KMZ or KML",
        type=["kmz", "kml"],
        accept_multiple_files=True,
    )
    if not uploads:
        st.info("Awaiting file upload.")
        return

    per_file: List[Tuple[str, str, int, int, List[List[Tuple[float, float]]]]] = []
    all_lines: List[List[Tuple[float, float]]] = []
    file_ranges: List[Tuple[str, int, int]] = []

    for uploaded in uploads:
        base_name = Path(uploaded.name).stem
        try:
            lines = process_upload(uploaded)
        except Exception as e:
            st.error(f"Error processing `{uploaded.name}`: {e}")
            continue
        if not lines:
            st.warning(f"No LineString geometries found in `{uploaded.name}`.")
            continue
        start = len(all_lines)
        all_lines.extend(lines)
        end = len(all_lines)
        file_ranges.append((base_name, start, end))
        per_file.append((uploaded.name, base_name, start, end, lines))

    if not all_lines:
        st.info("No valid LineString data found.")
        return

    n_lines = len(all_lines)
    if n_lines == 1:
        center_idx = 0
    else:
        labels: List[str] = []
        for base, a, b in file_ranges:
            for i in range(a, b):
                k = i - a + 1
                labels.append(f"{i + 1}: {base} — LineString {k}")
        center_idx = st.selectbox(
            "Which LineString is your primary centerline? (It will be drawn red in exports.)",
            options=list(range(n_lines)),
            format_func=lambda j: labels[j],
            key="center_idx",
        )

    ordered, colorrefs = ordered_lines_for_dmt(all_lines, center_idx)

    tpl = template_dmt_path()
    dmt_bytes: Optional[bytes] = None
    if n_lines == 1:
        dmt_filename = f"{file_ranges[0][0]}.dmt"
    else:
        dmt_filename = "Our CL and adjacent CLs.dmt"

    if tpl.is_file():
        try:
            dmt_bytes = build_dmt_bytes(tpl, ordered, colorrefs)
        except Exception as e:
            st.warning(f"Could not build .dmt (template issue): {e}")
    else:
        st.info(
            f"Optional: add a DeLorme `template.dmt` beside the app at `{tpl}` "
            "to enable combined .dmt export. The repo includes a sample you can copy."
        )

    zip_buffer = io.BytesIO()
    processed_any = False

    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for original_name, base_name, start, end, lines in per_file:
            csv_name = f"{base_name} CL.csv"
            txt_name = f"{base_name} CL.txt"

            local_colors = [
                color_name_for_index(palette_index_for_line(n_lines, center_idx, gi))
                for gi in range(start, end)
            ]

            df = lines_to_dataframe(lines, local_colors)
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
        label="Download CSV + TXT"
        + (" + DMT" if dmt_bytes else "")
        + " (zipped)",
        data=zip_buffer,
        file_name="Centerline_Files.zip",
        mime="application/zip",
    )


if __name__ == "__main__":
    main()
