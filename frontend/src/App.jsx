import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";

const API_BASE = "http://127.0.0.1:8000";
const WS_URL = "ws://127.0.0.1:8000/ws/stream";

export default function App() {
  const mountRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);

  const [world, setWorld] = useState({
    wearers: [],
    objects: [],
    pins: [],
    snapshots: [],
    diff: { added: [], removed: [], moved: [] },
    point_count: 0,
    tech_stack_status: null,
  });
  const [points, setPoints] = useState([]);
  const [query, setQuery] = useState("Red DeWalt Drill");
  const [ctrlFResult, setCtrlFResult] = useState(null);
  const [undoSteps, setUndoSteps] = useState([]);
  const [showDiffOverlay, setShowDiffOverlay] = useState(true);
  const [snapshotIdx, setSnapshotIdx] = useState(-1); // -1 = live/latest
  const [pinText, setPinText] = useState("");
  const [pinType, setPinType] = useState("warning");
  const [cameraActive, setCameraActive] = useState(false);
  const [captureStatus, setCaptureStatus] = useState("Idle (ready for webcam or synthetic POV)");
  const [ttsStatus, setTtsStatus] = useState("");

  // Initial state + WebSocket sync
  useEffect(() => {
    fetch(`${API_BASE}/api/state`)
      .then((r) => r.json())
      .then((data) => setWorld(data))
      .catch(() => {});

    fetch(`${API_BASE}/api/points`)
      .then((r) => r.json())
      .then((data) => setPoints(data.points || []))
      .catch(() => {});

    const ws = new WebSocket(WS_URL);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "world_state") {
        setWorld(msg);
      }
    };
    return () => ws.close();
  }, []);

  const refreshPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/points`);
      const data = await res.json();
      setPoints(data.points || []);
    } catch {
      // ignore transient error
    }
  };

  // Three.js 3D Spatial Viewport
  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070a12);

    const camera = new THREE.PerspectiveCamera(
      55,
      container.clientWidth / container.clientHeight,
      0.1,
      100
    );
    camera.position.set(0, 7.5, 9.5);
    camera.lookAt(0, 0.5, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.innerHTML = "";
    container.appendChild(renderer.domElement);

    const grid = new THREE.GridHelper(16, 16, 0x334155, 0x162032);
    scene.add(grid);
    scene.add(new THREE.AmbientLight(0xffffff, 0.95));

    // Render 3D Point Cloud
    if (points.length > 0) {
      const positions = new Float32Array(points.length * 3);
      const colors = new Float32Array(points.length * 3);
      points.forEach((pt, idx) => {
        positions[idx * 3] = pt[0];
        positions[idx * 3 + 1] = pt[1];
        positions[idx * 3 + 2] = pt[2];
        colors[idx * 3] = pt[3] ?? 0.5;
        colors[idx * 3 + 1] = pt[4] ?? 0.7;
        colors[idx * 3 + 2] = pt[5] ?? 0.9;
      });
      const pcGeom = new THREE.BufferGeometry();
      pcGeom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      pcGeom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      const pcMat = new THREE.PointsMaterial({ size: 0.08, vertexColors: true });
      scene.add(new THREE.Points(pcGeom, pcMat));
    }

    const dynamicGroup = new THREE.Group();
    scene.add(dynamicGroup);

    // Wearer Gaze Cones
    world.wearers.forEach((w) => {
      const cone = new THREE.Mesh(
        new THREE.ConeGeometry(0.26, 0.75, 16),
        new THREE.MeshBasicMaterial({
          color: w.wearer_id === "wearer-b" ? 0x38bdf8 : 0x818cf8,
          wireframe: true,
        })
      );
      cone.position.set(w.position[0], 1.2, w.position[2]);
      cone.rotation.x = Math.PI / 2;
      cone.rotation.z = -(w.yaw * Math.PI) / 180;
      dynamicGroup.add(cone);

      const base = new THREE.Mesh(
        new THREE.CylinderGeometry(0.16, 0.16, 0.9, 12),
        new THREE.MeshBasicMaterial({
          color: w.wearer_id === "wearer-b" ? 0x0ea5e9 : 0x6366f1,
        })
      );
      base.position.set(w.position[0], 0.45, w.position[2]);
      dynamicGroup.add(base);
    });

    // Time-travel selection: either live objects or historical snapshot
    const activeObjects =
      snapshotIdx >= 0 && world.snapshots[snapshotIdx]
        ? world.snapshots[snapshotIdx].objects
        : world.objects;

    activeObjects.forEach((o) => {
      const box = new THREE.Mesh(
        new THREE.BoxGeometry(0.32, 0.32, 0.32),
        new THREE.MeshBasicMaterial({ color: 0x22c55e })
      );
      box.position.set(o.position[0], o.position[1] || 0.3, o.position[2]);
      dynamicGroup.add(box);
    });

    // Spatial Pins & Friend's World
    world.pins.forEach((p) => {
      const color =
        p.pin_type === "warning"
          ? 0xef4444
          : p.pin_type === "friend_memory"
          ? 0xa855f7
          : 0xfacc15;
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(0.2, 16, 16),
        new THREE.MeshBasicMaterial({ color })
      );
      sphere.position.set(p.position[0], (p.position[1] || 0.8) + 0.35, p.position[2]);
      dynamicGroup.add(sphere);
    });

    // Ctrl+F Turn-by-Turn Navigation Line
    if (ctrlFResult?.found && ctrlFResult.path?.length === 2) {
      const [start, end] = ctrlFResult.path;
      const lineGeom = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(start[0], 0.35, start[2]),
        new THREE.Vector3(end[0], 0.55, end[2]),
      ]);
      const lineMat = new THREE.LineDashedMaterial({
        color: 0x4ade80,
        dashSize: 0.25,
        gapSize: 0.12,
      });
      const line = new THREE.Line(lineGeom, lineMat);
      line.computeLineDistances();
      dynamicGroup.add(line);
    }

    // git diff Displacement Vectors
    if (showDiffOverlay && world.diff) {
      (world.diff.moved || []).forEach((m) => {
        const diffGeom = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(m.from[0], m.from[1] || 0.5, m.from[2]),
          new THREE.Vector3(m.to[0], m.to[1] || 0.5, m.to[2]),
        ]);
        const diffLine = new THREE.Line(
          diffGeom,
          new THREE.LineBasicMaterial({ color: 0xfacc15 })
        );
        dynamicGroup.add(diffLine);

        const ghost = new THREE.Mesh(
          new THREE.BoxGeometry(0.3, 0.3, 0.3),
          new THREE.MeshBasicMaterial({ color: 0xfacc15, wireframe: true })
        );
        ghost.position.set(m.from[0], m.from[1] || 0.5, m.from[2]);
        dynamicGroup.add(ghost);
      });

      (world.diff.removed || []).forEach((r) => {
        const missing = new THREE.Mesh(
          new THREE.BoxGeometry(0.34, 0.34, 0.34),
          new THREE.MeshBasicMaterial({ color: 0xef4444, wireframe: true })
        );
        missing.position.set(r.position[0], r.position[1] || 0.5, r.position[2]);
        dynamicGroup.add(missing);
      });
    }

    let animId;
    let angle = 0.2;
    const animate = () => {
      animId = requestAnimationFrame(animate);
      angle += 0.0018;
      camera.position.x = Math.sin(angle) * 9.5;
      camera.position.z = Math.cos(angle) * 9.5;
      camera.lookAt(0, 0.6, 0);
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
      renderer.dispose();
    };
  }, [world, points, ctrlFResult, showDiffOverlay, snapshotIdx]);

  const speakText = async (text) => {
    try {
      const res = await fetch(`${API_BASE}/api/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      setTtsStatus(`Voice: ${data.provider}`);
      if (!data.fallback_tts && data.audio_b64) {
        const audio = new Audio(`data:${data.mime_type};base64,${data.audio_b64}`);
        audio.play().catch(() => {});
      } else if ("speechSynthesis" in window) {
        const u = new SpeechSynthesisUtterance(text);
        window.speechSynthesis.speak(u);
      }
    } catch {
      // ignore TTS error
    }
  };

  const handleCtrlF = async () => {
    if (!query.trim()) return;
    const res = await fetch(`${API_BASE}/api/ctrl-f`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wearer_id: "wearer-b", query }),
    });
    const data = await res.json();
    setCtrlFResult(data);
    if (data.directions || data.message) {
      speakText(data.directions || data.message);
    }
  };

  const handleSnapshot = async () => {
    await fetch(`${API_BASE}/api/snapshot?label=pass-${world.snapshots.length + 1}`, {
      method: "POST",
    });
    setSnapshotIdx(-1);
  };

  const handleCtrlZ = async () => {
    const res = await fetch(`${API_BASE}/api/ctrl-z`, { method: "POST" });
    const data = await res.json();
    const steps = data.undo_steps || [];
    setUndoSteps(steps);
    if (steps.length > 0) {
      speakText(steps.join(" "));
    }
  };

  const handleDropPin = async () => {
    if (!pinText.trim()) return;
    await fetch(`${API_BASE}/api/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: `pin-${Date.now()}`,
        author: pinType === "friend_memory" ? "Sam" : "Conor",
        text: pinText,
        pin_type: pinType,
        position: [Math.random() * 4 - 2, 1.0, Math.random() * 4 - 2],
        landmark: "Workbench",
        created_at: Date.now() / 1000,
        age_label: pinType === "friend_memory" ? "2 years ago" : "just now",
      }),
    });
    setPinText("");
  };

  const startWebcam = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      setCameraActive(true);
      setCaptureStatus("Webcam POV live");
    } catch {
      setCaptureStatus("Webcam unavailable — use Synthetic POV Capture below");
    }
  };

  const captureAndAnalyze = async () => {
    let imageB64 = "";
    if (cameraActive && videoRef.current && canvasRef.current) {
      const ctx = canvasRef.current.getContext("2d");
      canvasRef.current.width = 320;
      canvasRef.current.height = 240;
      ctx.drawImage(videoRef.current, 0, 0, 320, 240);
      imageB64 = canvasRef.current.toDataURL("image/jpeg", 0.7);
    }
    setCaptureStatus("Sending frame to Modal GPU + Gemini...");
    const res = await fetch(`${API_BASE}/api/analyze-frame`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wearer_id: "wearer-b", image_b64: imageB64 }),
    });
    const data = await res.json();
    setCaptureStatus(
      `Indexed ${data.detected_objects?.length || 0} objects (${data.ai_provider}) & +${data.added_points} pts (${data.modal_provider})`
    );
    await refreshPoints();
  };

  const aiven = world.tech_stack_status?.aiven;
  const ai = world.tech_stack_status?.ai;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      {/* Top Tech Challenge Status Bar */}
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 16px",
          background: "#0f172a",
          borderBottom: "1px solid #1e293b",
          fontSize: 12,
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 15 }}>
          WorldGit — Version Control for Reality
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span style={{ background: "#1e293b", padding: "3px 8px", borderRadius: 4 }}>
            Meta Display HUD: :4173
          </span>
          <span style={{ background: "#1e293b", padding: "3px 8px", borderRadius: 4 }}>
            Gemini: {ai?.gemini ? "Live Key" : "Sim Fallback"}
          </span>
          <span style={{ background: "#1e293b", padding: "3px 8px", borderRadius: 4 }}>
            Modal GPU: {ai?.modal_url === "stub-local-ready" ? "Local/Deployable" : "Live L4"}
          </span>
          <span style={{ background: "#1e293b", padding: "3px 8px", borderRadius: 4 }}>
            ElevenLabs: {ai?.elevenlabs ? "Live TTS" : "WebSpeech Fallback"}
          </span>
          <span style={{ background: "#1e293b", padding: "3px 8px", borderRadius: 4 }}>
            Aiven DB: {aiven?.backend || "connecting..."} ({aiven?.rows?.spatial_objects || 0} objs)
          </span>
        </div>
      </header>

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* Left 3D Spatial Viewport */}
        <div style={{ flex: 1, position: "relative" }}>
          <div ref={mountRef} style={{ width: "100%", height: "100%" }} />
          <div
            style={{
              position: "absolute",
              bottom: 14,
              left: 14,
              background: "rgba(15,23,42,0.85)",
              padding: "8px 12px",
              borderRadius: 8,
              fontSize: 12,
              border: "1px solid #334155",
            }}
          >
            <div>
              Points: <b>{points.length}</b> | Wearers: <b>{world.wearers.length}</b> | Objects:{" "}
              <b>{world.objects.length}</b> | Pins: <b>{world.pins.length}</b>
            </div>
            <div style={{ color: "#94a3b8", marginTop: 4 }}>
              Blue Cones = Wearers | Green Cubes = Objects | Red/Yellow/Purple Spheres = Pins & Friend's World | Yellow Wireframe = git diff
            </div>
          </div>
        </div>

        {/* Right Controls */}
        <aside
          style={{
            width: 390,
            background: "#111827",
            borderLeft: "1px solid #1f2937",
            padding: 14,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          {/* Ctrl+F Search */}
          <section style={{ background: "#1e293b", padding: 12, borderRadius: 8 }}>
            <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>Ctrl+F Physical Search</h3>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. drill, keys, socket..."
                style={{ flex: 1, padding: 6, borderRadius: 4, border: "none" }}
              />
              <button onClick={handleCtrlF} style={{ padding: "6px 10px", cursor: "pointer" }}>
                Find
              </button>
            </div>
            {ctrlFResult && (
              <p style={{ margin: "8px 0 0", fontSize: 12, color: "#38bdf8" }}>
                {ctrlFResult.arrow ? `${ctrlFResult.arrow} ` : ""}
                {ctrlFResult.directions || ctrlFResult.message}
              </p>
            )}
            {ttsStatus && (
              <p style={{ margin: "4px 0 0", fontSize: 11, color: "#94a3b8" }}>{ttsStatus}</p>
            )}
          </section>

          {/* Spatial Pin & Friend's World */}
          <section style={{ background: "#1e293b", padding: 12, borderRadius: 8 }}>
            <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>Spatial Pins & Friend's World</h3>
            <select
              value={pinType}
              onChange={(e) => setPinType(e.target.value)}
              style={{ width: "100%", padding: 6, marginBottom: 6 }}
            >
              <option value="warning">Team Warning (Auto-Whisper)</option>
              <option value="note">Spatial Note</option>
              <option value="friend_memory">Friend's World Memory</option>
            </select>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                value={pinText}
                onChange={(e) => setPinText(e.target.value)}
                placeholder="Pin warning or friend memory..."
                style={{ flex: 1, padding: 6, borderRadius: 4, border: "none" }}
              />
              <button onClick={handleDropPin} style={{ padding: "6px 10px", cursor: "pointer" }}>
                Pin
              </button>
            </div>
            <div style={{ marginTop: 8, maxHeight: 110, overflowY: "auto", fontSize: 11 }}>
              {world.pins.map((p) => (
                <div key={p.id} style={{ marginBottom: 4, color: "#cbd5e1" }}>
                  <b
                    style={{
                      color:
                        p.pin_type === "warning"
                          ? "#f87171"
                          : p.pin_type === "friend_memory"
                          ? "#c084fc"
                          : "#facc15",
                    }}
                  >
                    [{p.pin_type}]
                  </b>{" "}
                  {p.author} ({p.age_label}): {p.text}
                </div>
              ))}
            </div>
          </section>

          {/* git diff, Time Travel & Ctrl+Z */}
          <section style={{ background: "#1e293b", padding: 12, borderRadius: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>git diff & Ctrl+Z Undo</h3>
              <label style={{ fontSize: 11 }}>
                <input
                  type="checkbox"
                  checked={showDiffOverlay}
                  onChange={(e) => setShowDiffOverlay(e.target.checked)}
                />{" "}
                Diff Overlay
              </label>
            </div>
            <div style={{ marginTop: 8 }}>
              <label style={{ fontSize: 11, color: "#94a3b8" }}>
                Time Travel Snapshot:{" "}
                {snapshotIdx === -1
                  ? "Live (Latest)"
                  : world.snapshots[snapshotIdx]?.label || "Snapshot"}
              </label>
              <input
                type="range"
                min={-1}
                max={Math.max(0, world.snapshots.length - 1)}
                value={snapshotIdx}
                onChange={(e) => setSnapshotIdx(Number(e.target.value))}
                style={{ width: "100%" }}
              />
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button onClick={handleSnapshot} style={{ flex: 1, padding: 7, cursor: "pointer" }}>
                Commit Snapshot
              </button>
              <button onClick={handleCtrlZ} style={{ flex: 1, padding: 7, cursor: "pointer" }}>
                Ctrl+Z Undo
              </button>
            </div>
            {undoSteps.length > 0 && (
              <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12, color: "#facc15" }}>
                {undoSteps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            )}
          </section>

          {/* Live POV Frame Capture -> Modal + Gemini */}
          <section style={{ background: "#1e293b", padding: 12, borderRadius: 8 }}>
            <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>First-Person POV → Modal + Gemini</h3>
            <video
              ref={videoRef}
              autoPlay
              muted
              playsInline
              style={{
                width: "100%",
                height: cameraActive ? 110 : 0,
                objectFit: "cover",
                borderRadius: 6,
                background: "#0f172a",
              }}
            />
            <canvas ref={canvasRef} style={{ display: "none" }} />
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={startWebcam} style={{ flex: 1, padding: 6, cursor: "pointer" }}>
                Start Webcam
              </button>
              <button
                onClick={captureAndAnalyze}
                style={{ flex: 1, padding: 6, cursor: "pointer" }}
              >
                Scan & Index Frame
              </button>
            </div>
            <p style={{ margin: "6px 0 0", fontSize: 11, color: "#94a3b8" }}>{captureStatus}</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
