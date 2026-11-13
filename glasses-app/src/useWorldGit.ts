import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {HudMode} from './webmcp';

const API_BASE = 'http://127.0.0.1:8000';
const WS_URL = 'ws://127.0.0.1:8000/ws/stream';

export interface SpatialObject {
  id: string;
  label: string;
  position: number[];
  seen_by: string;
  timestamp: number;
}

export interface SpatialPin {
  id: string;
  author: string;
  text: string;
  pin_type: string;
  position: number[];
  landmark: string;
  age_label?: string;
}

export interface Wearer {
  wearer_id: string;
  name: string;
  position: number[];
  yaw: number;
}

export interface WorldDiff {
  added: SpatialObject[];
  removed: SpatialObject[];
  moved: Array<{label: string; distance_m: number}>;
}

const FALLBACK_OBJECTS: SpatialObject[] = [
  {id: 'obj-drill', label: 'Red DeWalt Drill', position: [-1.8, 0.9, -2.2], seen_by: 'Alex', timestamp: 0},
  {id: 'obj-keys', label: 'Car Keys', position: [-0.5, 0.75, 1.2], seen_by: 'Alex', timestamp: 0},
  {id: 'obj-socket', label: '10mm Socket Wrench', position: [2.4, 0.8, -1.0], seen_by: 'Sam', timestamp: 0},
];

const FALLBACK_PINS: SpatialPin[] = [
  {
    id: 'pin-1',
    author: 'Alex',
    text: 'LIVE 400V PANEL — Do not flip Breaker #4',
    pin_type: 'warning',
    position: [-2.2, 1.3, -1.8],
    landmark: 'Breaker Panel',
    age_label: '2m ago',
  },
  {
    id: 'pin-3',
    author: 'Sam',
    text: 'My favourite flat white in Helsinki — get the cardamom bun!',
    pin_type: 'friend_memory',
    position: [-1.0, 1.2, 2.0],
    landmark: 'Corner Espresso Bar',
    age_label: '2 years ago',
  },
];

export function computeLocalDirection(wearerPos: number[], wearerYaw: number, target: SpatialObject) {
  const dx = target.position[0] - wearerPos[0];
  const dz = target.position[2] - wearerPos[2];
  const dist = Math.round(Math.hypot(dx, dz) * 10) / 10;
  const angle = ((Math.round((Math.atan2(dx, -dz) * 180) / Math.PI) - wearerYaw) % 360 + 360) % 360;
  let arrow = '↑';
  let turn = 'straight ahead';
  if (angle >= 25 && angle < 70) {
    arrow = '↗';
    turn = 'ahead-right';
  } else if (angle >= 70 && angle < 115) {
    arrow = '→';
    turn = 'to your right';
  } else if (angle >= 115 && angle < 180) {
    arrow = '↘';
    turn = 'behind-right';
  } else if (angle >= 180 && angle < 245) {
    arrow = '↙';
    turn = 'behind-left';
  } else if (angle >= 245 && angle < 290) {
    arrow = '←';
    turn = 'to your left';
  } else if (angle >= 290 && angle <= 335) {
    arrow = '↖';
    turn = 'ahead-left';
  }
  return {
    arrow,
    dist,
    text: `${target.label} seen by ${target.seen_by}. Walk ${dist}m ${turn}.`,
  };
}

