import streamlit as st
import zipfile
import tempfile
import os
import xml.etree.ElementTree as ET
import csv

st.set_page_config(page_title="KMZ-to-CSV-Centerline-Generator")

st.title("KMZ-to-CSV-Centerline-Generator")
st.write("Upload a `.kml` or `.kmz` file to extract coordinates into `centerline.csv` and `centerline.txt`.")

def extract_kml_from_kmz(kmz_file, extract_dir):
    with zipfile.ZipFile(kmz_file, 'r') as z:
        for name in z.namelist():
            if name.endswith('.kml'):
                z.extract(name, extract_dir)
                return os.path.join(extract_dir, name)
    return None

def coordinates_match(c1, c2, tolerance=1e-6):
    return abs(float(c1[0]) - float(c2[0])) < tolerance and abs(float(c1[1]) - float(c2[1])) < tolerance

def parse_segments(kml_path):
    tree = ET.parse(kml_path)
    root = tree.getroot()
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}

    segments = []

    def find_linestrings(element):
        for linestring in element.findall('.//kml:LineString', ns):
            coord_elem = linestring.find('.//kml:coordinates', ns)
            if coord_elem is not None:
                coord_text = coord_elem.text.strip()
                segment = []
                last_coord = None
                for pair in coord_text.split():
                    lon, lat, *_ = pair.split(',')
                    coord = (lat, lon)
                    if last_coord is None or not coordinates_match(coord, last_coord):
                        segment.append(coord)
                        last_coord = coord
                if segment:
                    segments.append(segment)

    for placemark in root.findall('.//kml:Placemark', ns):
        find_linestrings(placemark)

    return segments

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

        segments = parse_segments(kml_path)

        # Write CSV
        csv_path = os.path.join(tmpdir, "centerline.csv")
        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Latitude", "Longitude"])
            for segment in segments:
                writer.writerow(["Begin Line", ""])
                for lat, lon in segment:
                    writer.writerow([lat, lon])
                writer.writerow(["End", ""])

        # Write TXT (DeLorme-compatible)
        txt_path = os.path.join(tmpdir, "centerline.txt")
        with open(txt_path, "w", encoding="utf-8") as txtfile:
            for segment in segments:
                txtfile.write("Begin Line,\n")
                txtfile.write("latitude,longitude\n")
                for lat, lon in segment:
                    txtfile.write(f"{lat},{lon}\n")
                txtfile.write("End,\n")

        # Create ZIP
        zip_path = os.path.join(tmpdir, "centerline_bundle.zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.write(csv_path, arcname="centerline.csv")
            zipf.write(txt_path, arcname="centerline.txt")

        # Download button
        with open(zip_path, "rb") as f_zip:
            st.download_button(
                label="Download centerline_bundle.zip",
                data=f_zip.read(),
                file_name="centerline_bundle.zip",
                mime="application/zip"
            )
