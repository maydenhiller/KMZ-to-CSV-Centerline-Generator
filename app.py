import streamlit as st
import zipfile
import tempfile
import os
import xml.etree.ElementTree as ET
import csv

st.set_page_config(page_title="KML/KMZ to CSV Converter")

st.title("KML/KMZ to CSV Converter")
st.write("Upload a `.kml` or `.kmz` file to extract coordinates into `Centerline.csv`.")

def extract_kml_from_kmz(kmz_file, extract_dir):
    with zipfile.ZipFile(kmz_file, 'r') as z:
        for name in z.namelist():
            if name.endswith('.kml'):
                z.extract(name, extract_dir)
                return os.path.join(extract_dir, name)
    return None

def parse_coordinates(kml_path):
    tree = ET.parse(kml_path)
    root = tree.getroot()
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}

    coords = []
    for coord_elem in root.findall('.//kml:coordinates', ns):
        coord_text = coord_elem.text.strip()
        for pair in coord_text.split():
            lon, lat, *_ = pair.split(',')
            coords.append((lat, lon))
    return coords

uploaded_file = st.file_uploader("Choose a KML or KMZ file", type=["kml", "kmz"])

if uploaded_file is not None:
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, uploaded_file.name)
        with open(filepath, "wb") as f:
            f.write(uploaded_file.getbuffer())

        if filepath.lower().endswith(".kmz"):
            kml_path = extract_kml_from_kmz(filepath, tmpdir)
            if not kml_path:
                st.error("No KML found inside KMZ.")
                st.stop()
        else:
            kml_path = filepath

        coords = parse_coordinates(kml_path)

        csv_path = os.path.join(tmpdir, "Centerline.csv")
        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Begin Line"])
            writer.writerow(["Latitude", "Longitude"])
            for lat, lon in coords:
                writer.writerow([lat, lon])
            writer.writerow(["End"])

        with open(csv_path, "rb") as f:
            st.download_button(
                label="Download Centerline.csv",
                data=f,
                file_name="Centerline.csv",
                mime="text/csv"
            )
