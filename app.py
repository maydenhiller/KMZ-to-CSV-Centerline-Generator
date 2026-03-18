import io
import zipfile
from pathlib import Path
from typing import List, Tuple, Optional

import streamlit as st
import pandas as pd
from lxml import etree

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
    """Extract each LineString as its own list of coordinates."""
    lines: List[List[Tuple[float, float]]] = []
    for el in root.findall(".//kml:LineString/kml:coordinates", namespaces=KML_NS):
        if el.text:
            coords = parse_coordinates_text(el.text)
            if coords:
                lines.append(coords)
    return lines


def lines_to_dataframe(lines: List[List[Tuple[float, float]]]) -> pd.DataFrame:
    rows = []
    for coords in lines:
        for lat, lon in coords:
            rows.append(
                {
                    "Latitude": lat,
                    "Longitude": lon,
                    "Icon": "none",
                    "LineStringColor": "Red",
                }
            )
    return pd.DataFrame(rows, columns=["Latitude", "Longitude", "Icon", "LineStringColor"])


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def lines_to_txt_bytes(lines: List[List[Tuple[float, float]]]) -> bytes:
    """TXT export with multiple blocks: Begin Line … End Line for each LineString."""
    buf = io.StringIO()
    for line in lines:
        buf.write("Begin Line\n")
        buf.write("Latitude,Longitude\n")
        for lat, lon in line:
            buf.write(f"{lat},{lon}\n")
        buf.write("End Line\n\n")
    return buf.getvalue().encode("utf-8")


def process_upload(uploaded) -> Tuple[pd.DataFrame, List[List[Tuple[float, float]]]]:
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
    df = lines_to_dataframe(lines)
    return df, lines


def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    st.title(APP_TITLE)
    st.caption(
        "Upload one or more KMZ/KML files. The app will extract LineString coordinates and export them as CSV and TXT."
    )

    uploads = st.file_uploader(
        "Upload KMZ or KML",
        type=["kmz", "kml"],
        accept_multiple_files=True,
    )
    if not uploads:
        st.info("Awaiting file upload.")
        return

    zip_buffer = io.BytesIO()
    processed_any = False

    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for uploaded in uploads:
            base_name = Path(uploaded.name).stem
            csv_name = f"{base_name} CL.csv"
            txt_name = f"{base_name} CL.txt"

            try:
                df, lines = process_upload(uploaded)
            except Exception as e:
                st.error(f"Error processing `{uploaded.name}`: {e}")
                continue

            if df.empty:
                st.warning(f"No LineString geometries found in `{uploaded.name}`.")
                continue

            processed_any = True

            with st.expander(f"Preview: {uploaded.name}", expanded=(len(uploads) == 1)):
                st.dataframe(df, use_container_width=True)

            zf.writestr(csv_name, dataframe_to_csv_bytes(df))
            zf.writestr(txt_name, lines_to_txt_bytes(lines))

    if not processed_any:
        st.info("No valid LineString data found to export.")
        return

    zip_buffer.seek(0)
    st.download_button(
        label="Download CSV + TXT (zipped)",
        data=zip_buffer,
        file_name="Centerline_Files.zip",
        mime="application/zip",
    )


if __name__ == "__main__":
    main()

