import * as THREE from "https://unpkg.com/three@0.161.0/build/three.module.js";
import { OrbitControls } from "https://unpkg.com/three@0.161.0/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "https://unpkg.com/three@0.161.0/examples/jsm/loaders/STLLoader.js";
import { CSS2DRenderer, CSS2DObject } from "https://unpkg.com/three@0.161.0/examples/jsm/renderers/CSS2DRenderer.js";

const viewerEl = document.getElementById("viewer");
const formEl = document.getElementById("uploadForm");
const fileEl = document.getElementById("stepFile");
const statusEl = document.getElementById("status");

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x181818);

const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100000);
camera.position.set(250, 250, 250);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
viewerEl.appendChild(renderer.domElement);

const labelRenderer = new CSS2DRenderer();
labelRenderer.domElement.style.position = "absolute";
labelRenderer.domElement.style.top = "0";
labelRenderer.domElement.style.pointerEvents = "none";
viewerEl.appendChild(labelRenderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.AmbientLight(0xffffff, 0.5));
const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
dirLight.position.set(1, 1, 1);
scene.add(dirLight);
scene.add(new THREE.GridHelper(400, 20, 0x555555, 0x333333));

let modelMesh = null;
let cadLines = null;
const pmiLabelObjects = [];

function resize() {
  const { clientWidth, clientHeight } = viewerEl;
  camera.aspect = clientWidth / clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(clientWidth, clientHeight);
  labelRenderer.setSize(clientWidth, clientHeight);
}

function clearSceneModel() {
  if (modelMesh) {
    scene.remove(modelMesh);
    modelMesh.geometry.dispose();
    modelMesh.material.dispose();
    modelMesh = null;
  }
  if (cadLines) {
    scene.remove(cadLines);
    cadLines.geometry.dispose();
    cadLines.material.dispose();
    cadLines = null;
  }
  while (pmiLabelObjects.length > 0) {
    const obj = pmiLabelObjects.pop();
    scene.remove(obj);
  }
}

function addPmiLabels(pmiItems) {
  for (const item of pmiItems) {
    const div = document.createElement("div");
    div.className = "pmi-label";
    const text = item.value ? `${item.text}: ${item.value}` : item.text;
    div.textContent = text || "PMI";
    const label = new CSS2DObject(div);
    const [x, y, z] = Array.isArray(item.position) ? item.position : [0, 0, 0];
    label.position.set(Number(x) || 0, Number(y) || 0, Number(z) || 0);
    scene.add(label);
    pmiLabelObjects.push(label);
  }
}

function frameModel(mesh) {
  mesh.geometry.computeBoundingBox();
  const box = mesh.geometry.boundingBox;
  const center = new THREE.Vector3();
  box.getCenter(center);
  mesh.position.sub(center);

  const size = new THREE.Vector3();
  box.getSize(size);
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const dist = maxDim * 2.2;

  camera.position.set(dist, dist, dist);
  camera.lookAt(0, 0, 0);
  controls.target.set(0, 0, 0);
  controls.update();
}

function loadStl(modelUrl, pmiItems) {
  const loader = new STLLoader();
  loader.load(
    modelUrl,
    (geometry) => {
      clearSceneModel();
      const material = new THREE.MeshStandardMaterial({ color: 0xb8bcc6, metalness: 0.1, roughness: 0.6 });
      modelMesh = new THREE.Mesh(geometry, material);
      scene.add(modelMesh);
      frameModel(modelMesh);
      addPmiLabels(pmiItems || []);
      statusEl.textContent = `Loaded model with ${pmiItems?.length || 0} PMI items.`;
    },
    undefined,
    (err) => {
      statusEl.textContent = `Failed to load model: ${err?.message || err}`;
    }
  );
}

function frameObject(object3d) {
  const box = new THREE.Box3().setFromObject(object3d);
  const center = box.getCenter(new THREE.Vector3());
  object3d.position.sub(center);

  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const dist = maxDim * 2.2;

  camera.position.set(dist, dist, dist);
  camera.lookAt(0, 0, 0);
  controls.target.set(0, 0, 0);
  controls.update();
}

function loadCadSegments(segments) {
  clearSceneModel();
  const positions = [];
  for (const segment of segments || []) {
    const a = segment?.[0] || [0, 0, 0];
    const b = segment?.[1] || [0, 0, 0];
    positions.push(Number(a[0]) || 0, Number(a[1]) || 0, Number(a[2]) || 0);
    positions.push(Number(b[0]) || 0, Number(b[1]) || 0, Number(b[2]) || 0);
  }

  if (positions.length === 0) {
    statusEl.textContent = "No drawable CAD entities found.";
    return;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const material = new THREE.LineBasicMaterial({ color: 0x6fd3ff });
  cadLines = new THREE.LineSegments(geometry, material);
  scene.add(cadLines);
  frameObject(cadLines);
  statusEl.textContent = `Loaded CAD drawing with ${segments.length} segments.`;
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileEl.files?.[0];
  if (!file) {
    statusEl.textContent = "Please choose a STEP/STP/DWG/DXF file first.";
    return;
  }

  const body = new FormData();
  body.append("file", file);
  statusEl.textContent = "Uploading and processing CAD file...";

  try {
    const response = await fetch("/api/upload", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Upload failed.");
    }
    if (data.model_format === "stl") {
      loadStl(data.model_url, data.pmi);
    } else if (data.model_format === "cad") {
      loadCadSegments(data.cad_segments);
    } else {
      throw new Error("Unsupported model format returned by server.");
    }
  } catch (error) {
    statusEl.textContent = error.message || "Unexpected error.";
  }
});

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
}

window.addEventListener("resize", resize);
resize();
animate();
