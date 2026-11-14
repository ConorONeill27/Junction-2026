import React from "react";
import { CloseIcon, SlidersIcon } from "./Icons.jsx";

export default function StudioDrawer({
  open,
  onClose,
  pointCount,
  rawPointCount,
  worlds,
  activeWorldId,
  onSelectWorld,
  showPointCloud,
  setShowPointCloud,
  showFrustums,
  setShowFrustums,
  autoOrbit,
  setAutoOrbit,
  splatSize,
  setSplatSize,
  onFocusBottle,
  onLocateCapturedVideo,
  locatingVideo,
  onUploadWalkthrough,
  scanJobs,
  statusText,
}) {
  return (
    <aside
      className={`wg-studio-drawer wg-glass ${open ? "" : "closed"}`}
      aria-label="Scan and Studio Tools"
    >
      <div
        className="wg-card-row"
        style={{ padding: "12px 14px", borderBottom: "1px solid var(--border-glass)" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <SlidersIcon size={16} />
          <strong style={{ fontSize: 13.5 }}>Scan &amp; Studio Tools</strong>
        </div>
        <button
          type="button"
          className="wg-btn wg-btn-ghost wg-btn-icon"
          style={{ minHeight: 36, width: 36, minWidth: 36 }}
          onClick={onClose}
          aria-label="Close studio tools"
        >
          <CloseIcon size={16} />
        </button>
      </div>

      <div className="wg-drawer-body">
        <div className="wg-card">
          <span className="wg-section-label">Scanned Space Mesh</span>
          <div style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>
            Rendered Splats: <strong style={{ color: "var(--text-primary)" }}>{pointCount}</strong>{" "}
            ({rawPointCount} raw)
          </div>
          <label className="wg-section-label" htmlFor="studio-world-select">
            Switch Raw Scan
          </label>
          <select
            id="studio-world-select"
            className="wg-select"
            value={activeWorldId || ""}
            onChange={(e) => onSelectWorld(e.target.value)}
          >
            {(worlds || []).map((w) => (
              <option key={w.world_id} value={w.world_id}>
                {w.name || w.world_id} ({w.point_count || 0} pts)
              </option>
            ))}
          </select>
        </div>

        <div className="wg-card">
          <span className="wg-section-label">Viewport Controls</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className={`wg-btn ${showPointCloud ? "wg-btn-emerald" : ""}`}
              onClick={() => setShowPointCloud((v) => !v)}
            >
              {showPointCloud ? "Hide Cloud" : "Show Cloud"}
            </button>
            <button
              type="button"
              className={`wg-btn ${showFrustums ? "wg-btn-emerald" : ""}`}
              onClick={() => setShowFrustums((v) => !v)}
            >
              {showFrustums ? "Hide Cameras" : "Show Cameras"}
            </button>
            <button
              type="button"
              className={`wg-btn ${autoOrbit ? "wg-btn-emerald" : ""}`}
              onClick={() => setAutoOrbit((v) => !v)}
            >
              {autoOrbit ? "Pause Orbit" : "Auto Orbit"}
            </button>
            <button type="button" className="wg-btn" onClick={onFocusBottle}>
              Focus Bottle
            </button>
          </div>
          <label style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 6 }}>
            Splat Size: {splatSize.toFixed(3)}m
            <input
              type="range"
              min="0.008"
              max="0.05"
              step="0.002"
              value={splatSize}
              onChange={(e) => setSplatSize(parseFloat(e.target.value))}
              style={{ width: "100%", marginTop: 4 }}
            />
          </label>
        </div>

        <div className="wg-card">
          <span className="wg-section-label">Live Scan &amp; Localization</span>
          <button
            type="button"
            className="wg-btn wg-btn-primary"
            disabled={locatingVideo}
            onClick={onLocateCapturedVideo}
          >
            {locatingVideo ? "Localizing Video..." : "Locate Video on 3D Map"}
          </button>
          <label className="wg-btn" style={{ cursor: "pointer" }}>
            <span>Upload Walkthrough Video / Scan</span>
            <input
              type="file"
              accept="video/*"
              style={{ display: "none" }}
              onChange={onUploadWalkthrough}
            />
          </label>
          {Object.values(scanJobs || {}).length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
              {Object.values(scanJobs).map((job) => (
                <div
                  key={job.job_id}
                  style={{
                    fontSize: 12,
                    padding: "6px 8px",
                    borderRadius: "var(--radius-sm)",
                    background: "rgba(255,255,255,0.04)",
                    border: "1px solid var(--border-glass)",
                  }}
                >
                  <strong>{job.world_id}</strong> — {job.status} ({job.detail || "processing"})
                </div>
              ))}
            </div>
          )}
        </div>

        {statusText && (
          <div style={{ fontSize: 12, color: "var(--text-secondary)", padding: "0 4px" }}>
            {statusText}
          </div>
        )}
      </div>
    </aside>
  );
}
