import React, { useState } from "react";
import {
  GlassesIcon,
  PlusIcon,
  SearchIcon,
  SlidersIcon,
  SparkIcon,
} from "./Icons.jsx";

export default function TopNav({
  viewerId,
  onChangeViewer,
  users = [],
  worldId,
  onChangeWorld,
  spaces = [],
  onAsk,
  onOpenNoteModal,
  drawerOpen,
  onToggleDrawer,
  studioOpen,
  onToggleStudio,
  wsConnected,
}) {
  const [query, setQuery] = useState("");
  const [asking, setAsking] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || asking) return;
    setAsking(true);
    try {
      await onAsk(trimmed);
    } finally {
      setAsking(false);
    }
  };

  return (
    <header className="wg-topnav" aria-label="Primary controls">
      <div className="wg-topnav-cluster wg-glass">
        <div className="wg-brand">
          <span
            className="wg-brand-dot"
            title={wsConnected ? "Live spatial sync active" : "Reconnecting"}
            style={{
              background: wsConnected ? "var(--accent-emerald)" : "var(--accent-amber)",
            }}
          />
          <span>WorldGit</span>
        </div>

        <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="wg-section-label" style={{ display: "none" }}>
            Space
          </span>
          <select
            aria-label="Active space"
            className="wg-pill-select"
            value={worldId}
            onChange={(e) => onChangeWorld(e.target.value)}
          >
            {(spaces.length ? spaces : [{ world_id: "world_1", display_name: "Hotel Room" }]).map(
              (sp) => (
                <option key={sp.world_id} value={sp.world_id}>
                  {sp.display_name || sp.world_id}
                </option>
              )
            )}
          </select>
        </label>
      </div>

      <form className="wg-search-pill wg-glass" onSubmit={handleSubmit}>
        <SearchIcon size={17} />
        <input
          type="text"
          className="wg-search-input"
          aria-label="Ask where something is"
          placeholder="Ask where something is... e.g. Where is my charger?"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          type="submit"
          className="wg-btn wg-btn-primary"
          style={{ minHeight: 36, borderRadius: "var(--radius-pill)", padding: "0 14px" }}
          disabled={asking || !query.trim()}
        >
          <SparkIcon size={15} />
          <span>{asking ? "Asking..." : "Ask"}</span>
        </button>
      </form>

      <div className="wg-topnav-cluster wg-glass">
        <select
          aria-label="Viewing as"
          className="wg-pill-select"
          value={viewerId}
          onChange={(e) => onChangeViewer(e.target.value)}
        >
          {(() => {
            const baseUsers = users.length
              ? users
              : [
                  { user_id: "usr-conor", name: "Conor" },
                  { user_id: "usr-alec", name: "Alec" },
                ];
            const hasGuest = baseUsers.some((u) => (u.user_id || u.id) === "guest");
            const allUsers = hasGuest ? baseUsers : [...baseUsers, { user_id: "guest", name: "Guest" }];
            return allUsers.map((u) => {
              const uid = u.user_id || u.id;
              return (
                <option key={uid} value={uid}>
                  View as: {u.name}
                </option>
              );
            });
          })()}
        </select>

        <button
          type="button"
          className="wg-btn wg-btn-primary"
          style={{ minHeight: 38, borderRadius: "var(--radius-pill)" }}
          onClick={onOpenNoteModal}
        >
          <PlusIcon size={16} />
          <span>Add Note</span>
        </button>

        <button
          type="button"
          className={`wg-btn wg-btn-icon ${studioOpen ? "wg-btn-emerald" : ""}`}
          style={{ minHeight: 38, height: 38, width: 38, minWidth: 38, borderRadius: "var(--radius-pill)" }}
          onClick={onToggleStudio}
          title="Scan & Studio Tools"
          aria-label="Scan & Studio Tools"
        >
          <SlidersIcon size={16} />
        </button>

        <button
          type="button"
          className={`wg-btn ${drawerOpen ? "wg-btn-emerald" : ""}`}
          style={{ minHeight: 38, borderRadius: "var(--radius-pill)" }}
          onClick={onToggleDrawer}
        >
          <GlassesIcon size={16} />
          <span>{drawerOpen ? "Hide Panel" : "Space Feed"}</span>
        </button>
      </div>
    </header>
  );
}
