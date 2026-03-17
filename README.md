# CAD Viewer (Flask + pythonocc-core + Three.js)

## Project Structure

```text
cad to stl converter/
├── app.py
├── requirements.txt
├── README.md
├── templates/
│   └── index.html
└── static/
    ├── css/
    │   └── style.css
    ├── js/
    │   └── viewer.js
    ├── uploads/
    └── converted/
```

## What it does

- Uploads `.step` / `.stp` / `.dwg` / `.dxf` files.
- Uses `pythonocc-core` + OpenCascade XCAF to read B-Rep geometry and PMI containers.
- Exports geometry to STL for web rendering.
- Converts `.dwg` to `.dxf` (when ODA File Converter is available) and visualizes CAD entities as line geometry.
- Returns JSON containing model URL + PMI items.
- Visualizes the model in Three.js with OrbitControls and 3D PMI labels via CSS2DRenderer.

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the Flask server:
   ```bash
   python app.py
   ```
4. Open:
   ```text
   http://127.0.0.1:5000
   ```

## API Endpoints

- `GET /` → UI page
- `POST /api/upload` → accepts multipart file upload (`file`) and returns STEP/STP output:
  ```json
  {
    "job_id": "uuid",
    "model_url": "/models/<uuid>.stl",
    "model_format": "stl",
    "pmi": [
      {
        "id": "dim-1",
        "text": "Dimension 1",
        "value": null,
        "position": [0.0, 0.0, 0.0]
      }
    ]
  }
  ```
- `POST /api/upload` → for DWG/DXF also returns:
  ```json
  {
    "job_id": "uuid",
    "model_url": null,
    "cad_url": "/cad/<uuid>.dxf",
    "model_format": "cad",
    "pmi": [],
    "cad_segments": [[[0, 0, 0], [10, 0, 0]]]
  }
  ```
- `GET /api/metadata/<job_id>` → returns the same processed metadata.
- `GET /models/<filename>` → serves converted STL files.
- `GET /cad/<filename>` → serves converted DXF files.

## Notes

- STEP PMI support depends on how PMI is authored in the source CAD and what semantic data is available through the OpenCascade bindings.
- Unsupported or absent PMI entities will result in an empty `pmi` array.
- DWG conversion requires ODA File Converter on Windows. Set `ODA_FILE_CONVERTER` to the full path of `ODAFileConverter.exe` if it is not installed in a default location.