export function useWorldGit() {
  const [mode, setMode] = useState<HudMode>('ctrlf');
  const [objects, setObjects] = useState<SpatialObject[]>(FALLBACK_OBJECTS);
  const [pins, setPins] = useState<SpatialPin[]>(FALLBACK_PINS);
  const [wearers, setWearers] = useState<Wearer[]>([
    {wearer_id: 'wearer-b', name: 'Conor (You)', position: [1.5, 1.6, 2.5], yaw: 0},
    {wearer_id: 'wearer-a', name: 'Alex', position: [-2.0, 1.6, -1.5], yaw: 45},
    {wearer_id: 'wearer-c', name: 'Sam', position: [-3.5, 1.6, 1.0], yaw: 120},
  ]);
  const [diff, setDiff] = useState<WorldDiff>({
    added: [{id: 'obj-laser', label: 'Laser Level', position: [3.0, 0.9, 0.5], seen_by: 'Conor', timestamp: 0}],
    removed: [{id: 'obj-battery', label: 'Backup Battery', position: [1.0, 0.8, 0.0], seen_by: 'Alex', timestamp: 0}],
    moved: [{label: 'Red DeWalt Drill', distance_m: 2.33}],
  });
  const [undoSteps, setUndoSteps] = useState<string[]>([
    'Move Red DeWalt Drill back 2.33m to (-0.2, -0.5).',
    'Return missing Backup Battery to (1.0, 0.0).',
    'Remove newly placed Laser Level.',
  ]);
  const [itemIndex, setItemIndex] = useState(0);
  const [searchMessage, setSearchMessage] = useState('');
  const [statusNote, setStatusNote] = useState('Hub syncing...');
  const wsRef = useRef<WebSocket | null>(null);
  const stepRef = useRef(0);

  const selfWearer = useMemo(
    () => wearers.find(w => w.wearer_id === 'wearer-b') ?? wearers[0],
    [wearers],
  );

  const activeTarget = objects[itemIndex % Math.max(1, objects.length)] ?? FALLBACK_OBJECTS[0];
  const localNav = useMemo(
    () => computeLocalDirection(selfWearer.position, selfWearer.yaw, activeTarget),
    [selfWearer, activeTarget],
  );

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    fetch(`${API_BASE}/api/state`, {signal: controller.signal})
      .then(r => r.json())
      .then(data => {
        if (!active) return;
        if (Array.isArray(data.objects) && data.objects.length > 0) setObjects(data.objects);
        if (Array.isArray(data.pins) && data.pins.length > 0) setPins(data.pins);
        if (Array.isArray(data.wearers) && data.wearers.length > 0) setWearers(data.wearers);
        if (data.diff) setDiff(data.diff);
        setStatusNote('Connected to WorldGit Hub');
      })
      .catch(() => {
        if (active) setStatusNote('Offline cache mode');
      });

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        if (active) setStatusNote('Live WebSocket synced');
      };
      ws.onmessage = ev => {
        if (!active) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'world_state') {
            if (Array.isArray(msg.objects) && msg.objects.length > 0) setObjects(msg.objects);
            if (Array.isArray(msg.pins) && msg.pins.length > 0) setPins(msg.pins);
            if (Array.isArray(msg.wearers) && msg.wearers.length > 0) setWearers(msg.wearers);
            if (msg.diff) setDiff(msg.diff);
          }
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onerror = () => {
        if (active) setStatusNote('Offline cache mode');
      };
    } catch {
      setStatusNote('Offline cache mode');
    }

    return () => {
      active = false;
      controller.abort();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  const findObjectAction = useCallback(
    async (query: string) => {
      setMode('ctrlf');
      const idx = objects.findIndex(o => o.label.toLowerCase().includes(query.toLowerCase()));
      if (idx >= 0) setItemIndex(idx);
      try {
        const res = await fetch(`${API_BASE}/api/ctrl-f`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({wearer_id: 'wearer-b', query}),
        });
        const data = await res.json();
        const msg = data.directions || data.message || localNav.text;
        setSearchMessage(msg);
        return data;
      } catch {
        setSearchMessage(localNav.text);
        return {found: true, directions: localNav.text};
      }
    },
    [objects, localNav.text],
  );

  const pinMemoryAction = useCallback(
    async (text: string, kind: string) => {
      setMode('pins');
      const normalized = kind.includes('warn')
        ? 'warning'
        : kind.includes('friend')
          ? 'friend_memory'
          : 'note';
      const newPin: SpatialPin = {
        id: `pin-${Date.now()}`,
        author: 'Conor',
        text,
        pin_type: normalized,
        position: [...selfWearer.position],
        landmark: 'Current Gaze',
        age_label: 'just now',
      };
      setPins(prev => [newPin, ...prev]);
      try {
        await fetch(`${API_BASE}/api/pin`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...newPin, created_at: Date.now() / 1000}),
        });
      } catch {
        /* local state updated for offline mode */
      }
      return {pinned: true, pin: newPin};
    },
    [selfWearer.position],
  );

  const runCtrlZAction = useCallback(async () => {
    setMode('ctrlz');
    try {
      const res = await fetch(`${API_BASE}/api/ctrl-z`, {method: 'POST'});
      const data = await res.json();
      if (Array.isArray(data.undo_steps)) setUndoSteps(data.undo_steps);
      return data;
    } catch {
      return {undo_steps: undoSteps};
    }
  }, [undoSteps]);

  const cycleTargetOrShowCtrlF = useCallback(() => {
    if (mode !== 'ctrlf') {
      setMode('ctrlf');
      return;
    }
    const nextIdx = (itemIndex + 1) % Math.max(1, objects.length);
    setItemIndex(nextIdx);
    const nextObj = objects[nextIdx];
    if (nextObj) {
      void findObjectAction(nextObj.label);
    }
  }, [mode, itemIndex, objects, findObjectAction]);

  const showPinsOrDropPin = useCallback(() => {
    if (mode !== 'pins') {
      setMode('pins');
      return;
    }
    void pinMemoryAction('Caution: wet cable & power strip on floor', 'warning');
  }, [mode, pinMemoryAction]);

  const showCrewOrStepWalk = useCallback(() => {
    if (mode !== 'crew') {
      setMode('crew');
      return;
    }
    stepRef.current += 1;
    const angle = (stepRef.current * 40) % 360;
    const rad = (angle * Math.PI) / 180;
    const nextPos = [
      Math.round(Math.cos(rad) * 2.1 * 10) / 10,
      1.6,
      Math.round(Math.sin(rad) * 2.1 * 10) / 10,
    ];
    setWearers(prev =>
      prev.map(w => (w.wearer_id === 'wearer-b' ? {...w, position: nextPos, yaw: angle} : w)),
    );
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'pose',
          wearer_id: 'wearer-b',
          name: 'Conor (You)',
          position: nextPos,
          yaw: angle,
        }),
      );
    }
  }, [mode]);

  return {
    mode,
    objects,
    pins,
    wearers,
    diff,
    undoSteps,
    selfWearer,
    activeTarget,
    localNav,
    searchMessage,
    statusNote,
    findObjectAction,
    pinMemoryAction,
    runCtrlZAction,
    cycleTargetOrShowCtrlF,
    showPinsOrDropPin,
    showCrewOrStepWalk,
  };
}
