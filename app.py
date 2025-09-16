from flask import Flask, request, send_file, render_template_string
import zipfile
import os
import tempfile
import xml.etree.ElementTree as ET
import csv

app = Flask(__name__)

HTML_FORM = """
<!doctype html>
<title>KML/KMZ to CSV</title>
<h1>Upload KML or KMZ</h1>
<form method=post enctype=multipart/form-data>
  <input type=file name=file>
  <input type=submit value=Upload>
</form>
"""

def extract_kml_from_kmz(kmz_path, extract_dir):
    with zipfile.ZipFile(kmz_path, 'r') as z:
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

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files['file']
        if not file:
            return "No file uploaded", 400

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, file.filename)
            file.save(filepath)

            if filepath.lower().endswith('.kmz'):
                kml_path = extract_kml_from_kmz(filepath, tmpdir)
                if not kml_path:
                    return "No KML found in KMZ", 400
            elif filepath.lower().endswith('.kml'):
                kml_path = filepath
            else:
                return "Invalid file type", 400

            coords = parse_coordinates(kml_path)

            csv_path = os.path.join(tmpdir, "Centerline.csv")
            with open(csv_path, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["Begin Line"])
                writer.writerow(["Latitude", "Longitude"])
                for lat, lon in coords:
                    writer.writerow([lat, lon])
                writer.writerow(["End"])

            return send_file(csv_path, as_attachment=True)

    return render_template_string(HTML_FORM)

if __name__ == '__main__':
    app.run(debug=True)
