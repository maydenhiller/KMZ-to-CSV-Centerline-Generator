import io
import zipfile
from typing import List, Tuple, Optional

import streamlit as st
import pandas as pd
from lxml import etree

APP_TITLE = "KMZ/KML to CSV Centerline Generator"
CSV_FILENAME = "Centerline.csv"
TXT_FILENAME = "Centerline.txt"

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
    coords = []
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

def extract_linestring_coords(root: etree._Element) -> List[Tuple[float, float]]:
    coords = []
    for el in root.findall(".//kml:LineString/kml:coordinates", namespaces=KML_NS):
        if el.text:
            coords.extend(parse_coordinates_text(el.text))
    return coords

def parse_kml(kml_bytes: bytes) -> pd.DataFrame:
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(kml_bytes, parser=parser)
    coords = extract_linestring_coords(root)
    rows = []
    for lat, lon in coords:
        rows.append({
            "Latitude": lat,
            "Longitude": lon,
            "Icon": "none",
            "LineStringColor": "Red"
        })
    return pd.DataFrame(rows, columns=["Latitude", "Longitude", "Icon", "LineStringColor"])

def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

def dataframe_to_txt(df: pd.DataFrame) -> bytes:
    """TXT export in original format: Begin Line, header, coords, End."""
    buf = io.StringIO()
    buf.write("Begin Line\n")
    buf.write("Latitude,Longitude\n")
    for _, row in df.iterrows():
        buf.write(f'{row["Latitude"]},{row["Longitude"]}\n')
    buf.write("End\n")
    return buf.getvalue().encode("utf-8")

def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    st.title(APP_TITLE)
    st.caption("Upload a KMZ or KML file. The app will extract LineString coordinates and export them as CSV and TXT.")

    uploaded = st.file_uploader("Upload KMZ or KML", type=["kmz", "kml"])
    if uploaded is None:
        st.info("Awaiting file upload.")
        return

    try:
        if uploaded.name.lower().endswith(".kmz"):
            kml_bytes = read_kml_from_kmz(uploaded.read())
            if kml_bytes is None:
                st.error("No KML file found inside the KMZ.")
                return
        else:
            kml_bytes = uploaded.read()

        df = parse_kml(kml_bytes)
        if df.empty:
            st.warning("No LineString geometries found in this file.")
            return

        st.subheader("Extracted centerline points")
        st.dataframe(df, use_container_width=True)

        csv_bytes = dataframe_to_csv_bytes(df)
        txt_bytes = dataframe_to_txt(df)

        # Bundle both into a single ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr(CSV_FILENAME, csv_bytes)
            zf.writestr(TXT_FILENAME, txt_bytes)
        zip_buffer.seek(0)

        st.download_button(
            label="Download CSV + TXT (zipped)",
            data=zip_buffer,
            file_name="Centerline_Files.zip",
            mime="application/zip",
        )

    except Exception as e:
        st.error(f"Error processing file: {e}")

if __name__ == "__main__":
    main()
