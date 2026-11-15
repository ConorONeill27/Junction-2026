import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = "";

async function jsonPost(url, body) {
  const res = await fetch(`${API_BASE}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = Array.isArray(data.detail)
      ? data.detail.map((d) => d.msg || JSON.stringify(d)).join(", ")
      : typeof data.detail === "string"
      ? data.detail
      : `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

export function useMultiplayerSpace(initialViewerId = "usr-conor", initialWorldId = "world_1") {
  const [viewerId, setViewerId] = useState(initialViewerId);
  const [worldId, setWorldId] = useState(initialWorldId);
  const [bootstrap, setBootstrap] = useState({
    viewer: null,
    users: [],
    friends: [],
    groups: [],
    spaces: [],
    scan_jobs: {},
  });
  const [zones, setZones] = useState([]);
  const [history, setHistory] = useState([]);
  const [askResult, setAskResult] = useState(null);
  const [liveState, setLiveState] = useState(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [errorBanner, setErrorBanner] = useState("");
  const wsRef = useRef(null);

  const refreshBootstrap = useCallback(async (vId = viewerId) => {
    const res = await fetch(`${API_BASE}/api/social/bootstrap?viewer_id=${encodeURIComponent(vId)}`);
    if (!res.ok) return null;
    const data = await res.json();
    setBootstrap(data);
    return data;
  }, [viewerId]);

  const refreshState = useCallback(async (vId = viewerId, wId = worldId) => {
    const res = await fetch(
      `${API_BASE}/api/state?viewer_id=${encodeURIComponent(vId)}&world_id=${encodeURIComponent(wId)}`
    );
    if (!res.ok) return null;
    const data = await res.json();
    setLiveState(data);
    if (Array.isArray(data.zones)) setZones(data.zones);
    return data;
  }, [viewerId, worldId]);

  const refreshHistory = useCallback(async (query = "", vId = viewerId, wId = worldId) => {
    const res = await fetch(
      `${API_BASE}/api/objects/history?world_id=${encodeURIComponent(wId)}&viewer_id=${encodeURIComponent(vId)}&query=${encodeURIComponent(query)}`
    );
    if (!res.ok) return [];
    const data = await res.json();
    setHistory(data.events || []);
    return data.events || [];
  }, [viewerId, worldId]);

  const refreshAll = useCallback(async (vId = viewerId, wId = worldId) => {
    await Promise.all([refreshBootstrap(vId), refreshState(vId, wId), refreshHistory("", vId, wId)]);
  }, [refreshBootstrap, refreshState, refreshHistory, viewerId, worldId]);

  useEffect(() => {
    refreshAll(viewerId, worldId).catch(() => {});
  }, [viewerId, worldId, refreshAll]);

  useEffect(() => {
    let disposed = false;
    let reconnectTimer = null;
    let attempt = 0;

    const connect = () => {
      if (disposed) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${proto}//${window.location.host}/ws/stream?viewer_id=${encodeURIComponent(viewerId)}&world_id=${encodeURIComponent(worldId)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (disposed) {
          try { ws.close(); } catch {}
          return;
        }
        attempt = 0;
        setWsConnected(true);
      };
      ws.onclose = () => {
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
        setWsConnected(false);
        if (disposed) return;
        const delayMs = Math.min(10000, 1000 * Math.pow(2, attempt));
        attempt = Math.min(attempt + 1, 4);
        reconnectTimer = window.setTimeout(connect, delayMs);
      };
      ws.onerror = () => {
        try { ws.close(); } catch {}
      };
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "world_state" || msg.type === "state") {
            setLiveState(msg);
            if (Array.isArray(msg.zones)) setZones(msg.zones);
          } else if (msg.type === "wearers_update" || msg.type === "wearers") {
            setLiveState((prev) => (prev ? { ...prev, wearers: msg.wearers || [] } : prev));
          }
        } catch {
          // ignore malformed WS frames
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      try {
        wsRef.current?.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    };
  }, [viewerId, worldId]);

  const askWhere = useCallback(async (query) => {
    const data = await jsonPost("/api/ask", {
      viewer_id: viewerId,
      wearer_id: "wearer-a",
      query,
      world_id: worldId,
    });
    setAskResult(data);
    return data;
  }, [viewerId, worldId]);

  const createPin = useCallback(async (payload) => {
    const viewerName = bootstrap.viewer?.name || "Conor";
    const data = await jsonPost("/api/pin", {
      world_id: worldId,
      author: viewerName,
      author_id: viewerId,
      ...payload,
    });
    await refreshAll(viewerId, worldId);
    return data;
  }, [bootstrap.viewer, refreshAll, viewerId, worldId]);

  const createZone = useCallback(async ({ name, position, radius_m = 1.4 }) => {
    const data = await jsonPost("/api/zones", {
      world_id: worldId,
      name,
      position,
      radius_m,
      created_by: viewerId,
    });
    await refreshAll(viewerId, worldId);
    return data;
  }, [refreshAll, viewerId, worldId]);

  const reportSighting = useCallback(async ({ label, position, actor_user_id = viewerId, zone_hint = "" }) => {
    const data = await jsonPost("/api/objects/sighting", {
      world_id: worldId,
      label,
      position,
      actor_user_id,
      zone_hint,
    });
    await refreshAll(viewerId, worldId);
    return data;
  }, [refreshAll, viewerId, worldId]);

  const addFriend = useCallback(async (targetHandleOrCode) => {
    const data = await jsonPost("/api/social/friends/add", {
      user_id: viewerId,
      target_handle_or_code: targetHandleOrCode,
    });
    await refreshBootstrap(viewerId);
    return data;
  }, [refreshBootstrap, viewerId]);

  const createGroup = useCallback(async ({ name, kind = "friends", member_ids = [] }) => {
    const data = await jsonPost("/api/social/groups", {
      owner_id: viewerId,
      name,
      kind,
      member_ids,
    });
    await refreshBootstrap(viewerId);
    return data;
  }, [refreshBootstrap, viewerId]);

  const joinGroup = useCallback(async (inviteCode) => {
    const data = await jsonPost("/api/social/groups/join", {
      user_id: viewerId,
      invite_code: inviteCode,
    });
    await refreshBootstrap(viewerId);
    return data;
  }, [refreshBootstrap, viewerId]);

  const shareSpace = useCallback(async ({ audience_type, group_ids = [], display_name = "" }) => {
    const data = await jsonPost("/api/spaces/share", {
      world_id: worldId,
      viewer_id: viewerId,
      display_name,
      audience_type,
      group_ids,
    });
    await refreshBootstrap(viewerId);
    return data;
  }, [refreshBootstrap, viewerId, worldId]);

  const createProfile = useCallback(async (name) => {
    const data = await jsonPost("/api/social/profiles", { name });
    if (data.user?.id) {
      setViewerId(data.user.id);
    }
    return data;
  }, []);

  const autoIndexSpace = useCallback(async () => {
    const data = await jsonPost(`/api/spaces/${encodeURIComponent(worldId)}/auto-index`, {
      viewer_id: viewerId,
    });
    await refreshAll(viewerId, worldId);
    return data;
  }, [refreshAll, viewerId, worldId]);

  return {
    viewerId,
    setViewerId,
    worldId,
    setWorldId,
    bootstrap,
    zones,
    history,
    askResult,
    setAskResult,
    liveState,
    wsConnected,
    errorBanner,
    setErrorBanner,
    refreshAll,
    refreshState,
    refreshHistory,
    askWhere,
    createPin,
    createZone,
    reportSighting,
    autoIndexSpace,
    addFriend,
    createGroup,
    joinGroup,
    shareSpace,
    createProfile,
  };
}
