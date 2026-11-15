import React, { useState } from "react";
import {
  ArrowRightIcon,
  CubeIcon,
  GlobeIcon,
  LockIcon,
  PinIcon,
  PlusIcon,
  UsersIcon,
  ZoneIcon,
} from "./Icons.jsx";

function formatAge(epochSec) {
  if (!epochSec) return "just now";
  const diff = Math.max(0, Math.round(Date.now() / 1000 - epochSec));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function AudienceBadge({ type }) {
  const map = {
    public: { label: "Public", cls: "wg-badge-public", icon: <GlobeIcon size={11} /> },
    friends: { label: "Friends", cls: "wg-badge-friends", icon: <UsersIcon size={11} /> },
    group: { label: "Group", cls: "wg-badge-group", icon: <UsersIcon size={11} /> },
    people: { label: "Selected", cls: "wg-badge-group", icon: <LockIcon size={11} /> },
    private: { label: "Only Me", cls: "wg-badge-private", icon: <LockIcon size={11} /> },
  };
  const cfg = map[type] || map.friends;
  return (
    <span className={`wg-badge ${cfg.cls}`}>
      {cfg.icon}
      <span>{cfg.label}</span>
    </span>
  );
}

export default function SideDrawer({
  open,
  objects = [],
  zones = [],
  history = [],
  pins = [],
  bootstrap = {},
  worldId,
  onFocusPosition,
  onOpenNoteModal,
  onPinToObject,
  onCreateZone,
  onReportSighting,
  onAddFriend,
  onCreateGroup,
  onJoinGroup,
  onShareSpace,
}) {
  const [tab, setTab] = useState("things");
  const [zoneName, setZoneName] = useState("");
  const [sightingLabel, setSightingLabel] = useState("");
  const [sightingActor, setSightingActor] = useState("usr-alec");
  const [sightingZone, setSightingZone] = useState("");
  const [friendCode, setFriendCode] = useState("");
  const [groupName, setGroupName] = useState("");
  const [groupKind, setGroupKind] = useState("household");
  const [joinCode, setJoinCode] = useState("");
  const [feedback, setFeedback] = useState("");

  const users = bootstrap.users || [];
  const friends = bootstrap.friends || [];
  const groups = bootstrap.groups || [];
  const spaces = bootstrap.spaces || [];
  const currentSpace = spaces.find((s) => s.world_id === worldId);

  const runAction = async (fn, okText) => {
    setFeedback("");
    try {
      await fn();
      if (okText) setFeedback(okText);
    } catch (e) {
      setFeedback(e?.message || "Action failed");
    }
  };

  return (
    <aside
      className={`wg-drawer wg-glass ${open ? "" : "closed"}`}
      aria-label="Space feed and social controls"
    >
      <div className="wg-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "things"}
          className={`wg-tab ${tab === "things" ? "active" : ""}`}
          onClick={() => setTab("things")}
        >
          <CubeIcon size={15} />
          <span>Things</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "notes"}
          className={`wg-tab ${tab === "notes" ? "active" : ""}`}
          onClick={() => setTab("notes")}
        >
          <PinIcon size={15} />
          <span>Notes ({pins.length})</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "people"}
          className={`wg-tab ${tab === "people" ? "active" : ""}`}
          onClick={() => setTab("people")}
        >
          <UsersIcon size={15} />
          <span>People</span>
        </button>
      </div>

      {feedback && (
        <div
          style={{
            margin: "10px 14px 0",
            padding: "8px 10px",
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
            background: "rgba(41, 151, 255, 0.14)",
            border: "1px solid rgba(41, 151, 255, 0.35)",
          }}
        >
          {feedback}
        </div>
      )}

      <div className="wg-drawer-body">
        {tab === "things" && (
          <>
            <div className="wg-card">
              <div className="wg-card-row">
                <span className="wg-section-label">Named Zones ({zones.length})</span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {zones.map((z) => {
                  const zid = z.zone_id || z.id || z.name;
                  const zpos = Array.isArray(z.position)
                    ? z.position
                    : [z.x ?? 0, z.y ?? 0.8, z.z ?? 0];
                  return (
                    <button
                      key={zid}
                      type="button"
                      className="wg-btn"
                      style={{ minHeight: 34, padding: "0 10px", fontSize: 12 }}
                      onClick={() => onFocusPosition && onFocusPosition(zpos)}
                    >
                      <ZoneIcon size={13} />
                      <span>{z.name}</span>
                    </button>
                  );
                })}
              </div>
              <form
                style={{ display: "flex", gap: 6, marginTop: 4 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!zoneName.trim()) return;
                  runAction(
                    () => onCreateZone({ name: zoneName.trim(), position: [0.6, 0.7, -0.4] }),
                    `Zone "${zoneName.trim()}" saved`
                  );
                  setZoneName("");
                }}
              >
                <input
                  type="text"
                  className="wg-input"
                  style={{ minHeight: 38 }}
                  placeholder="Add zone (e.g. Bedside Table, Desk)"
                  value={zoneName}
                  onChange={(e) => setZoneName(e.target.value)}
                />
                <button type="submit" className="wg-btn wg-btn-primary" style={{ minHeight: 38 }}>
                  Add
                </button>
              </form>
            </div>

            <div className="wg-card">
              <span className="wg-section-label">Log Item Sighting / Move</span>
              <form
                style={{ display: "flex", flexDirection: "column", gap: 6 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!sightingLabel.trim()) return;
                  const targetZone = zones.find(
                    (z) => z.name.toLowerCase() === sightingZone.toLowerCase()
                  );
                  const pos = Array.isArray(targetZone?.position)
                    ? targetZone.position
                    : targetZone
                    ? [targetZone.x ?? 0, targetZone.y ?? 0.8, targetZone.z ?? 0]
                    : [0.5, 0.8, -0.5];
                  runAction(
                    () =>
                      onReportSighting({
                        label: sightingLabel.trim(),
                        position: pos,
                        actor_user_id: sightingActor,
                        zone_hint: sightingZone,
                      }),
                    `Logged ${sightingLabel.trim()}`
                  );
                  setSightingLabel("");
                }}
              >
                <input
                  type="text"
                  className="wg-input"
                  style={{ minHeight: 38 }}
                  placeholder="Item (e.g. Phone Charger)"
                  value={sightingLabel}
                  onChange={(e) => setSightingLabel(e.target.value)}
                />
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                  <select
                    className="wg-select"
                    style={{ minHeight: 38 }}
                    value={sightingActor}
                    onChange={(e) => setSightingActor(e.target.value)}
                  >
                    {users.map((u) => {
                      const uid = u.user_id || u.id;
                      return (
                        <option key={uid} value={uid}>
                          {u.name}
                        </option>
                      );
                    })}
                  </select>
                  <select
                    className="wg-select"
                    style={{ minHeight: 38 }}
                    value={sightingZone}
                    onChange={(e) => setSightingZone(e.target.value)}
                  >
                    <option value="">Zone (optional)</option>
                    {zones.map((z) => {
                      const zid = z.zone_id || z.id || z.name;
                      return (
                        <option key={zid} value={z.name}>
                          {z.name}
                        </option>
                      );
                    })}
                  </select>
                </div>
                <button type="submit" className="wg-btn wg-btn-emerald" style={{ minHeight: 38 }}>
                  Record Sighting
                </button>
              </form>
            </div>

            <span className="wg-section-label" style={{ paddingLeft: 2 }}>
              Tracked Objects ({objects.length})
            </span>
            {objects.map((obj) => (
              <div
                key={obj.id}
                className="wg-card"
                style={{ cursor: "pointer" }}
                onClick={() => onFocusPosition && onFocusPosition(obj.position)}
              >
                <div className="wg-card-row">
                  <strong style={{ fontSize: 13.5 }}>{obj.label}</strong>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span className="wg-badge">{obj.zone_name || "Room"}</span>
                    {onPinToObject && (
                      <button
                        type="button"
                        className="wg-btn"
                        style={{ minHeight: 28, padding: "0 8px", fontSize: 11 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          onPinToObject(obj);
                        }}
                      >
                        <PinIcon size={12} />
                        <span>Pin Note</span>
                      </button>
                    )}
                  </div>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  Last seen by {obj.last_seen_by_name || obj.owner || "Wearer"} &bull;{" "}
                  {formatAge(obj.last_seen_at)}
                </div>
              </div>
            ))}

            <span className="wg-section-label" style={{ paddingLeft: 2 }}>
              Movement Ledger ({history.length})
            </span>
            {history.slice(0, 8).map((ev, idx) => (
              <div key={ev.event_id || ev.id || idx} className="wg-card">
                <div className="wg-card-row">
                  <strong style={{ fontSize: 13 }}>{ev.object_label}</strong>
                  <span
                    className={`wg-badge ${
                      ev.event_type === "moved" ? "wg-badge-moved" : ""
                    }`}
                  >
                    {ev.event_type}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 12.5,
                    color: "var(--text-secondary)",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    flexWrap: "wrap",
                  }}
                >
                  <span>{ev.actor_name}</span>
                  {ev.from_zone && ev.to_zone && ev.from_zone !== ev.to_zone ? (
                    <>
                      <span>{ev.from_zone}</span>
                      <ArrowRightIcon size={12} />
                      <strong style={{ color: "var(--text-primary)" }}>{ev.to_zone}</strong>
                    </>
                  ) : (
                    <span>in {ev.to_zone || "Room"}</span>
                  )}
                </div>
              </div>
            ))}
          </>
        )}

        {tab === "notes" && (
          <>
            <button type="button" className="wg-btn wg-btn-primary" onClick={onOpenNoteModal}>
              <PlusIcon size={16} />
              <span>Pin New Note or Place Review</span>
            </button>

            {pins.length === 0 ? (
              <div className="wg-card" style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                No notes visible for this account in the current space yet.
              </div>
            ) : (
              pins.map((pin) => (
                <button
                  key={pin.id}
                  type="button"
                  className="wg-card"
                  style={{ textAlign: "left", cursor: "pointer" }}
                  onClick={() => onFocusPosition && onFocusPosition(pin.position)}
                >
                  <div className="wg-card-row">
                    <strong style={{ fontSize: 13.5 }}>
                      {pin.place_name || pin.landmark || "Spatial Note"}
                      {pin.rating ? ` (${pin.rating}/5)` : ""}
                    </strong>
                    <AudienceBadge type={pin.audience_type} />
                  </div>
                  <div style={{ fontSize: 13, color: "var(--text-primary)" }}>{pin.text}</div>
                  <div style={{ fontSize: 11.5, color: "var(--text-secondary)" }}>
                    By {pin.author} &bull; {pin.age_label || formatAge(pin.created_at)}
                  </div>
                </button>
              ))
            )}
          </>
        )}

        {tab === "people" && (
          <>
            <div className="wg-card">
              <span className="wg-section-label">Your Profile</span>
              <div className="wg-card-row">
                <strong>{bootstrap.viewer?.name || "Conor"}</strong>
                <span className="wg-badge wg-mono">
                  {bootstrap.viewer?.friend_code || "CONOR-101"}
                </span>
              </div>
              {currentSpace && (
                <label style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                  <span className="wg-section-label">Space Sharing ({currentSpace.display_name})</span>
                  <select
                    className="wg-select"
                    value={currentSpace.audience_type || "group"}
                    onChange={(e) =>
                      runAction(
                        () =>
                          onShareSpace({
                            audience_type: e.target.value,
                            group_ids: currentSpace.group_ids || [],
                          }),
                        "Space sharing updated"
                      )
                    }
                  >
                    <option value="group">Household / Group Only</option>
                    <option value="friends">All Friends</option>
                    <option value="public">Public</option>
                    <option value="private">Private (Owner Only)</option>
                  </select>
                </label>
              )}
            </div>

            <div className="wg-card">
              <span className="wg-section-label">Friends ({friends.length})</span>
              {friends.map((f) => {
                const fid = f.user_id || f.id;
                return (
                  <div key={fid} className="wg-card-row" style={{ fontSize: 13 }}>
                    <span>{f.name}</span>
                    <span className="wg-badge wg-mono">{f.handle}</span>
                  </div>
                );
              })}
              <form
                style={{ display: "flex", gap: 6, marginTop: 4 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!friendCode.trim()) return;
                  runAction(
                    () => onAddFriend(friendCode.trim()),
                    `Added friend ${friendCode.trim()}`
                  );
                  setFriendCode("");
                }}
              >
                <input
                  type="text"
                  className="wg-input"
                  style={{ minHeight: 38 }}
                  placeholder="Handle or friend code"
                  value={friendCode}
                  onChange={(e) => setFriendCode(e.target.value)}
                />
                <button type="submit" className="wg-btn wg-btn-primary" style={{ minHeight: 38 }}>
                  Add
                </button>
              </form>
            </div>

            <div className="wg-card">
              <span className="wg-section-label">Groups &amp; Households ({groups.length})</span>
              {groups.map((g) => {
                const gid = g.group_id || g.id;
                return (
                  <div key={gid} className="wg-card-row" style={{ fontSize: 12.5 }}>
                    <span>
                      <strong>{g.name}</strong> ({g.kind})
                    </span>
                    <span className="wg-badge wg-mono">{g.invite_code}</span>
                  </div>
                );
              })}
              <form
                style={{ display: "flex", gap: 6, marginTop: 4 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!groupName.trim()) return;
                  runAction(
                    () => onCreateGroup({ name: groupName.trim(), kind: groupKind }),
                    `Created ${groupName.trim()}`
                  );
                  setGroupName("");
                }}
              >
                <input
                  type="text"
                  className="wg-input"
                  style={{ minHeight: 38 }}
                  placeholder="New group name"
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                />
                <select
                  className="wg-select"
                  style={{ minHeight: 38, width: 112 }}
                  value={groupKind}
                  onChange={(e) => setGroupKind(e.target.value)}
                >
                  <option value="household">Household</option>
                  <option value="friends">Friends</option>
                </select>
                <button type="submit" className="wg-btn wg-btn-primary" style={{ minHeight: 38 }}>
                  Create
                </button>
              </form>
              <form
                style={{ display: "flex", gap: 6 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!joinCode.trim()) return;
                  runAction(() => onJoinGroup(joinCode.trim()), `Joined ${joinCode.trim()}`);
                  setJoinCode("");
                }}
              >
                <input
                  type="text"
                  className="wg-input"
                  style={{ minHeight: 38 }}
                  placeholder="Invite code (e.g. ROOM-01)"
                  value={joinCode}
                  onChange={(e) => setJoinCode(e.target.value)}
                />
                <button type="submit" className="wg-btn" style={{ minHeight: 38 }}>
                  Join
                </button>
              </form>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
