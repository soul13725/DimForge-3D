import os
import uuid
import math
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename
import ezdxf

from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.StlAPI import StlAPI_Writer
from OCC.Core.TCollection import TCollection_ExtendedString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDF import TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.XCAFDoc import (
    XCAFDoc_DocumentTool_DimTolTool,
    XCAFDoc_DocumentTool_ShapeTool,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
CONVERTED_DIR = os.path.join(STATIC_DIR, "converted")

ALLOWED_EXTENSIONS = {"step", "stp", "dwg", "dxf"}
JOBS: Dict[str, Dict[str, Any]] = {}

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CONVERTED_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _find_oda_converter() -> Optional[str]:
    env_path = os.environ.get("ODA_FILE_CONVERTER")
    if env_path and os.path.isfile(env_path):
        return env_path

    common_paths = [
        r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files\Open Design Alliance\ODAFileConverter\ODAFileConverter.exe",
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path
    return None


def _convert_dwg_to_dxf(dwg_path: str, output_dxf_path: str) -> None:
    converter = _find_oda_converter()
    if not converter:
        raise ValueError(
            "DWG conversion requires ODA File Converter. Install it and set ODA_FILE_CONVERTER to ODAFileConverter.exe."
        )

    input_dir = os.path.dirname(dwg_path)
    output_dir = os.path.dirname(output_dxf_path)
    input_name = os.path.basename(dwg_path)
    os.makedirs(output_dir, exist_ok=True)

    # ODA converter output version and format are set to produce ASCII DXF.
    cmd = [converter, input_dir, output_dir, "ACAD2018", "DXF", "0", "1", input_name]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ValueError(f"DWG conversion failed: {stderr or 'Unknown error'}")

    produced = os.path.join(output_dir, os.path.splitext(input_name)[0] + ".dxf")
    if not os.path.exists(produced):
        raise ValueError("DWG conversion did not produce a DXF file.")
    if os.path.abspath(produced) != os.path.abspath(output_dxf_path):
        shutil.move(produced, output_dxf_path)


def _polyline_segments(points: List[List[float]], closed: bool) -> List[List[List[float]]]:
    segments: List[List[List[float]]] = []
    if len(points) < 2:
        return segments
    for idx in range(len(points) - 1):
        segments.append([points[idx], points[idx + 1]])
    if closed:
        segments.append([points[-1], points[0]])
    return segments


def _extract_cad_segments_from_dxf(dxf_path: str) -> List[List[List[float]]]:
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    segments: List[List[List[float]]] = []

    for entity in msp:
        etype = entity.dxftype()

        if etype == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            segments.append([[float(start.x), float(start.y), float(start.z)], [float(end.x), float(end.y), float(end.z)]])
            continue

        if etype == "LWPOLYLINE":
            points = [[float(p[0]), float(p[1]), 0.0] for p in entity.get_points("xy")]
            segments.extend(_polyline_segments(points, bool(entity.closed)))
            continue

        if etype == "POLYLINE":
            points = []
            for v in entity.vertices:
                loc = v.dxf.location
                points.append([float(loc.x), float(loc.y), float(loc.z)])
            segments.extend(_polyline_segments(points, bool(entity.is_closed)))
            continue

        if etype == "CIRCLE":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            steps = 48
            circle_points = []
            for i in range(steps):
                ang = 2.0 * math.pi * i / steps
                circle_points.append(
                    [
                        float(center.x + radius * math.cos(ang)),
                        float(center.y + radius * math.sin(ang)),
                        float(center.z),
                    ]
                )
            segments.extend(_polyline_segments(circle_points, True))
            continue

        if etype == "ARC":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            start_angle = math.radians(float(entity.dxf.start_angle))
            end_angle = math.radians(float(entity.dxf.end_angle))
            if end_angle <= start_angle:
                end_angle += 2.0 * math.pi
            steps = max(12, int((end_angle - start_angle) / (math.pi / 24.0)))
            arc_points = []
            for i in range(steps + 1):
                t = i / steps
                ang = start_angle + (end_angle - start_angle) * t
                arc_points.append(
                    [
                        float(center.x + radius * math.cos(ang)),
                        float(center.y + radius * math.sin(ang)),
                        float(center.z),
                    ]
                )
            segments.extend(_polyline_segments(arc_points, False))

    if not segments:
        raise ValueError("No drawable entities found in DXF/DWG file.")
    return segments


def _label_name(label: Any) -> str:
    name_attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID(), name_attr):
        return name_attr.Get().ToExtString()
    return ""


def _extract_label_location_xyz(label: Any) -> Optional[List[float]]:
    # Best-effort OCAF location extraction across pythonocc versions.
    try:
        location = label.Location()
        trsf = location.Transformation()
        return [float(trsf.TranslationPart().X()), float(trsf.TranslationPart().Y()), float(trsf.TranslationPart().Z())]
    except Exception:
        return None


def _extract_pmi_dimensions(dim_tol_tool: Any) -> List[Dict[str, Any]]:
    pmi_items: List[Dict[str, Any]] = []

    # Try known semantic dimension APIs if available.
    try:
        labels = TDF_LabelSequence()
        if hasattr(dim_tol_tool, "GetDimensionLabels"):
            dim_tol_tool.GetDimensionLabels(labels)
            for idx in range(1, labels.Length() + 1):
                label = labels.Value(idx)
                item: Dict[str, Any] = {
                    "id": f"dim-{idx}",
                    "text": _label_name(label) or f"Dimension {idx}",
                    "value": None,
                    "position": _extract_label_location_xyz(label) or [0.0, 0.0, 0.0],
                }
                pmi_items.append(item)
    except Exception:
        pass

    # Fallback: include all dim/tol labels as annotation-like PMI if semantic values are unavailable.
    if not pmi_items:
        try:
            labels = TDF_LabelSequence()
            if hasattr(dim_tol_tool, "GetDimTolLabels"):
                dim_tol_tool.GetDimTolLabels(labels)
                for idx in range(1, labels.Length() + 1):
                    label = labels.Value(idx)
                    pmi_items.append(
                        {
                            "id": f"pmi-{idx}",
                            "text": _label_name(label) or f"PMI {idx}",
                            "value": None,
                            "position": _extract_label_location_xyz(label) or [0.0, 0.0, 0.0],
                        }
                    )
        except Exception:
            pass

    return pmi_items


def _read_step_with_xcaf(step_path: str) -> Tuple[Any, List[Dict[str, Any]]]:
    app_handle = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app_handle.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetPropsMode(True)
    reader.SetGDTMode(True)

    status = reader.ReadFile(step_path)
    if status != IFSelect_RetDone:
        raise ValueError("Failed to read STEP file.")

    if not reader.Transfer(doc):
        raise ValueError("Failed to transfer STEP data to XCAF document.")

    shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    if free_shapes.Length() == 0:
        raise ValueError("No free shapes found in STEP file.")

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for i in range(1, free_shapes.Length() + 1):
        lbl = free_shapes.Value(i)
        shp = shape_tool.GetShape(lbl)
        builder.Add(compound, shp)

    dim_tol_tool = XCAFDoc_DocumentTool_DimTolTool(doc.Main())
    pmi_data = _extract_pmi_dimensions(dim_tol_tool)
    return compound, pmi_data


def _export_stl(shape: Any, output_path: str) -> None:
    BRepMesh_IncrementalMesh(shape, 0.5, False, 0.5, True)
    writer = StlAPI_Writer()
    writer.SetASCIIMode(False)
    if not writer.Write(shape, output_path):
        raise ValueError("Failed to write STL output.")


@app.route("/", methods=["GET"])
def index() -> str:
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_step() -> Any:
    if "file" not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400
    if not _allowed_file(file.filename):
        return jsonify({"error": "Only .step and .stp files are supported."}), 400

    original_name = secure_filename(file.filename)
    job_id = str(uuid.uuid4())
    step_name = f"{job_id}_{original_name}"
    step_path = os.path.join(UPLOAD_DIR, step_name)
    file.save(step_path)

    ext = original_name.rsplit(".", 1)[1].lower()

    try:
        if ext in {"step", "stp"}:
            shape, pmi_data = _read_step_with_xcaf(step_path)
            stl_name = f"{job_id}.stl"
            stl_path = os.path.join(CONVERTED_DIR, stl_name)
            _export_stl(shape, stl_path)

            payload = {
                "job_id": job_id,
                "model_url": f"/models/{stl_name}",
                "model_format": "stl",
                "pmi": pmi_data,
                "cad_segments": [],
            }
        else:
            dxf_name = f"{job_id}.dxf"
            dxf_path = os.path.join(CONVERTED_DIR, dxf_name)
            if ext == "dwg":
                _convert_dwg_to_dxf(step_path, dxf_path)
            else:
                shutil.copyfile(step_path, dxf_path)

            cad_segments = _extract_cad_segments_from_dxf(dxf_path)
            payload = {
                "job_id": job_id,
                "model_url": None,
                "cad_url": f"/cad/{dxf_name}",
                "model_format": "cad",
                "pmi": [],
                "cad_segments": cad_segments,
            }
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    JOBS[job_id] = payload
    return jsonify(payload)


@app.route("/api/metadata/<job_id>", methods=["GET"])
def metadata(job_id: str) -> Any:
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@app.route("/models/<path:filename>", methods=["GET"])
def serve_model(filename: str) -> Any:
    return send_from_directory(CONVERTED_DIR, filename)


@app.route("/cad/<path:filename>", methods=["GET"])
def serve_cad(filename: str) -> Any:
    return send_from_directory(CONVERTED_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
