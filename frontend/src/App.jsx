import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { drawImageToCanvas } from "./camera3d";
import { useMultiplayerSpace } from "./hooks/useMultiplayerSpace";
import TopNav from "./components/TopNav";
import SideDrawer from "./components/SideDrawer";
import StudioDrawer from "./components/StudioDrawer";
import NoteComposerModal from "./components/NoteComposerModal";
import ClickActionPopover from "./components/ClickActionPopover";
import { CloseIcon, SparkIcon } from "./components/Icons";

const API_BASE = "";
const WS_PROTO = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = `${WS_PROTO}//${window.location.host}/ws/stream`;
const IS_PHONE_SCANNER = new URLSearchParams(window.location.search).get("mode") === "scan";

let cachedSplatTexture = null;
function getGaussianSplatTexture() {
  if (cachedSplatTexture) return cachedSplatTexture;
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const half = size / 2;
  const grad = ctx.createRadialGradient(half, half, 0, half, half, half);
  grad.addColorStop(0.0, "rgba(255,255,255,1.0)");
  grad.addColorStop(0.55, "rgba(255,255,255,0.92)");
  grad.addColorStop(0.85, "rgba(255,255,255,0.35)");
  grad.addColorStop(1.0, "rgba(255,255,255,0.0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  cachedSplatTexture = new THREE.CanvasTexture(canvas);
  return cachedSplatTexture;
}

function createLabelSprite(title, subtitle = "", accent = "#22c55e") {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "rgba(8, 15, 30, 0.90)";
  ctx.strokeStyle = accent;
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.roundRect(8, 8, 496, 112, 18);
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = accent;
  ctx.font = "bold 30px system-ui, sans-serif";
  ctx.fillText(title, 28, 52);
  if (subtitle) {
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "22px system-ui, sans-serif";
    ctx.fillText(subtitle.slice(0, 42), 28, 92);
  }
  const tex = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(1.15, 0.29, 1);
  return sprite;
}

export default function App() {
  const mountRef = useRef(null);
  const threeRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const scanBusyRef = useRef(false);
  const lastPointCountRef = useRef(-1);
  const baseAlphaRef = useRef(null);
  const smoothVecRef = useRef({ sin: 0, cos: 1 });
  const cloudBuffersRef = useRef({ positions: null, colors: null, count: 0 });
  const framedWorldKeyRef = useRef("");

  const [pointCount, setPointCount] = useState(0);
  const [rawPointCount, setRawPointCount] = useState(0);
  const [cloudVersion, setCloudVersion] = useState(0);
  const [keyframes, setKeyframes] = useState([]);
  const [wallSegments, setWallSegments] = useState([]);
  const [predictedOpenings, setPredictedOpenings] = useState([]);
  const [roomEnvelope, setRoomEnvelope] = useState(null);
  const [worlds, setWorlds] = useState([]);
  const [activeWorldId, setActiveWorldId] = useState(null);
  const [showAiFill, setShowAiFill] = useState(true);
  const [showPredictedWalls, setShowPredictedWalls] = useState(false);
  const [cameraActive, setCameraActive] = useState(false);
  const [autoScan, setAutoScan] = useState(false);
  const [scanMode, setScanMode] = useState("surround"); // "surround" | "room"
  const [bgCutoff, setBgCutoff] = useState(0.18); // 0.0 = map everything, 0.25 = foreground object only
  const [scanYaw, setScanYaw] = useState(0);
  const [scanPitch, setScanPitch] = useState(12);
  const [gyroActive, setGyroActive] = useState(IS_PHONE_SCANNER);
  const [depthPreviewB64, setDepthPreviewB64] = useState("");
  const [splatSize, setSplatSize] = useState(0.022);
  const [showPointCloud, setShowPointCloud] = useState(true);
  const [showFrustums, setShowFrustums] = useState(true);
  const [showVideoMarkers, setShowVideoMarkers] = useState(true);
  const [autoOrbit, setAutoOrbit] = useState(true);
  const [spatialObjects, setSpatialObjects] = useState([]);
  const [spatialPins, setSpatialPins] = useState([]);
  const [videoTrail, setVideoTrail] = useState([]);
  const [locatingVideo, setLocatingVideo] = useState(false);
  const [glassesLive, setGlassesLive] = useState(null);
  const [latestImuSession, setLatestImuSession] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [studioOpen, setStudioOpen] = useState(false);
  const [noteModalOpen, setNoteModalOpen] = useState(false);
  const [noteInitPos, setNoteInitPos] = useState(null);
  const [clickTarget, setClickTarget] = useState(null);

  const mp = useMultiplayerSpace("usr-conor", "world_1");
  const [statusText, setStatusText] = useState(
    "WorldGit 3D Spatial Viewer — VGGT-1B Walk-Around Point Cloud"
  );

  const scanParamsRef = useRef({ scanYaw, scanPitch, scanMode, bgCutoff, gyroActive });
  useEffect(() => {
    scanParamsRef.current = { scanYaw, scanPitch, scanMode, bgCutoff, gyroActive };
  }, [scanYaw, scanPitch, scanMode, bgCutoff, gyroActive]);

  const refreshPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/points.bin`);
      const buf = await res.arrayBuffer();
      const floats = new Float32Array(buf);
      const count = Math.floor(floats.length / 6);
      const positions = new Float32Array(count * 3);
      const colors = new Float32Array(count * 3);
      for (let i = 0; i < count; i += 1) {
        const s = i * 6;
        const d = i * 3;
        positions[d] = floats[s];
        positions[d + 1] = floats[s + 1];
        positions[d + 2] = floats[s + 2];
        colors[d] = floats[s + 3];
        colors[d + 1] = floats[s + 4];
        colors[d + 2] = floats[s + 5];
      }
      cloudBuffersRef.current = { positions, colors, count };
      setPointCount(count);
      setCloudVersion((v) => v + 1);
    } catch {
      // ignore transient error
    }
  };

  const refreshWorlds = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/worlds`);
      const data = await res.json();
      if (Array.isArray(data.worlds)) setWorlds(data.worlds);
      if (data.active_world_id) setActiveWorldId(data.active_world_id);
    } catch {
      // ignore
    }
  };

  const refreshImuLatest = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/imu-sessions/latest`);
      const data = await res.json();
      setLatestImuSession(data.session || null);
    } catch {
      // ignore
    }
  };

  const applyWorldMetadata = (data) => {
    if (!data || typeof data !== "object") return;
    if (Array.isArray(data.keyframes)) setKeyframes(data.keyframes);
    if (Array.isArray(data.wall_segments)) setWallSegments(data.wall_segments);
    if (Array.isArray(data.predicted_openings)) setPredictedOpenings(data.predicted_openings);
    if ("room_envelope" in data) setRoomEnvelope(data.room_envelope || null);
    if (typeof data.raw_point_count === "number") setRawPointCount(data.raw_point_count);
    if (Array.isArray(data.objects)) setSpatialObjects(data.objects);
    if (Array.isArray(data.pins)) setSpatialPins(data.pins);
    if (Array.isArray(data.video_trail)) setVideoTrail(data.video_trail);
    if (Array.isArray(data.wearers)) {
      const selfW = data.wearers.find((w) => w.wearer_id === "wearer-b");
      if (selfW) setGlassesLive(selfW);
    }
  };

  // Initial state + WebSocket live sync
  useEffect(() => {
    refreshPoints();
    refreshWorlds();
    refreshImuLatest();
  }, []);

  useEffect(() => {
    if (!mp.liveState) return;
    applyWorldMetadata(mp.liveState);
    if (
      typeof mp.liveState.point_count === "number" &&
      mp.liveState.point_count !== lastPointCountRef.current
    ) {
      lastPointCountRef.current = mp.liveState.point_count;
      refreshPoints();
    }
  }, [mp.liveState]);

  // 1. Initialize Persistent Three.js 3D Viewport (Laptop View)
  useEffect(() => {
    const container = mountRef.current;
    if (!container) return undefined;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x050811);

    const camera = new THREE.PerspectiveCamera(
      50,
      container.clientWidth / container.clientHeight,
      0.05,
      140
    );
    camera.position.set(1.05, 1.35, 2.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.innerHTML = "";
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.48, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 0.25;
    controls.maxDistance = 60;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.6;
    controls.update();

    const grid = new THREE.GridHelper(32, 64, 0x1e293b, 0x0f172a);
    scene.add(grid);

    // Subtle target ring for small tabletop 360° surround scans
    const ringGeom = new THREE.RingGeometry(0.38, 0.4, 48);
    ringGeom.rotateX(-Math.PI / 2);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.35,
    });
    const ring = new THREE.Mesh(ringGeom, ringMat);
    ring.position.set(0, 0.01, 0);
    scene.add(ring);

    const pointsGroup = new THREE.Group();
    const frustumsGroup = new THREE.Group();
    const wallsGroup = new THREE.Group();
    const markersGroup = new THREE.Group();
    scene.add(pointsGroup);
    scene.add(frustumsGroup);
    scene.add(wallsGroup);
    scene.add(markersGroup);

    threeRef.current = { scene, camera, renderer, controls, pointsGroup, frustumsGroup, wallsGroup, markersGroup, grid, ring };

    let animId;
    const animate = () => {
      animId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const handleResize = () => {
      if (!container) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener("resize", handleResize);

    const raycaster = new THREE.Raycaster();
    raycaster.params.Points.threshold = 0.14;
    let downPt = null;
    const onPointerDown = (ev) => {
      downPt = { x: ev.clientX, y: ev.clientY };
    };
    const onPointerUp = (ev) => {
      if (!downPt) return;
      const dist = Math.hypot(ev.clientX - downPt.x, ev.clientY - downPt.y);
      downPt = null;
      if (dist > 6) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((ev.clientX - rect.left) / rect.width) * 2 - 1,
        -((ev.clientY - rect.top) / rect.height) * 2 + 1
      );
      raycaster.setFromCamera(ndc, camera);
      const hits = raycaster.intersectObjects(pointsGroup.children, true);
      let pos = null;
      if (hits.length > 0) {
        const p = hits[0].point;
        pos = [Number(p.x.toFixed(3)), Number(p.y.toFixed(3)), Number(p.z.toFixed(3))];
      } else {
        const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -0.8);
        const hitPt = new THREE.Vector3();
        if (raycaster.ray.intersectPlane(plane, hitPt)) {
          pos = [Number(hitPt.x.toFixed(3)), Number(hitPt.y.toFixed(3)), Number(hitPt.z.toFixed(3))];
        }
      }
      if (pos) {
        setClickTarget({ screenX: ev.clientX, screenY: ev.clientY, worldPosition: pos });
      }
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointerup", onPointerUp);

    return () => {
      window.removeEventListener("resize", handleResize);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      cancelAnimationFrame(animId);
      controls.dispose();
      renderer.dispose();
      threeRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (threeRef.current?.controls) {
      threeRef.current.controls.autoRotate = autoOrbit;
      threeRef.current.controls.autoRotateSpeed = 1.6;
    }
  }, [autoOrbit]);

  // 2. Update 3D Point Cloud, Camera Frustums & Architectural Shell
  useEffect(() => {
    const ctx3d = threeRef.current;
    if (!ctx3d) return;
    const { camera, controls, pointsGroup, frustumsGroup, wallsGroup, grid, ring } = ctx3d;

    pointsGroup.clear();
    frustumsGroup.clear();
    wallsGroup.clear();

    const { positions, colors, count } = cloudBuffersRef.current;
    const activeCount =
      showAiFill || rawPointCount <= 0 || rawPointCount >= count ? count : rawPointCount;

    // Auto-frame camera & grid once per loaded world without overriding user orbit on toggles
    const poly = roomEnvelope?.room_polygon;
    const frameKey = `${activeWorldId || "scan"}:${count}`;
    if (count > 0 && positions && framedWorldKeyRef.current !== frameKey) {
      framedWorldKeyRef.current = frameKey;
      let minX = Infinity, maxX = -Infinity;
      let minY = Infinity, maxY = -Infinity;
      let minZ = Infinity, maxZ = -Infinity;
      const stride = Math.max(1, Math.floor(count / 4000));
      for (let i = 0; i < count; i += stride) {
        const px = positions[i * 3];
        const py = positions[i * 3 + 1];
        const pz = positions[i * 3 + 2];
        if (px < minX) minX = px;
        if (px > maxX) maxX = px;
        if (py < minY) minY = py;
        if (py > maxY) maxY = py;
        if (pz < minZ) minZ = pz;
        if (pz > maxZ) maxZ = pz;
      }
      if (Array.isArray(poly) && poly.length >= 3) {
        poly.forEach(([px, pz]) => {
          if (px < minX) minX = px;
          if (px > maxX) maxX = px;
          if (pz < minZ) minZ = pz;
          if (pz > maxZ) maxZ = pz;
        });
      }
      const floorY = typeof roomEnvelope?.floor_y === "number" ? roomEnvelope.floor_y : minY;
      const roofY = typeof roomEnvelope?.roof_y === "number" ? roomEnvelope.roof_y : maxY;
      const cx = (minX + maxX) * 0.5;
      const cz = (minZ + maxZ) * 0.5;
      const cy = floorY + (roofY - floorY) * 0.45;
      const dx = Math.max(1.0, maxX - minX);
      const dy = Math.max(0.8, roofY - floorY);
      const dz = Math.max(1.0, maxZ - minZ);
      const maxSpan = Math.max(dx, dz);

      if (grid) grid.position.set(cx, floorY - 0.015, cz);
      if (ring) {
        ring.visible = maxSpan < 4.5;
        ring.position.set(cx, floorY + 0.005, cz);
      }

      const dist = Math.max(2.4, maxSpan * 0.72 + dy * 0.45);
      controls.target.set(cx, cy, cz);
      camera.position.set(cx + dist * 0.42, floorY + dy * 0.55 + dist * 0.48, cz + dist * 0.78);
      controls.update();
    }

    if (showPointCloud && activeCount > 0 && positions && colors) {
      const pcGeom = new THREE.BufferGeometry();
      pcGeom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      pcGeom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      pcGeom.setDrawRange(0, activeCount);
      const pcMat = new THREE.PointsMaterial({
        size: splatSize,
        vertexColors: true,
        sizeAttenuation: true,
        map: getGaussianSplatTexture(),
        alphaTest: 0.12,
        transparent: true,
      });
      const pointsObj = new THREE.Points(pcGeom, pcMat);
      pointsObj.frustumCulled = false;
      pointsGroup.add(pointsObj);
    }

    const toThreeColor = (rgb, fallbackHex) =>
      Array.isArray(rgb) && rgb.length >= 3
        ? new THREE.Color(rgb[0], rgb[1], rgb[2])
        : new THREE.Color(fallbackHex);

    if (showPredictedWalls && wallSegments.length > 0) {
      const wallVerts = [];
      const wallVertColors = [];
      const lineVerts = [];
      const wallColorPalette = Array.isArray(roomEnvelope?.wall_colors) ? roomEnvelope.wall_colors : null;

      wallSegments.forEach(([x1, z1, x2, z2, y0 = 0.0, y1 = 1.6, isObs = 1.0], idx) => {
        const c = toThreeColor(wallColorPalette?.[idx], isObs >= 0.5 ? 0xdbe2ea : 0xcbd5e1);
        wallVerts.push(x1, y0, z1, x2, y0, z2, x2, y1, z2);
        wallVerts.push(x1, y0, z1, x2, y1, z2, x1, y1, z1);
        for (let k = 0; k < 6; k += 1) wallVertColors.push(c.r, c.g, c.b);
        lineVerts.push(x1, y0, z1, x2, y0, z2);
        lineVerts.push(x1, y1, z1, x2, y1, z2);
        lineVerts.push(x1, y0, z1, x1, y1, z1);
        lineVerts.push(x2, y0, z2, x2, y1, z2);
      });

      if (wallVerts.length > 0) {
        const wallGeom = new THREE.BufferGeometry();
        wallGeom.setAttribute("position", new THREE.Float32BufferAttribute(wallVerts, 3));
        wallGeom.setAttribute("color", new THREE.Float32BufferAttribute(wallVertColors, 3));
        wallsGroup.add(
          new THREE.Mesh(
            wallGeom,
            new THREE.MeshBasicMaterial({
              vertexColors: true,
              transparent: true,
              opacity: 0.16,
              side: THREE.DoubleSide,
              depthWrite: false,
            })
          )
        );
      }

      const edgeGeom = new THREE.BufferGeometry();
      edgeGeom.setAttribute("position", new THREE.Float32BufferAttribute(lineVerts, 3));
      wallsGroup.add(
        new THREE.LineSegments(
          edgeGeom,
          new THREE.LineBasicMaterial({
            color: 0x94a3b8,
            transparent: true,
            opacity: 0.35,
          })
        )
      );
    }

    if (showPredictedWalls && Array.isArray(poly) && poly.length >= 3) {
      const floorY = typeof roomEnvelope.floor_y === "number" ? roomEnvelope.floor_y : 0.0;
      const roofY = typeof roomEnvelope.roof_y === "number" ? roomEnvelope.roof_y : 1.0;
      const shape = new THREE.Shape();
      poly.forEach(([px, pz], idx) => {
        if (idx === 0) shape.moveTo(px, pz);
        else shape.lineTo(px, pz);
      });
      shape.closePath();

      const slabGeom = new THREE.ShapeGeometry(shape);
      slabGeom.rotateX(Math.PI / 2);

      const floorMesh = new THREE.Mesh(
        slabGeom,
        new THREE.MeshBasicMaterial({
          color: toThreeColor(roomEnvelope.floor_color, 0x94a3b8),
          transparent: true,
          opacity: 0.18,
          side: THREE.DoubleSide,
          depthWrite: false,
        })
      );
      floorMesh.position.y = floorY;
      wallsGroup.add(floorMesh);

      const roofMesh = new THREE.Mesh(
        slabGeom.clone(),
        new THREE.MeshBasicMaterial({
          color: toThreeColor(roomEnvelope.roof_color, 0xe2e8f0),
          transparent: true,
          opacity: 0.07,
          side: THREE.DoubleSide,
          depthWrite: false,
        })
      );
      roofMesh.position.y = roofY;
      wallsGroup.add(roofMesh);

      const roofRimVerts = poly.map(([px, pz]) => new THREE.Vector3(px, roofY, pz));
      const roofRimGeom = new THREE.BufferGeometry().setFromPoints(roofRimVerts);
      wallsGroup.add(
        new THREE.LineLoop(
          roofRimGeom,
          new THREE.LineBasicMaterial({
            color: 0x94a3b8,
            transparent: true,
            opacity: 0.32,
          })
        )
      );
    }

    if (showPredictedWalls && predictedOpenings.length > 0) {
      const openVerts = [];
      const openEdges = [];
      predictedOpenings.forEach(([x1, z1, x2, z2, y0 = 0.0, y1 = 1.2]) => {
        openVerts.push(x1, y0, z1, x2, y0, z2, x2, y1, z2);
        openVerts.push(x1, y0, z1, x2, y1, z2, x1, y1, z1);
        openEdges.push(x1, y0, z1, x2, y0, z2);
        openEdges.push(x1, y1, z1, x2, y1, z2);
        openEdges.push(x1, y0, z1, x1, y1, z1);
        openEdges.push(x2, y0, z2, x2, y1, z2);
      });
      const oGeom = new THREE.BufferGeometry();
      oGeom.setAttribute("position", new THREE.Float32BufferAttribute(openVerts, 3));
      wallsGroup.add(
        new THREE.Mesh(
          oGeom,
          new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            transparent: true,
            opacity: 0.08,
            side: THREE.DoubleSide,
            depthWrite: false,
          })
        )
      );
      const oeGeom = new THREE.BufferGeometry();
      oeGeom.setAttribute("position", new THREE.Float32BufferAttribute(openEdges, 3));
      wallsGroup.add(
        new THREE.LineSegments(
          oeGeom,
          new THREE.LineBasicMaterial({
            color: 0x38bdf8,
            transparent: true,
            opacity: 0.45,
          })
        )
      );
    }

    if (showFrustums && keyframes.length > 0) {
      keyframes.forEach((kf, i) => {
        const isLatest = i === keyframes.length - 1;
        const cone = new THREE.Mesh(
          new THREE.ConeGeometry(isLatest ? 0.11 : 0.07, isLatest ? 0.24 : 0.16, 4),
          new THREE.MeshBasicMaterial({
            color: isLatest ? 0x22c55e : 0x38bdf8,
            wireframe: true,
          })
        );
        const [px, py, pz] = kf.pose || [0, 0.8, 1.2];
        cone.position.set(px, py, pz);
        const prevPose = i > 0 ? keyframes[i - 1]?.pose : null;
        const nextPose = i < keyframes.length - 1 ? keyframes[i + 1]?.pose : null;
        if (nextPose && Math.hypot(nextPose[0] - px, nextPose[1] - py, nextPose[2] - pz) > 1e-3) {
          cone.lookAt(nextPose[0], nextPose[1], nextPose[2]);
        } else if (prevPose && Math.hypot(px - prevPose[0], py - prevPose[1], pz - prevPose[2]) > 1e-3) {
          cone.lookAt(2 * px - prevPose[0], 2 * py - prevPose[1], 2 * pz - prevPose[2]);
        } else {
          cone.lookAt(controls.target.x, py, controls.target.z);
        }
        cone.rotateX(Math.PI / 2);
        frustumsGroup.add(cone);
      });
    }
  }, [
    cloudVersion,
    keyframes,
    wallSegments,
    predictedOpenings,
    roomEnvelope,
    splatSize,
    showFrustums,
    showPointCloud,
    showAiFill,
    showPredictedWalls,
    rawPointCount,
    activeWorldId,
  ]);

  // 2b. Render Localized 3D Objects, Pins & Video Walking Path
  useEffect(() => {
    const ctx3d = threeRef.current;
    if (!ctx3d || !ctx3d.markersGroup) return;
    const { markersGroup } = ctx3d;
    markersGroup.clear();
    if (!showVideoMarkers) return;

    // 1) Walking trail from relocalized video frames
    if (Array.isArray(videoTrail) && videoTrail.length > 0) {
      const pts = videoTrail
        .filter((wp) => Array.isArray(wp.pose) && wp.pose.length >= 3)
        .map((wp) => new THREE.Vector3(wp.pose[0], wp.pose[1], wp.pose[2]));

      if (pts.length >= 2) {
        const lineGeom = new THREE.BufferGeometry().setFromPoints(pts);
        markersGroup.add(
          new THREE.Line(
            lineGeom,
            new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9 })
          )
        );
      }

      videoTrail.forEach((wp) => {
        if (!Array.isArray(wp.pose) || wp.pose.length < 3) return;
        const isTarget = Boolean(wp.is_target_view);
        const node = new THREE.Mesh(
          new THREE.SphereGeometry(isTarget ? 0.065 : 0.038, 16, 16),
          new THREE.MeshBasicMaterial({ color: isTarget ? 0xf59e0b : 0x38bdf8 })
        );
        node.position.set(wp.pose[0], wp.pose[1], wp.pose[2]);
        markersGroup.add(node);
        if (isTarget) {
          const camLbl = createLabelSprite(
            `Video Sighting (t=${wp.t}s)`,
            `KF #${(wp.kf_index ?? 0) + 1} • ${wp.inliers ?? 0} inliers`,
            "#f59e0b"
          );
          camLbl.position.set(wp.pose[0] + 0.22, wp.pose[1] + 0.55, wp.pose[2] - 0.15);
          markersGroup.add(camLbl);
        }
      });
    }

    // 2) Render localized SpatialObjects & bottle/user pins
    const bottlePins = (spatialPins || []).filter(
      (p) => p.pin_type === "bottle" || String(p.id || "").includes("water-bottle")
    );
    const itemsToShow = spatialObjects.length > 0 ? spatialObjects : bottlePins;

    const targetView = (videoTrail || []).find((wp) => wp.is_target_view);

    itemsToShow.forEach((item) => {
      if (!Array.isArray(item.position) || item.position.length < 3) return;
      const [ox, oy, oz] = item.position;
      const accentHex = 0x22c55e;

      // Glowing sphere on exact 3D bottle location
      const beacon = new THREE.Mesh(
        new THREE.SphereGeometry(0.075, 24, 24),
        new THREE.MeshBasicMaterial({ color: accentHex })
      );
      beacon.position.set(ox, oy + 0.08, oz);
      markersGroup.add(beacon);

      // Ground target ring
      const ringGeo = new THREE.RingGeometry(0.11, 0.16, 32);
      ringGeo.rotateX(-Math.PI / 2);
      const ringMesh = new THREE.Mesh(
        ringGeo,
        new THREE.MeshBasicMaterial({
          color: accentHex,
          side: THREE.DoubleSide,
          transparent: true,
          opacity: 0.9,
        })
      );
      ringMesh.position.set(ox, oy + 0.015, oz);
      markersGroup.add(ringMesh);

      // Vertical locator beam
      const beamGeom = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(ox, oy + 0.02, oz),
        new THREE.Vector3(ox, oy + 0.58, oz),
      ]);
      markersGroup.add(
        new THREE.Line(
          beamGeom,
          new THREE.LineBasicMaterial({ color: 0x4ade80, transparent: true, opacity: 0.9 })
        )
      );

      // Line from camera sighting pose directly to the bottle
      if (targetView && Array.isArray(targetView.pose)) {
        const [cx, cy, cz] = targetView.pose;
        const rayGeom = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(cx, cy, cz),
          new THREE.Vector3(ox, oy + 0.08, oz),
        ]);
        markersGroup.add(
          new THREE.Line(
            rayGeom,
            new THREE.LineBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0.85 })
          )
        );
      }

      const labelTitle = item.label || item.landmark || "Detected Object";
      const zoneLabel = item.zone_name || "Room";
      const coordText = `${zoneLabel} • ${item.last_seen_by_name || item.owner || "Wearer"}`;
      const sprite = createLabelSprite(labelTitle, coordText, "#4ade80");
      sprite.position.set(ox, oy + 0.72, oz);
      markersGroup.add(sprite);
    });

    // 3) Render audience-scoped spatial pins & place reviews
    const AUDIENCE_HEX = {
      public: 0x34d399,
      friends: 0x2997ff,
      group: 0xa78bfa,
      people: 0xa78bfa,
      private: 0x94a3b8,
    };
    (spatialPins || []).forEach((pin) => {
      if (!Array.isArray(pin.position) || pin.position.length < 3) return;
      if (pin.pin_type === "bottle") return;
      const [px, py, pz] = pin.position;
      const hex = AUDIENCE_HEX[pin.audience_type] || 0x2997ff;
      const hexStr = `#${hex.toString(16).padStart(6, "0")}`;

      const orb = new THREE.Mesh(
        new THREE.OctahedronGeometry(0.065, 0),
        new THREE.MeshBasicMaterial({ color: hex })
      );
      orb.position.set(px, py + 0.14, pz);
      markersGroup.add(orb);

      const title = pin.place_name
        ? `${pin.place_name}${pin.rating ? ` (${pin.rating}/5)` : ""}`
        : pin.landmark || "Spatial Note";
      const sprite = createLabelSprite(title, `${pin.author}: ${pin.text}`, hexStr);
      sprite.position.set(px, py + 0.45, pz);
      markersGroup.add(sprite);
    });

    // 4) Render Named Zones
    (mp.zones || []).forEach((zone) => {
      const pos = Array.isArray(zone.position)
        ? zone.position
        : typeof zone.x === "number"
        ? [zone.x, zone.y ?? 0.6, zone.z ?? 0]
        : null;
      if (!pos || pos.length < 3) return;
      const [zx, zy, zz] = pos;
      const zRingGeo = new THREE.RingGeometry(0.32, 0.36, 36);
      zRingGeo.rotateX(-Math.PI / 2);
      const zRing = new THREE.Mesh(
        zRingGeo,
        new THREE.MeshBasicMaterial({
          color: 0x38bdf8,
          side: THREE.DoubleSide,
          transparent: true,
          opacity: 0.45,
        })
      );
      zRing.position.set(zx, Math.max(0.02, zy - 0.35), zz);
      markersGroup.add(zRing);

      const zSprite = createLabelSprite(zone.name, "Named Zone", "#38bdf8");
      zSprite.scale.set(0.85, 0.22, 1);
      zSprite.position.set(zx, zy + 0.28, zz);
      markersGroup.add(zSprite);
    });

    // 5) Render Live Multiplayer Wearer Avatars
    const WEARER_COLORS = ["#34d399", "#2997ff", "#f59e0b", "#c084fc"];
    (mp.liveState?.wearers || []).forEach((w, idx) => {
      if (!Array.isArray(w.position) || w.position.length < 3) return;
      const [wx, wy, wz] = w.position;
      const colStr = WEARER_COLORS[idx % WEARER_COLORS.length];
      const avatar = new THREE.Mesh(
        new THREE.SphereGeometry(0.08, 16, 16),
        new THREE.MeshBasicMaterial({ color: colStr })
      );
      avatar.position.set(wx, wy, wz);
      markersGroup.add(avatar);

      const wSprite = createLabelSprite(w.name || w.wearer_id, w.landmark || "Live", colStr);
      wSprite.scale.set(0.8, 0.2, 1);
      wSprite.position.set(wx, wy + 0.32, wz);
      markersGroup.add(wSprite);
    });

    // 6) Render From -> To Movement Arc for Ask Result
    if (mp.askResult?.path?.from_position && mp.askResult?.path?.to_position) {
      const f = mp.askResult.path.from_position;
      const t = mp.askResult.path.to_position;
      const p0 = new THREE.Vector3(f[0], f[1] + 0.12, f[2]);
      const p2 = new THREE.Vector3(t[0], t[1] + 0.12, t[2]);
      const mid = p0.clone().add(p2).multiplyScalar(0.5);
      mid.y += 0.45;
      const curve = new THREE.QuadraticBezierCurve3(p0, mid, p2);
      const arcGeom = new THREE.BufferGeometry().setFromPoints(curve.getPoints(28));
      const arcLine = new THREE.Line(
        arcGeom,
        new THREE.LineBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0.95 })
      );
      markersGroup.add(arcLine);

      const destMarker = new THREE.Mesh(
        new THREE.SphereGeometry(0.09, 20, 20),
        new THREE.MeshBasicMaterial({ color: 0xfbbf24 })
      );
      destMarker.position.copy(p2);
      markersGroup.add(destMarker);
    }
  }, [
    spatialObjects,
    spatialPins,
    videoTrail,
    showVideoMarkers,
    activeWorldId,
    mp.zones,
    mp.liveState,
    mp.askResult,
  ]);

  const startWebcam = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 640 }, height: { ideal: 480 } },
      });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }
      setCameraActive(true);
      setStatusText("Live Camera + Gyro active — walk/orbit slowly around object");
    } catch {
      setStatusText("Tap 'Start Camera' to grant permission");
    }
  };

  useEffect(() => {
    if (!IS_PHONE_SCANNER) return;
    startWebcam();
    setGyroActive(true);
    setAutoScan(true);
  }, []);

  useEffect(() => {
    if (!gyroActive) return undefined;
    const onOrient = (e) => {
      if (typeof e.alpha !== "number") return;

      // Compensate for Android Euler gimbal flip when phone is held near-vertical (beta > 90)
      let rawAlpha = e.alpha;
      if (typeof e.beta === "number" && (e.beta > 95 || e.beta < -95)) {
        rawAlpha = (rawAlpha + 180) % 360;
      }

      if (baseAlphaRef.current === null) {
        baseAlphaRef.current = rawAlpha;
      }

      // Relative angle from starting orientation in [-180, 180], smoothed on the unit circle
      const relDeg = ((rawAlpha - baseAlphaRef.current + 540) % 360) - 180;
      const rad = (relDeg * Math.PI) / 180;
      smoothVecRef.current.sin = smoothVecRef.current.sin * 0.75 + Math.sin(rad) * 0.25;
      smoothVecRef.current.cos = smoothVecRef.current.cos * 0.75 + Math.cos(rad) * 0.25;
      const smoothDeg = Math.round(
        (Math.atan2(smoothVecRef.current.sin, smoothVecRef.current.cos) * 180) / Math.PI
      );
      setScanYaw(smoothDeg);

      if (typeof e.beta === "number") {
        const clampedBeta = Math.max(20, Math.min(90, e.beta));
        const pitch = Math.max(5, Math.min(35, Math.round(80 - clampedBeta)));
        setScanPitch(pitch);
      }
    };
    // Listen ONLY to deviceorientation (attaching both deviceorientation and
    // deviceorientationabsolute causes alternating positive/negative jumps!)
    window.addEventListener("deviceorientation", onOrient);
    return () => {
      window.removeEventListener("deviceorientation", onOrient);
    };
  }, [gyroActive]);

  const captureKeyframe = async () => {
    if (scanBusyRef.current || !canvasRef.current) return;
    if (!cameraActive || !videoRef.current || videoRef.current.readyState < 2) return;

    scanBusyRef.current = true;
    const {
      scanYaw: curYaw,
      scanPitch: curPitch,
      scanMode: curMode,
      bgCutoff: curCutoff,
      gyroActive: curGyro,
    } = scanParamsRef.current;

    try {
      drawImageToCanvas(videoRef.current, canvasRef.current, 320, 240);
      const imageB64 = canvasRef.current.toDataURL("image/jpeg", 0.8);

      const res = await fetch(`${API_BASE}/api/analyze-frame`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          wearer_id: "wearer-b",
          image_b64: imageB64,
          yaw: curYaw,
          pitch: curPitch,
          scan_mode: curMode,
          bg_cutoff: curCutoff,
          skip_ai: true,
        }),
      });
      const data = await res.json();
      if (data.depth_preview_b64) {
        setDepthPreviewB64(data.depth_preview_b64);
      }
      if (Array.isArray(data.keyframes)) {
        setKeyframes(data.keyframes);
      }
      if (!curGyro && typeof data.yaw === "number") {
        setScanYaw(Math.round(data.yaw));
      }
      if (data.added_points > 0) {
        setStatusText(
          `✓ Keyframe #${data.keyframe_count} @ ${Math.round(data.yaw ?? curYaw)}° | Total: ${data.total_points.toLocaleString()} pts`
        );
        await refreshPoints();
      } else {
        setStatusText(
          `Frame #${data.keyframe_count || keyframes.length} processed — keep orbiting around the object`
        );
      }
    } catch {
      setStatusText("Frame sync busy — retrying...");
    } finally {
      scanBusyRef.current = false;
    }
  };

  useEffect(() => {
    if (!autoScan || !cameraActive) return undefined;
    captureKeyframe();
    const id = setInterval(() => {
      captureKeyframe();
    }, 900);
    return () => clearInterval(id);
  }, [autoScan, cameraActive]);

  const clearScan = async () => {
    baseAlphaRef.current = null;
    smoothVecRef.current = { sin: 0, cos: 1 };
    setScanYaw(0);
    await fetch(`${API_BASE}/api/reset-cloud`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ empty: true }),
    });
    setKeyframes([]);
    setWallSegments([]);
    setPredictedOpenings([]);
    setRoomEnvelope(null);
    setRawPointCount(0);
    framedWorldKeyRef.current = "";
    cloudBuffersRef.current = { positions: null, colors: null, count: 0 };
    setPointCount(0);
    setCloudVersion((v) => v + 1);
    setStatusText("Cleared 3D map & zeroed angle to 0° — ready for fresh 360° scan");
  };

  const loadVggtModel = async () => {
    const res = await fetch(`${API_BASE}/api/load-vggt-model`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      setStatusText(data.error || "VGGT walk-around cloud not generated yet");
      return;
    }
    framedWorldKeyRef.current = "";
    setRawPointCount(typeof data.raw_point_count === "number" ? data.raw_point_count : 0);
    applyWorldMetadata(data);
    await refreshPoints();
    setShowPointCloud(true);
    setStatusText(`Loaded VGGT-1B walk-around 3D cloud (${data.point_count?.toLocaleString()} pts, ${data.wall_segments?.length || 0} wall segs in ${data.elapsed_s}s on ${data.device})`);
  };

  const activateWorld = async (worldId) => {
    const res = await fetch(`${API_BASE}/api/worlds/${encodeURIComponent(worldId)}/activate`, {
      method: "POST",
    });
    const data = await res.json();
    if (!data.ok) {
      setStatusText(data.error || "Could not switch world");
      return;
    }
    framedWorldKeyRef.current = "";
    setActiveWorldId(worldId);
    setRawPointCount(typeof data.raw_point_count === "number" ? data.raw_point_count : 0);
    applyWorldMetadata(data);
    await refreshPoints();
    setShowPointCloud(true);
    setStatusText(
      `Loaded ${data.label} (${data.point_count?.toLocaleString()} pts, ${data.wall_segments?.length || 0} walls, ${data.predicted_openings?.length || 0} openings)`
    );
  };

  const uploadWalkthroughVideo = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setStatusText(`Building 3D clip & checking intersections for ${file.name} (RTX 5060)...`);
    const buf = await file.arrayBuffer();
    const res = await fetch(
      `${API_BASE}/api/worlds/add-video?name=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: buf,
      }
    );
    const data = await res.json();
    if (data.ok) {
      framedWorldKeyRef.current = "";
      if (Array.isArray(data.worlds)) setWorlds(data.worlds);
      if (data.active_world?.world_id) setActiveWorldId(data.active_world.world_id);
      setRawPointCount(typeof data.raw_point_count === "number" ? data.raw_point_count : 0);
      applyWorldMetadata(data);
      await refreshPoints();
      await refreshImuLatest();
      setShowPointCloud(true);
      const statusTag = data.active_world?.status === "spliced" ? "SPLICED into shared world" : "Separate world created";
      const imuTag = data.imu_matched
        ? ` • 9-axis IMU fused (${data.imu_matched.step_count} steps)`
        : "";
      setStatusText(
        `${statusTag}: ${data.active_world?.label || file.name} (${data.point_count?.toLocaleString()} pts)${imuTag}`
      );
    } else {
      setStatusText(data.error || "VGGT reconstruction failed");
    }
    e.target.value = "";
  };

  const focusCameraOnTarget = (pos) => {
    const ctx3d = threeRef.current;
    if (!ctx3d || !Array.isArray(pos) || pos.length < 3) return;
    setAutoOrbit(false);
    ctx3d.controls.target.set(pos[0], pos[1] + 0.2, pos[2]);
    ctx3d.camera.position.set(pos[0] + 1.35, pos[1] + 1.25, pos[2] + 1.15);
    ctx3d.controls.update();
  };

  const locateCapturedVideo = async () => {
    setLocatingVideo(true);
    setStatusText("Matching video frames to 3D room map & raycasting bottle...");
    try {
      const res = await fetch(`${API_BASE}/api/localize-video`, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        if (data.object) setSpatialObjects([data.object]);
        if (data.pin) setSpatialPins((prev) => [...prev.filter((p) => p.id !== data.pin.id), data.pin]);
        if (Array.isArray(data.video_trail)) setVideoTrail(data.video_trail);
        setShowVideoMarkers(true);
        focusCameraOnTarget(data.target_position);
        setStatusText(
          `Located ${data.target_label} at [${data.target_position.join(", ")}] (t=${data.target_t}s, KF #${data.matched_keyframe + 1}, ${data.sift_inliers} inliers)`
        );
      } else {
        setStatusText(data.error || "Could not localize video onto 3D map");
      }
    } catch {
      setStatusText("Video localization request failed");
    } finally {
      setLocatingVideo(false);
    }
  };

  // ============================================================================
  // PHONE UI (?mode=scan) — Simplified 360° Surround Scanner
  // ============================================================================
  if (IS_PHONE_SCANNER) {
    return (
      <div
        style={{
          minHeight: "100vh",
          background: "#020617",
          color: "#f8fafc",
          padding: 14,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: 17 }}>360° 3D Scanner</strong>
          <span style={{ fontSize: 13, color: "#38bdf8", fontWeight: 600 }}>
            {keyframes.length} frames • {pointCount.toLocaleString()} pts
          </span>
        </div>

        {/* Live Camera + Neural Depth-Anything-V2 PiP */}
        <div
          style={{
            position: "relative",
            borderRadius: 12,
            overflow: "hidden",
            background: "#0f172a",
            height: 320,
            border: autoScan ? "2px solid #22c55e" : "2px solid #334155",
          }}
        >
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
          />
          {/* Target guide reticle for centering the object */}
          <div
            style={{
              position: "absolute",
              inset: "18% 20%",
              border: "1px dashed rgba(56,189,248,0.65)",
              borderRadius: 16,
              pointerEvents: "none",
            }}
          />
          {depthPreviewB64 && (
            <img
              src={depthPreviewB64}
              alt="Depth Map"
              style={{
                position: "absolute",
                bottom: 10,
                right: 10,
                width: 112,
                height: 84,
                borderRadius: 8,
                border: "1px solid #38bdf8",
                objectFit: "cover",
              }}
            />
          )}
          <div
            style={{
              position: "absolute",
              top: 10,
              left: 10,
              background: "rgba(2,6,23,0.82)",
              padding: "5px 10px",
              borderRadius: 6,
              fontSize: 12,
              color: "#38bdf8",
            }}
          >
            Angle: <b>{scanYaw}°</b> | Tilt: <b>{scanPitch}°</b>
          </div>
        </div>
        <canvas ref={canvasRef} style={{ display: "none" }} />

        {/* 3 Big Primary Controls */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <button
            onClick={() => setAutoScan((v) => !v)}
            style={{
              padding: 14,
              fontSize: 15,
              fontWeight: 700,
              borderRadius: 10,
              background: autoScan ? "#16a34a" : "#1e293b",
              color: "#fff",
              border: "none",
            }}
          >
            {autoScan ? "● Auto-Scan: ON" : "Auto-Scan: PAUSED"}
          </button>
          <button
            onClick={captureKeyframe}
            style={{
              padding: 14,
              fontSize: 15,
              fontWeight: 700,
              borderRadius: 10,
              background: "#0284c7",
              color: "#fff",
              border: "none",
            }}
          >
            Snap Frame Now
          </button>
        </div>

        {/* Mode + Background Cutoff + Manual Angle Fallback */}
        <div style={{ background: "#0f172a", padding: 12, borderRadius: 10, fontSize: 13 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <button
              onClick={() => setScanMode("surround")}
              style={{
                flex: 1,
                padding: 9,
                borderRadius: 8,
                border: scanMode === "surround" ? "1px solid #38bdf8" : "1px solid #334155",
                background: scanMode === "surround" ? "#0c4a6e" : "#1e293b",
                color: "#fff",
                fontWeight: 600,
              }}
            >
              360° Object Surround
            </button>
            <button
              onClick={() => setScanMode("room")}
              style={{
                flex: 1,
                padding: 9,
                borderRadius: 8,
                border: scanMode === "room" ? "1px solid #38bdf8" : "1px solid #334155",
                background: scanMode === "room" ? "#0c4a6e" : "#1e293b",
                color: "#fff",
                fontWeight: 600,
              }}
            >
              Outward Room Scan
            </button>
          </div>

          <div style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#cbd5e1" }}>
              <span>Background Wall Cutoff (Isolate Object):</span>
              <b>{Math.round(bgCutoff * 100)}%</b>
            </div>
            <input
              type="range"
              min={0.0}
              max={0.45}
              step={0.02}
              value={bgCutoff}
              onChange={(e) => setBgCutoff(Number(e.target.value))}
              style={{ width: "100%" }}
            />
          </div>

          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#cbd5e1" }}>
              <span>Orbit Angle ({gyroActive ? "Gyro Auto" : "Manual Slider"}): {scanYaw}°</span>
              <button
                onClick={() => setGyroActive((v) => !v)}
                style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4 }}
              >
                {gyroActive ? "Use Slider" : "Use Gyro"}
              </button>
            </div>
            <input
              type="range"
              min={-180}
              max={180}
              value={scanYaw}
              onChange={(e) => {
                setGyroActive(false);
                setScanYaw(Number(e.target.value));
              }}
              style={{ width: "100%" }}
            />
          </div>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <button
            onClick={clearScan}
            style={{
              flex: 1,
              padding: 12,
              fontSize: 14,
              fontWeight: 600,
              borderRadius: 10,
              background: "#dc2626",
              color: "#fff",
              border: "none",
            }}
          >
            Clear / Reset 3D Map
          </button>
          {!cameraActive && (
            <button onClick={startWebcam} style={{ flex: 1, padding: 12, borderRadius: 10 }}>
              Start Camera
            </button>
          )}
        </div>

        <p style={{ fontSize: 12, color: "#94a3b8", margin: 0 }}>{statusText}</p>
      </div>
    );
  }

  // ============================================================================
  // CONSUMER SPATIAL GLASS UI (http://localhost:5173/)
  // ============================================================================
  const handleSelectSpace = async (newWorldId) => {
    mp.setWorldId(newWorldId);
    if (newWorldId !== activeWorldId) {
      await activateWorld(newWorldId);
    }
    mp.refreshAll(mp.viewerId, newWorldId);
  };

  const handleFocusBottle = async () => {
    const res = await fetch(`${API_BASE}/api/ctrl-f`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wearer_id: "wearer-a", query: "water bottle", world_id: mp.worldId }),
    });
    const data = await res.json();
    if (data.found && data.object?.position) {
      focusCameraOnTarget(data.object.position);
      setStatusText(`Focused ${data.object.label}: ${data.directions}`);
    } else {
      setStatusText("Water bottle not found in this space yet.");
    }
  };

  return (
    <div className="wg-root">
      {/* Full-Bleed 3D Spatial Canvas */}
      <div ref={mountRef} className="wg-canvas-host" />

      {/* Floating Top Bar */}
      <TopNav
        viewerId={mp.viewerId}
        onChangeViewer={mp.setViewerId}
        users={mp.bootstrap.users}
        worldId={mp.worldId}
        onChangeWorld={handleSelectSpace}
        spaces={mp.bootstrap.spaces}
        onAsk={async (q) => {
          const ans = await mp.askWhere(q);
          if (ans?.object?.position) {
            focusCameraOnTarget(ans.object.position);
          }
        }}
        onOpenNoteModal={() => {
          setNoteInitPos(null);
          setNoteModalOpen(true);
        }}
        drawerOpen={drawerOpen}
        onToggleDrawer={() => setDrawerOpen((v) => !v)}
        studioOpen={studioOpen}
        onToggleStudio={() => setStudioOpen((v) => !v)}
        wsConnected={mp.wsConnected}
      />

      {/* Ask / Movement Answer Banner */}
      {mp.askResult && (
        <div className="wg-answer-banner wg-glass" role="status">
          <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <SparkIcon size={18} />
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                {mp.askResult.spoken_answer}
              </div>
              {mp.askResult.directions && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 3 }}>
                  {mp.askResult.directions}
                </div>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {mp.askResult.object?.position && (
              <button
                type="button"
                className="wg-btn wg-btn-emerald"
                style={{ minHeight: 34 }}
                onClick={() => focusCameraOnTarget(mp.askResult.object.position)}
              >
                Focus
              </button>
            )}
            <button
              type="button"
              className="wg-btn wg-btn-ghost wg-btn-icon"
              style={{ minHeight: 34, width: 34, minWidth: 34 }}
              onClick={() => mp.setAskResult(null)}
              aria-label="Dismiss answer"
            >
              <CloseIcon size={15} />
            </button>
          </div>
        </div>
      )}

      {/* Left Collapsible Scan & Studio Tools Drawer */}
      <StudioDrawer
        open={studioOpen}
        onClose={() => setStudioOpen(false)}
        pointCount={pointCount}
        rawPointCount={rawPointCount}
        worlds={worlds}
        activeWorldId={activeWorldId}
        onSelectWorld={handleSelectSpace}
        showPointCloud={showPointCloud}
        setShowPointCloud={setShowPointCloud}
        showFrustums={showFrustums}
        setShowFrustums={setShowFrustums}
        autoOrbit={autoOrbit}
        setAutoOrbit={setAutoOrbit}
        splatSize={splatSize}
        setSplatSize={setSplatSize}
        onFocusBottle={handleFocusBottle}
        onLocateCapturedVideo={locateCapturedVideo}
        locatingVideo={locatingVideo}
        onUploadWalkthrough={uploadWalkthroughVideo}
        scanJobs={mp.bootstrap.scan_jobs}
        statusText={statusText}
      />

      {/* Right Consumer Space Feed Drawer */}
      <SideDrawer
        open={drawerOpen}
        objects={spatialObjects}
        zones={mp.zones}
        history={mp.history}
        pins={spatialPins}
        bootstrap={mp.bootstrap}
        worldId={mp.worldId}
        onFocusPosition={focusCameraOnTarget}
        onOpenNoteModal={() => {
          setNoteInitPos(null);
          setNoteModalOpen(true);
        }}
        onCreateZone={mp.createZone}
        onReportSighting={mp.reportSighting}
        onAddFriend={mp.addFriend}
        onCreateGroup={mp.createGroup}
        onJoinGroup={mp.joinGroup}
        onShareSpace={mp.shareSpace}
      />

      {/* Click-to-Act 3D Point Popover */}
      <ClickActionPopover
        target={clickTarget}
        onClose={() => setClickTarget(null)}
        onDropNoteHere={(pos) => {
          setNoteInitPos(pos);
          setNoteModalOpen(true);
        }}
        onNameZoneHere={async (pos) => {
          const name = window.prompt("Name this zone (e.g. Bedside Table, Hotel Desk):");
          if (name && name.trim()) {
            await mp.createZone({ name: name.trim(), position: pos });
          }
        }}
        onRecordObjectHere={async (pos) => {
          const label = window.prompt("What item is here? (e.g. Phone Charger, House Keys):");
          if (label && label.trim()) {
            await mp.reportSighting({ label: label.trim(), position: pos });
          }
        }}
      />

      {/* Note & Place Review Composer Modal */}
      <NoteComposerModal
        open={noteModalOpen}
        onClose={() => setNoteModalOpen(false)}
        initialPosition={noteInitPos}
        groups={mp.bootstrap.groups}
        friends={mp.bootstrap.friends}
        onSubmit={mp.createPin}
      />

      {/* Minimal Bottom Status Pill */}
      <div className="wg-bottom-pill wg-glass">
        <span>{pointCount.toLocaleString()} splats</span>
        <span>&bull;</span>
        <span>{spatialObjects.length} tracked items</span>
        <span>&bull;</span>
        <span>Click 3D space to pin note or zone</span>
      </div>
    </div>
  );
}
