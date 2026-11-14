import React, { useEffect, useState } from "react";
import { CloseIcon, PinIcon, StarIcon } from "./Icons.jsx";

export default function NoteComposerModal({
  open,
  onClose,
  initialPosition,
  groups = [],
  friends = [],
  onSubmit,
}) {
  const [pinType, setPinType] = useState("note");
  const [placeName, setPlaceName] = useState("");
  const [rating, setRating] = useState(5);
  const [text, setText] = useState("");
  const [audienceType, setAudienceType] = useState("friends");
  const [selectedGroupIds, setSelectedGroupIds] = useState([]);
  const [selectedUserIds, setSelectedUserIds] = useState([]);
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (open) {
      setErr("");
      setSaving(false);
    }
  }, [open]);

  if (!open) return null;

  const toggleItem = (list, setList, id) => {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!text.trim()) {
      setErr("Please enter a note or review.");
      return;
    }
    if (pinType === "place_review" && !placeName.trim()) {
      setErr("Please enter a place name for this review.");
      return;
    }
    setSaving(true);
    setErr("");
    try {
      await onSubmit({
        pin_type: pinType,
        place_name: pinType === "place_review" ? placeName.trim() : "",
        rating: pinType === "place_review" ? rating : null,
        text: text.trim(),
        landmark: pinType === "place_review" ? placeName.trim() : "Spatial Note",
        position: initialPosition || [0, 1.0, -1.2],
        audience_type: audienceType,
        group_ids: audienceType === "group" ? selectedGroupIds : [],
        target_user_ids: audienceType === "people" ? selectedUserIds : [],
        lat: lat.trim() ? parseFloat(lat) : null,
        lon: lon.trim() ? parseFloat(lon) : null,
      });
      setText("");
      setPlaceName("");
      onClose();
    } catch (error) {
      setErr(error?.message || "Could not save note");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="wg-modal-backdrop" role="presentation" onClick={onClose}>
      <form
        className="wg-modal wg-glass"
        role="dialog"
        aria-label="Add spatial note or place review"
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <div className="wg-card-row">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <PinIcon size={18} />
            <strong style={{ fontSize: 15 }}>Pin Spatial Note</strong>
          </div>
          <button
            type="button"
            className="wg-btn wg-btn-ghost wg-btn-icon"
            style={{ minHeight: 36, width: 36, minWidth: 36 }}
            onClick={onClose}
            aria-label="Close modal"
          >
            <CloseIcon size={16} />
          </button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <button
            type="button"
            className={`wg-btn ${pinType === "note" ? "wg-btn-primary" : ""}`}
            onClick={() => setPinType("note")}
          >
            Room / Object Note
          </button>
          <button
            type="button"
            className={`wg-btn ${pinType === "place_review" ? "wg-btn-primary" : ""}`}
            onClick={() => setPinType("place_review")}
          >
            Place Review
          </button>
        </div>

        {pinType === "place_review" && (
          <>
            <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="wg-section-label">Place Name</span>
              <input
                type="text"
                className="wg-input"
                placeholder="e.g. Subway, Espresso Bar"
                value={placeName}
                onChange={(e) => setPlaceName(e.target.value)}
              />
            </label>

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span className="wg-section-label">Rating</span>
              <div style={{ display: "flex", gap: 6 }}>
                {[1, 2, 3, 4, 5].map((star) => (
                  <button
                    key={star}
                    type="button"
                    className="wg-btn wg-btn-icon"
                    style={{
                      minHeight: 36,
                      width: 36,
                      minWidth: 36,
                      color: star <= rating ? "var(--accent-amber)" : "var(--text-muted)",
                    }}
                    onClick={() => setRating(star)}
                    aria-label={`${star} stars`}
                  >
                    <StarIcon size={16} filled={star <= rating} />
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="wg-section-label">
            {pinType === "place_review" ? "Your Review" : "Note"}
          </span>
          <textarea
            className="wg-textarea"
            placeholder={
              pinType === "place_review"
                ? "Great turkey sub, quick line..."
                : "Put spare house key in top drawer..."
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="wg-section-label">Who can see this?</span>
          <select
            className="wg-select"
            value={audienceType}
            onChange={(e) => setAudienceType(e.target.value)}
          >
            <option value="public">Public (Anyone)</option>
            <option value="friends">Friends</option>
            <option value="group">Specific Group(s)</option>
            <option value="people">Specific People</option>
            <option value="private">Only Me</option>
          </select>
        </label>

        {audienceType === "group" && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {groups.map((g) => {
              const gid = g.group_id || g.id;
              const active = selectedGroupIds.includes(gid);
              return (
                <button
                  key={gid}
                  type="button"
                  className={`wg-btn ${active ? "wg-btn-emerald" : ""}`}
                  style={{ minHeight: 36 }}
                  onClick={() => toggleItem(selectedGroupIds, setSelectedGroupIds, gid)}
                >
                  {g.name}
                </button>
              );
            })}
          </div>
        )}

        {audienceType === "people" && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {friends.map((f) => {
              const fid = f.user_id || f.id;
              const active = selectedUserIds.includes(fid);
              return (
                <button
                  key={fid}
                  type="button"
                  className={`wg-btn ${active ? "wg-btn-emerald" : ""}`}
                  style={{ minHeight: 36 }}
                  onClick={() => toggleItem(selectedUserIds, setSelectedUserIds, fid)}
                >
                  {f.name}
                </button>
              );
            })}
          </div>
        )}

        {pinType === "place_review" && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <input
              type="text"
              className="wg-input"
              placeholder="Lat (optional, e.g. 53.3498)"
              value={lat}
              onChange={(e) => setLat(e.target.value)}
            />
            <input
              type="text"
              className="wg-input"
              placeholder="Lon (optional, e.g. -6.2603)"
              value={lon}
              onChange={(e) => setLon(e.target.value)}
            />
          </div>
        )}

        {err && <div style={{ color: "var(--accent-danger)", fontSize: 12.5 }}>{err}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" className="wg-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="wg-btn wg-btn-primary" disabled={saving}>
            {saving ? "Saving..." : "Pin Note"}
          </button>
        </div>
      </form>
    </div>
  );
}
