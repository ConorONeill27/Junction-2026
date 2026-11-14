import React from "react";
import { CloseIcon, CubeIcon, PinIcon, ZoneIcon } from "./Icons.jsx";

export default function ClickActionPopover({
  target,
  onClose,
  onDropNoteHere,
  onNameZoneHere,
  onRecordObjectHere,
}) {
  if (!target) return null;

  const left = Math.min(Math.max(16, target.screenX + 12), window.innerWidth - 260);
  const top = Math.min(Math.max(82, target.screenY - 20), window.innerHeight - 210);
  const pos = target.worldPosition || [0, 0.8, 0];

  return (
    <div
      className="wg-popover wg-glass"
      style={{ left, top }}
      role="dialog"
      aria-label="3D point actions"
    >
      <div className="wg-card-row" style={{ marginBottom: 4 }}>
        <span className="wg-section-label">
          3D Point ({pos.map((v) => Number(v).toFixed(2)).join(", ")})
        </span>
        <button
          type="button"
          className="wg-btn wg-btn-ghost wg-btn-icon"
          style={{ minHeight: 28, width: 28, minWidth: 28 }}
          onClick={onClose}
          aria-label="Close point actions"
        >
          <CloseIcon size={14} />
        </button>
      </div>

      <button
        type="button"
        className="wg-btn"
        style={{ justifyContent: "flex-start" }}
        onClick={() => {
          onDropNoteHere(pos);
          onClose();
        }}
      >
        <PinIcon size={16} />
        <span>Drop Note / Review Here</span>
      </button>

      <button
        type="button"
        className="wg-btn"
        style={{ justifyContent: "flex-start" }}
        onClick={() => {
          onNameZoneHere(pos);
          onClose();
        }}
      >
        <ZoneIcon size={16} />
        <span>Name This Zone</span>
      </button>

      <button
        type="button"
        className="wg-btn"
        style={{ justifyContent: "flex-start" }}
        onClick={() => {
          onRecordObjectHere(pos);
          onClose();
        }}
      >
        <CubeIcon size={16} />
        <span>Log Item Sighting Here</span>
      </button>
    </div>
  );
}
