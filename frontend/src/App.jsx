import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { drawImageToCanvas } from "./camera3d";

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

export default function App() {
  const mountRef = useRef(null);
  const threeRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const scanBusyRef = useRef(false);
  const lastPointCountRef = useRef(-1);
  const baseAlphaRef = useRef(null);
  const smoothVecRef = useRef({ sin: 0, cos: 1 });

  const [points, setPoints] = useState([]);
  const [keyframes, setKeyframes] = useState([]);
  const [cameraActive, setCameraActive] = useState(false);
  const [autoScan, setAutoScan] = useState(false);
  const [scanMode, setScanMode] = useState("surround"); // "surround" | "room"
  const [bgCutoff, setBgCutoff] = useState(0.18); // 0.0 = map everything, 0.25 = foreground object only
  const [scanYaw, setScanYaw] = useState(0);
  const [scanPitch, setScanPitch] = useState(12);
  const [gyroActive, setGyroActive] = useState(IS_PHONE_SCANNER);
  const [depthPreviewB64, setDepthPreviewB64] = useState("");
  const [splatSize, setSplatSize] = useState(0.016);
  const [showPointCloud, setShowPointCloud] = useState(true);
  const [showFrustums, setShowFrustums] = useState(true);
  const [autoOrbit, setAutoOrbit] = useState(true);
  const [statusText, setStatusText] = useState(
    "WorldGit 3D Spatial Viewer — VGGT-1B Walk-Around Point Cloud"
  );

  const scanParamsRef = useRef({ scanYaw, scanPitch, scanMode, bgCutoff, gyroActive });
  useEffect(() => {
    scanParamsRef.current = { scanYaw, scanPitch, scanMode, bgCutoff, gyroActive };
  }, [scanYaw, scanPitch, scanMode, bgCutoff, gyroActive]);

  const refreshPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/points`);
      const data = await res.json();
      setPoints(data.points || []);
    } catch {
      // ignore transient error
    }
  };

  // Initial state + WebSocket live sync
  useEffect(() => {
    fetch(`${API_BASE}/api/state`)
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data.keyframes)) setKeyframes(data.keyframes);
      })
      .catch(() => {});

    refreshPoints();

    const ws = new WebSocket(WS_URL);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "world_state") {
        if (Array.isArray(msg.keyframes)) {
          setKeyframes(msg.keyframes);
        }
        if (
          typeof msg.point_count === "number" &&
          msg.point_count !== lastPointCountRef.current
        ) {
          lastPointCountRef.current = msg.point_count;
          refreshPoints();
        }
      }
    };
    return () => ws.close();
  }, []);

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
      50
    );
    camera.position.set(0.55, 1.05, 1.38);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.innerHTML = "";
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.48, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.6;
    controls.update();

    const grid = new THREE.GridHelper(6, 24, 0x1e293b, 0x0f172a);
    scene.add(grid);

    // Subtle target ring at center where 360° surround object sits
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
    scene.add(pointsGroup);
    scene.add(frustumsGroup);

    threeRef.current = { scene, camera, renderer, controls, pointsGroup, frustumsGroup };

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

    return () => {
      window.removeEventListener("resize", handleResize);
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

  // 2. Update 3D Point Cloud & Camera Frustums
  useEffect(() => {
    const ctx3d = threeRef.current;
    if (!ctx3d) return;
    const { pointsGroup, frustumsGroup } = ctx3d;

    pointsGroup.clear();
    frustumsGroup.clear();

    if (showPointCloud && points.length > 0) {
      const positions = new Float32Array(points.length * 3);
      const colors = new Float32Array(points.length * 3);
      points.forEach((pt, idx) => {
        positions[idx * 3] = pt[0];
        positions[idx * 3 + 1] = pt[1];
        positions[idx * 3 + 2] = pt[2];
        colors[idx * 3] = pt[3] ?? 0.7;
        colors[idx * 3 + 1] = pt[4] ?? 0.8;
        colors[idx * 3 + 2] = pt[5] ?? 0.9;
      });
      const pcGeom = new THREE.BufferGeometry();
      pcGeom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      pcGeom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      const pcMat = new THREE.PointsMaterial({
        size: splatSize,
        vertexColors: true,
        sizeAttenuation: true,
        map: getGaussianSplatTexture(),
        alphaTest: 0.12,
        transparent: true,
      });
      pointsGroup.add(new THREE.Points(pcGeom, pcMat));
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
        cone.lookAt(0, 0.48, 0);
        cone.rotateX(Math.PI / 2);
        frustumsGroup.add(cone);
      });
    }
  }, [points, keyframes, splatSize, showFrustums, showPointCloud]);

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
    setPoints([]);
    setStatusText("Cleared 3D map & zeroed angle to 0° — ready for fresh 360° scan");
  };

  const loadVggtModel = async () => {
    const res = await fetch(`${API_BASE}/api/load-vggt-model`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      setStatusText(data.error || "VGGT walk-around cloud not generated yet");
      return;
    }
    if (Array.isArray(data.keyframes)) setKeyframes(data.keyframes);
    await refreshPoints();
    setShowPointCloud(true);
    setStatusText(`Loaded VGGT-1B walk-around 3D cloud (${data.point_count?.toLocaleString()} pts in ${data.elapsed_s}s on ${data.device})`);
  };

  const uploadWalkthroughVideo = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setStatusText(`Running incremental VGGT-1B on ${file.name} (RTX 5060)...`);
    const buf = await file.arrayBuffer();
    const res = await fetch(`${API_BASE}/api/reconstruct-video-bytes`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: buf,
    });
    const data = await res.json();
    if (data.ok) {
      if (Array.isArray(data.keyframes)) setKeyframes(data.keyframes);
      await refreshPoints();
      setShowPointCloud(true);
      setStatusText(`Reconstructed ${file.name} via VGGT-1B: ${data.point_count?.toLocaleString()} pts in ${data.elapsed_s}s`);
    } else {
      setStatusText(data.error || "VGGT reconstruction failed");
    }
    e.target.value = "";
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
            {keyframes.length} frames • {points.length.toLocaleString()} pts
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
  // LAPTOP UI (http://localhost:5173/) — Full-Screen 3D Reconstruction Studio
  // ============================================================================
  return (
    <div style={{ position: "relative", width: "100vw", height: "100vh", overflow: "hidden", background: "#050811", color: "#f8fafc", fontFamily: "system-ui, sans-serif" }}>
      {/* Full-Screen Three.js 3D Viewport */}
      <div ref={mountRef} style={{ width: "100%", height: "100%" }} />

      {/* Minimal Top Telemetry Bar */}
      <div
        style={{
          position: "absolute",
          top: 16,
          left: 16,
          right: 16,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "rgba(15, 23, 42, 0.85)",
          backdropFilter: "blur(8px)",
          padding: "10px 18px",
          borderRadius: 12,
          border: "1px solid #1e293b",
          pointerEvents: "auto",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <strong style={{ fontSize: 16 }}>WorldGit 3D Reconstruction Studio</strong>
          <span style={{ fontSize: 13, color: "#38bdf8", background: "#0f172a", padding: "4px 10px", borderRadius: 6 }}>
            Keyframes: <b>{keyframes.length}</b>
          </span>
          <span style={{ fontSize: 13, color: "#4ade80", background: "#0f172a", padding: "4px 10px", borderRadius: 6 }}>
            3D Points: <b>{points.length.toLocaleString()}</b>
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={loadVggtModel}
            style={{
              padding: "7px 14px",
              borderRadius: 8,
              background: "#7c3aed",
              color: "#fff",
              border: "none",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Load VGGT Walk-Around
          </button>
          <label
            style={{
              padding: "7px 14px",
              borderRadius: 8,
              background: "#4f46e5",
              color: "#fff",
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Upload .MP4 (VGGT)
            <input type="file" accept="video/*" onChange={uploadWalkthroughVideo} style={{ display: "none" }} />
          </label>
          <button
            onClick={clearScan}
            style={{
              padding: "7px 14px",
              borderRadius: 8,
              background: "#dc2626",
              color: "#fff",
              border: "none",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Clear / Reset 3D Map
          </button>
        </div>
      </div>

      {/* Minimal Bottom View Controls */}
      <div
        style={{
          position: "absolute",
          bottom: 16,
          left: 16,
          background: "rgba(15, 23, 42, 0.88)",
          backdropFilter: "blur(8px)",
          padding: "10px 16px",
          borderRadius: 12,
          border: "1px solid #1e293b",
          display: "flex",
          alignItems: "center",
          gap: 18,
          fontSize: 13,
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showPointCloud}
            onChange={(e) => setShowPointCloud(e.target.checked)}
          />
          3D Point Cloud ({points.length.toLocaleString()})
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          Point Size:
          <input
            type="range"
            min={0.008}
            max={0.08}
            step={0.002}
            value={splatSize}
            onChange={(e) => setSplatSize(Number(e.target.value))}
            style={{ width: 95 }}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showFrustums}
            onChange={(e) => setShowFrustums(e.target.checked)}
          />
          Show 360° Camera Ring
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={autoOrbit}
            onChange={(e) => setAutoOrbit(e.target.checked)}
          />
          Auto-Turntable 3D View
        </label>
        <span style={{ color: "#94a3b8", fontSize: 12 }}>
          Left-Drag: Orbit • Right-Drag: Pan • Scroll: Zoom
        </span>
      </div>
    </div>
  );
}
