import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {HudMode} from './webmcp';

const WS_PROTO =
  typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const API_BASE = '';
const WS_URL =
  typeof window !== 'undefined' && window.location.host
    ? `${WS_PROTO}//${window.location.host}/ws/stream`
    : 'ws://127.0.0.1:4173/ws/stream';

interface SpeechRecInstance {
  lang: string;
  continuous?: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((e: {results: ArrayLike<ArrayLike<{transcript: string}> & {isFinal?: boolean}>}) => void) | null;
  onerror: ((e: {error?: string}) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop?: () => void;
}

type SpeechRecCtor = new () => SpeechRecInstance;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecCtor;
    webkitSpeechRecognition?: SpeechRecCtor;
  }
}

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

export interface HudCalibration {
  size: number;
  horizontal: number;
  vertical: number;
  roll: number;
}

export interface HudOverlayItem {
  id: string;
  label: string;
  confidence: number;
  rawBox: [number, number, number, number];
  frameSize: [number, number];
  box: [number, number, number, number];
  labelBounds: {x: number; y: number; w: number; h: number};
  noteBounds: {x: number; y: number; w: number; h: number};
  noteLines: string[];
  note?: string;
}

export interface HeadCursorState {
  visible: boolean;
  x: number;
  y: number;
  selectedId: string | null;
}

export interface LiveSpeechState {
  active: boolean;
  finishing: boolean;
  noteId: string | null;
  text: string;
  error: string | null;
}

interface RawYoloDetection {
  object_id: string;
  label: string;
  confidence: number;
  bbox_xyxy: [number, number, number, number];
  note?: string;
}

const DEFAULT_CALIBRATION: HudCalibration = {
  size: 0.8522121,
  horizontal: -512.44965,
  vertical: -365.0765,
  roll: 0.0,
};

function wrapNoteLines(text: string, maxChars = 26): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return ['add note?'];
  const lines: string[] = [];
  let cur = '';
  for (const w of words) {
    if (!cur) {
      cur = w.slice(0, maxChars);
    } else if (cur.length + 1 + w.length <= maxChars) {
      cur += ` ${w}`;
    } else {
      lines.push(cur);
      cur = w.slice(0, maxChars);
    }
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}

export interface Wearer {
  wearer_id: string;
  name: string;
  position: number[];
  yaw: number;
  pitch?: number;
  roll?: number;
  compass_abs?: number | null;
  steps?: number;
  sensor_active?: boolean;
}

export interface WorldDiff {
  added: SpatialObject[];
  removed: SpatialObject[];
  moved: Array<{label: string; distance_m: number}>;
}

export interface ImuSample {
  t_ms: number;
  compass_deg: number | null;
  yaw_deg: number;
  pitch_deg: number;
  roll_deg: number;
  absolute: boolean;
  gyro_dps: [number, number, number];
  accel_g: [number, number, number];
  accel_lin: [number, number, number];
  step_count: number;
  lat?: number;
  lon?: number;
}

export interface ImuTelemetry {
  sensorStatus: 'idle' | 'requesting' | 'active' | 'denied' | 'unsupported';
  isRecording: boolean;
  headingDeg: number | null;
  cardinal: string;
  pitchDeg: number;
  rollDeg: number;
  gyroYawDeg: number;
  steps: number;
  distanceM: number;
  sampleCount: number;
  hasAbsoluteCompass: boolean;
  hasGps: boolean;
  lastSessionId: string | null;
  lastSyncNote: string;
}

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;

export function degToCardinal(deg: number | null): string {
  if (deg === null || !Number.isFinite(deg)) return '--';
  const norm = ((deg % 360) + 360) % 360;
  return CARDINALS[Math.round(norm / 45) % 8];
}

function wrapDeg(deg: number): number {
  return ((((deg + 180) % 360) + 360) % 360) - 180;
}

function norm360(deg: number): number {
  return ((deg % 360) + 360) % 360;
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
    angle,
    turn,
    dist,
    text: `${target.label} seen by ${target.seen_by}. ${turn === 'straight ahead' ? 'Look straight ahead.' : `Turn ${turn}.`}`,
  };
}

function buildImuSessionPayload(
  startedAt: number,
  endedAt: number,
  stepCount: number,
  samples: ImuSample[],
) {
  return {
    session_id: `imu_${Math.round(startedAt)}`,
    device: 'meta-ray-ban-display',
    started_at_ms: startedAt,
    ended_at_ms: endedAt,
    step_count: stepCount,
    samples,
  };
}

export function useWorldGit() {
  const [mode, setMode] = useState<HudMode>('ctrlf');
  const [objects, setObjects] = useState<SpatialObject[]>(FALLBACK_OBJECTS);
  const [pins, setPins] = useState<SpatialPin[]>(FALLBACK_PINS);
  const [wearers, setWearers] = useState<Wearer[]>([
    {wearer_id: 'wearer-b', name: 'Conor (You)', position: [1.5, 1.6, 2.5], yaw: 0, pitch: 0, roll: 0, steps: 0},
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
  const [isListening, setIsListening] = useState(false);
  const [statusNote, setStatusNote] = useState('Hub syncing...');
  const [imu, setImu] = useState<ImuTelemetry>({
    sensorStatus: 'idle',
    isRecording: false,
    headingDeg: null,
    cardinal: '--',
    pitchDeg: 0,
    rollDeg: 0,
    gyroYawDeg: 0,
    steps: 0,
    distanceM: 0,
    sampleCount: 0,
    hasAbsoluteCompass: false,
    hasGps: false,
    lastSessionId: null,
    lastSyncNote: 'Tap Scan to arm 9-axis sensors & record walk',
  });
  const [cursorPos, setCursorPos] = useState<{xPct: number; yPct: number}>({xPct: 50, yPct: 50});
  const [pinDraft, setPinDraft] = useState('');
  const [pinStatus, setPinStatus] = useState('');
  const [calibration, setCalibration] = useState<HudCalibration>(DEFAULT_CALIBRATION);
  const [rawDetections, setRawDetections] = useState<RawYoloDetection[]>([
    {
      object_id: 'ee5906dc8f2544e189bfa659871e7504',
      label: 'bottle',
      confidence: 0.476,
      bbox_xyxy: [392.98, 574.17, 438.85, 684.95],
    },
    {
      object_id: '19dea338a6854cfeb795d39c3ff105ec',
      label: 'desk',
      confidence: 0.268,
      bbox_xyxy: [388.71, 346.24, 536.7, 607.09],
    },
    {
      object_id: '9341b735850942de96757e3ae7335969',
      label: 'bed',
      confidence: 0.494,
      bbox_xyxy: [447.62, 361.12, 540.0, 959.93],
    },
  ]);
  const [frameSizePx, setFrameSizePx] = useState<[number, number]>([540, 960]);
  const [savedNotesMap, setSavedNotesMap] = useState<Record<string, string>>({});
  const [hudCursor, setHudCursor] = useState<HeadCursorState>({
    visible: true,
    x: 0.55,
    y: 0.72,
    selectedId: 'ee5906dc8f2544e189bfa659871e7504',
  });
  const [liveSpeech, setLiveSpeech] = useState<LiveSpeechState>({
    active: false,
    finishing: false,
    noteId: null,
    text: '',
    error: null,
  });
  const speechStopRef = useRef<(() => void) | null>(null);
  const lastPointerMoveMsRef = useRef(0);
  const headOriginRef = useRef<{yaw: number; pitch: number} | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const stepRef = useRef(0);

  // Live 9-axis sensor refs (high-rate without re-render thrash)
  const sensorActiveRef = useRef(false);
  const isRecordingRef = useRef(false);
  const hasAbsoluteOrientRef = useRef(false);
  const geoWatchIdRef = useRef<number | null>(null);
  const coordsRef = useRef<{lat: number; lon: number} | null>(null);

  const gVecRef = useRef<[number, number, number]>([0, 9.81, 0]);
  const gyroYawRef = useRef(0);
  const fusedYawRef = useRef(0);
  const headingRef = useRef<number | null>(null);
  const pitchRef = useRef(0);
  const rollRef = useRef(0);
  const posRef = useRef<[number, number, number]>([1.5, 1.6, 2.5]);

  const lastMotionMsRef = useRef(0);
  const lastStepMsRef = useRef(0);
  const lastSampleMsRef = useRef(0);
  const lastUiPushMsRef = useRef(0);
  const sessionStartMsRef = useRef(0);
  const samplesRef = useRef<ImuSample[]>([]);
  const lastFlushCountRef = useRef(0);
  const lastFlushMsRef = useRef(0);

  const motionHandlerRef = useRef<((e: DeviceMotionEvent) => void) | null>(null);
  const absOrientHandlerRef = useRef<((e: DeviceOrientationEvent) => void) | null>(null);
  const relOrientHandlerRef = useRef<((e: DeviceOrientationEvent) => void) | null>(null);

  const selfWearer = useMemo(
    () => wearers.find(w => w.wearer_id === 'wearer-b') ?? wearers[0],
    [wearers],
  );

  const activeTarget = objects[itemIndex % Math.max(1, objects.length)] ?? FALLBACK_OBJECTS[0];
  const localNav = useMemo(
    () => computeLocalDirection(selfWearer.position, selfWearer.yaw, activeTarget),
    [selfWearer, activeTarget],
  );

  const mergeIncomingWearers = useCallback((incoming: Wearer[]) => {
    setWearers(prev => {
      if (!sensorActiveRef.current) return incoming;
      const localSelf = prev.find(w => w.wearer_id === 'wearer-b');
      if (!localSelf) return incoming;
      return incoming.map(w => (w.wearer_id === 'wearer-b' ? localSelf : w));
    });
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    fetch(`${API_BASE}/api/state`, {signal: controller.signal})
      .then(r => r.json())
      .then(data => {
        if (!active) return;
        if (Array.isArray(data.objects) && data.objects.length > 0) setObjects(data.objects);
        if (Array.isArray(data.pins) && data.pins.length > 0) setPins(data.pins);
        if (Array.isArray(data.wearers) && data.wearers.length > 0) mergeIncomingWearers(data.wearers);
        if (data.diff) setDiff(data.diff);
        setStatusNote('Connected to WorldGit Hub');

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
                if (Array.isArray(msg.wearers) && msg.wearers.length > 0) mergeIncomingWearers(msg.wearers);
                if (msg.diff) setDiff(msg.diff);
              } else if (msg.type === 'wearers_update' && Array.isArray(msg.wearers)) {
                mergeIncomingWearers(msg.wearers);
              }
            } catch {
              /* ignore malformed frame */
            }
          };
          ws.onerror = () => {
            if (active) setStatusNote('Offline cache mode');
          };
        } catch {
          if (active) setStatusNote('Offline cache mode');
        }
      })
      .catch(() => {
        if (active) setStatusNote('Offline cache mode');
      });

    return () => {
      active = false;
      controller.abort();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [mergeIncomingWearers]);

  const pushTelemetryUi = useCallback((nowMs: number, force = false) => {
    if (!force && nowMs - lastUiPushMsRef.current < 85) return;
    lastUiPushMsRef.current = nowMs;

    const hDeg = headingRef.current !== null ? Math.round(headingRef.current * 10) / 10 : null;
    const yawDeg = Math.round(norm360(fusedYawRef.current) * 10) / 10;
    const pitchDeg = Math.round(pitchRef.current * 10) / 10;
    const rollDeg = Math.round(rollRef.current * 10) / 10;
    const steps = stepRef.current;
    const nextPos: [number, number, number] = [
      Math.round(posRef.current[0] * 100) / 100,
      1.6,
      Math.round(posRef.current[2] * 100) / 100,
    ];

    setWearers(prev =>
      prev.map(w =>
        w.wearer_id === 'wearer-b'
          ? {
              ...w,
              position: nextPos,
              yaw: yawDeg,
              pitch: pitchDeg,
              roll: rollDeg,
              compass_abs: hDeg,
              steps,
              sensor_active: sensorActiveRef.current,
            }
          : w,
      ),
    );

    setImu(prev => ({
      ...prev,
      sensorStatus: sensorActiveRef.current ? 'active' : prev.sensorStatus,
      isRecording: isRecordingRef.current,
      headingDeg: hDeg,
      cardinal: degToCardinal(hDeg),
      pitchDeg,
      rollDeg,
      gyroYawDeg: Math.round(gyroYawRef.current * 10) / 10,
      steps,
      distanceM: Math.round(steps * 0.72 * 10) / 10,
      sampleCount: samplesRef.current.length,
      hasAbsoluteCompass: hasAbsoluteOrientRef.current,
      hasGps: coordsRef.current !== null,
    }));

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'pose',
          wearer_id: 'wearer-b',
          name: 'Conor (You)',
          position: nextPos,
          yaw: yawDeg,
          pitch: pitchDeg,
          roll: rollDeg,
          compass_abs: hDeg,
          steps,
          sensor_active: sensorActiveRef.current,
        }),
      );
    }
  }, []);

  const detachSensorListeners = useCallback(() => {
    sensorActiveRef.current = false;
    if (motionHandlerRef.current) {
      window.removeEventListener('devicemotion', motionHandlerRef.current);
      motionHandlerRef.current = null;
    }
    if (absOrientHandlerRef.current) {
      window.removeEventListener(
        'deviceorientationabsolute' as keyof WindowEventMap,
        absOrientHandlerRef.current as EventListener,
      );
      absOrientHandlerRef.current = null;
    }
    if (relOrientHandlerRef.current) {
      window.removeEventListener('deviceorientation', relOrientHandlerRef.current);
      relOrientHandlerRef.current = null;
    }
    if (geoWatchIdRef.current !== null && 'geolocation' in navigator) {
      navigator.geolocation.clearWatch(geoWatchIdRef.current);
      geoWatchIdRef.current = null;
    }
  }, []);

  const flushImuCheckpoint = useCallback((useBeacon = false) => {
    const samples = samplesRef.current;
    if (!isRecordingRef.current || samples.length === 0) return;
    const now = Date.now();
    if (samples.length === lastFlushCountRef.current && now - lastFlushMsRef.current < 60000) {
      return;
    }
    lastFlushCountRef.current = samples.length;
    lastFlushMsRef.current = now;
    const body = JSON.stringify(
      buildImuSessionPayload(sessionStartMsRef.current, now, stepRef.current, samples),
    );
    const url = `${API_BASE}/api/imu-sessions`;
    if (useBeacon && typeof navigator.sendBeacon === 'function') {
      const sent = navigator.sendBeacon(url, new Blob([body], {type: 'application/json'}));
      if (sent) return;
    }
    void fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body,
      keepalive: true,
    }).catch(() => undefined);
  }, []);

  const stopSensorsAndUpload = useCallback(async () => {
    const wasRecording = isRecordingRef.current;
    const samples = samplesRef.current;
    const startedAt = sessionStartMsRef.current;
    const endedAt = Date.now();
    const totalSteps = stepRef.current;

    isRecordingRef.current = false;
    detachSensorListeners();

    setImu(prev => ({
      ...prev,
      sensorStatus: 'idle',
      isRecording: false,
      steps: totalSteps,
      distanceM: Math.round(totalSteps * 0.72 * 10) / 10,
      gyroYawDeg: Math.round(gyroYawRef.current * 10) / 10,
      sampleCount: samples.length,
      lastSyncNote:
        wasRecording && samples.length > 0
          ? `Uploading ${samples.length} IMU samples...`
          : 'Sensors paused',
    }));
    setWearers(prev =>
      prev.map(w => (w.wearer_id === 'wearer-b' ? {...w, sensor_active: false} : w)),
    );

    if (!wasRecording || samples.length === 0) {
      return {uploaded: false, sample_count: 0};
    }

    const payload = buildImuSessionPayload(startedAt, endedAt, totalSteps, samples);
    const sessionId = payload.session_id;
    try {
      const res = await fetch(`${API_BASE}/api/imu-sessions`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data?.ok) {
        lastFlushCountRef.current = samples.length;
        lastFlushMsRef.current = Date.now();
        setImu(prev => ({
          ...prev,
          lastSessionId: sessionId,
          lastSyncNote: `Synced ${samples.length} IMU pts (${totalSteps} steps) • auto-pairs with .mp4`,
        }));
        return {uploaded: true, session_id: sessionId, sample_count: samples.length, step_count: totalSteps};
      }
    } catch {
      /* offline or unreachable hub */
    }

    setImu(prev => ({
      ...prev,
      lastSessionId: sessionId,
      lastSyncNote: `Saved ${samples.length} pts locally (Hub unreachable)`,
    }));
    return {uploaded: false, session_id: sessionId, sample_count: samples.length};
  }, [detachSensorListeners]);

  const startSensorsAndRecord = useCallback(
    async (recordWalk = true, resume = false) => {
      detachSensorListeners();
      setImu(prev => ({...prev, sensorStatus: 'requesting'}));

      if (!resume) {
        try {
          const motionCtor: object | undefined =
            typeof DeviceMotionEvent === 'function' ? DeviceMotionEvent : undefined;
          if (motionCtor) {
            const fn: unknown = Reflect.get(motionCtor, 'requestPermission');
            if (typeof fn === 'function') {
              const perm: unknown = await Reflect.apply(fn, motionCtor, []);
              if (perm !== 'granted') {
                setImu(prev => ({
                  ...prev,
                  sensorStatus: 'denied',
                  lastSyncNote: 'Motion permission denied',
                }));
                return {started: false, error: 'Motion permission denied'};
              }
            }
          }
          const orientCtor: object | undefined =
            typeof DeviceOrientationEvent === 'function' ? DeviceOrientationEvent : undefined;
          if (orientCtor) {
            const fn: unknown = Reflect.get(orientCtor, 'requestPermission');
            if (typeof fn === 'function') {
              await Promise.resolve(Reflect.apply(fn, orientCtor, [])).catch(() => 'denied');
            }
          }
        } catch {
          setImu(prev => ({
            ...prev,
            sensorStatus: 'denied',
            lastSyncNote: 'Sensor permission failed',
          }));
          return {started: false, error: 'Permission failed'};
        }
      }

      const now = Date.now();
      sensorActiveRef.current = true;
      isRecordingRef.current = recordWalk;
      hasAbsoluteOrientRef.current = false;
      lastMotionMsRef.current = now;
      lastStepMsRef.current = 0;
      lastSampleMsRef.current = 0;
      lastUiPushMsRef.current = 0;
      if (recordWalk && !resume) {
        sessionStartMsRef.current = now;
        samplesRef.current = [];
        stepRef.current = 0;
        gyroYawRef.current = 0;
        lastFlushCountRef.current = 0;
        lastFlushMsRef.current = 0;
      }

      const handleOrient = (ev: DeviceOrientationEvent, fromAbsolute: boolean) => {
        if (!sensorActiveRef.current) return;
        const wk = (ev as DeviceOrientationEvent & {webkitCompassHeading?: number}).webkitCompassHeading;
        const isAbs = fromAbsolute || ev.absolute === true || typeof wk === 'number';
        if (fromAbsolute && typeof ev.alpha === 'number' && Number.isFinite(ev.alpha)) {
          hasAbsoluteOrientRef.current = true;
          if (relOrientHandlerRef.current) {
            window.removeEventListener('deviceorientation', relOrientHandlerRef.current);
            relOrientHandlerRef.current = null;
          }
        } else if (isAbs) {
          hasAbsoluteOrientRef.current = true;
        }

        let heading: number | null = null;
        if (typeof wk === 'number' && Number.isFinite(wk)) {
          heading = norm360(wk);
        } else if (typeof ev.alpha === 'number' && Number.isFinite(ev.alpha)) {
          heading = norm360(360 - ev.alpha);
        }

        if (heading !== null) {
          if (headingRef.current === null) {
            headingRef.current = heading;
            fusedYawRef.current = heading;
          } else {
            const err = wrapDeg(heading - fusedYawRef.current);
            const gain = Math.abs(err) <= 18 ? 0.15 : 0.45;
            fusedYawRef.current = norm360(fusedYawRef.current + gain * err);
            headingRef.current = heading;
          }
        }
        if (typeof ev.beta === 'number' && Number.isFinite(ev.beta)) {
          pitchRef.current = ev.beta;
        }
        if (typeof ev.gamma === 'number' && Number.isFinite(ev.gamma)) {
          rollRef.current = ev.gamma;
        }
        pushTelemetryUi(Date.now());
      };

      const onAbsOrient = (e: Event) => handleOrient(e as DeviceOrientationEvent, true);
      const onRelOrient = (e: DeviceOrientationEvent) => handleOrient(e, false);
      absOrientHandlerRef.current = onAbsOrient as (e: DeviceOrientationEvent) => void;
      relOrientHandlerRef.current = onRelOrient;

      window.addEventListener('deviceorientationabsolute' as keyof WindowEventMap, onAbsOrient);
      window.addEventListener('deviceorientation', onRelOrient);

      const onMotion = (ev: DeviceMotionEvent) => {
        if (!sensorActiveRef.current) return;
        const tNow = Date.now();
        const dt = Math.min(0.2, Math.max(0.001, (tNow - (lastMotionMsRef.current || tNow)) / 1000));
        lastMotionMsRef.current = tNow;

        const rr = ev.rotationRate;
        const gAlpha = Number(rr?.alpha ?? 0) || 0;
        const gBeta = Number(rr?.beta ?? 0) || 0;
        const gGamma = Number(rr?.gamma ?? 0) || 0;

        const ag = ev.accelerationIncludingGravity;
        const axg = Number(ag?.x ?? gVecRef.current[0]) || 0;
        const ayg = Number(ag?.y ?? gVecRef.current[1]) || 0;
        const azg = Number(ag?.z ?? gVecRef.current[2]) || 0;

        // Low-pass gravity vector
        gVecRef.current = [
          0.82 * gVecRef.current[0] + 0.18 * axg,
          0.82 * gVecRef.current[1] + 0.18 * ayg,
          0.82 * gVecRef.current[2] + 0.18 * azg,
        ];
        const gn = Math.hypot(gVecRef.current[0], gVecRef.current[1], gVecRef.current[2]);

        let yawRateEarth = gAlpha;
        if (gn > 3.0) {
          const gx = gVecRef.current[0] / gn;
          const gy = gVecRef.current[1] / gn;
          const gz = gVecRef.current[2] / gn;
          // Project 3-axis gyro [X=beta, Y=gamma, Z=alpha] onto gravity axis [gx, gy, gz] for Earth-vertical yaw rate
          yawRateEarth = gBeta * gx + gGamma * gy + gAlpha * gz;
          if (!hasAbsoluteOrientRef.current) {
            pitchRef.current = (Math.atan2(-gy, gz) * 180) / Math.PI;
            rollRef.current = (Math.atan2(gx, Math.hypot(gy, gz)) * 180) / Math.PI;
          }
        }

        if (Math.abs(yawRateEarth) >= 0.35) {
          const deltaDeg = -yawRateEarth * dt;
          gyroYawRef.current += deltaDeg;
          fusedYawRef.current = norm360(fusedYawRef.current + deltaDeg);
        }

        const al = ev.acceleration;
        const axl = Number(al?.x ?? 0) || 0;
        const ayl = Number(al?.y ?? 0) || 0;
        const azl = Number(al?.z ?? 0) || 0;
        const linMag =
          al && (al.x !== null || al.y !== null || al.z !== null)
            ? Math.hypot(axl, ayl, azl)
            : Math.abs(Math.hypot(axg, ayg, azg) - 9.81);

        let stepped = false;
        if (linMag > 1.35 && tNow - lastStepMsRef.current > 340) {
          stepped = true;
          lastStepMsRef.current = tNow;
          stepRef.current += 1;
          const rad = (fusedYawRef.current * Math.PI) / 180;
          const stepLen = 0.72;
          posRef.current = [
            posRef.current[0] + Math.sin(rad) * stepLen,
            1.6,
            posRef.current[2] - Math.cos(rad) * stepLen,
          ];
        }

        if (isRecordingRef.current && tNow - lastSampleMsRef.current >= 38 && samplesRef.current.length < 18000) {
          lastSampleMsRef.current = tNow;
          const sample: ImuSample = {
            t_ms: tNow,
            compass_deg: headingRef.current !== null ? Math.round(headingRef.current * 100) / 100 : null,
            yaw_deg: Math.round(gyroYawRef.current * 100) / 100,
            pitch_deg: Math.round(pitchRef.current * 100) / 100,
            roll_deg: Math.round(rollRef.current * 100) / 100,
            absolute: hasAbsoluteOrientRef.current,
            gyro_dps: [
              Math.round(gBeta * 100) / 100,
              Math.round(gGamma * 100) / 100,
              Math.round(gAlpha * 100) / 100,
            ],
            accel_g: [
              Math.round(axg * 100) / 100,
              Math.round(ayg * 100) / 100,
              Math.round(azg * 100) / 100,
            ],
            accel_lin: [
              Math.round(axl * 100) / 100,
              Math.round(ayl * 100) / 100,
              Math.round(azl * 100) / 100,
            ],
            step_count: stepRef.current,
          };
          if (coordsRef.current) {
            sample.lat = coordsRef.current.lat;
            sample.lon = coordsRef.current.lon;
          }
          samplesRef.current.push(sample);
        }

        pushTelemetryUi(tNow, stepped);
      };

      motionHandlerRef.current = onMotion;
      window.addEventListener('devicemotion', onMotion);

      if ('geolocation' in navigator) {
        try {
          geoWatchIdRef.current = navigator.geolocation.watchPosition(
            pos => {
              coordsRef.current = {
                lat: Math.round(pos.coords.latitude * 1e6) / 1e6,
                lon: Math.round(pos.coords.longitude * 1e6) / 1e6,
              };
            },
            () => {
              /* optional GPS indoors */
            },
            {enableHighAccuracy: true, maximumAge: 5000, timeout: 10000},
          );
        } catch {
          /* ignore unsupported geolocation */
        }
      }

      setWearers(prev =>
        prev.map(w => (w.wearer_id === 'wearer-b' ? {...w, sensor_active: true} : w)),
      );
      setImu(prev => ({
        ...prev,
        sensorStatus: 'active',
        isRecording: recordWalk,
        steps: stepRef.current,
        distanceM: Math.round(stepRef.current * 0.72 * 10) / 10,
        sampleCount: samplesRef.current.length,
        lastSyncNote: recordWalk
          ? resume
            ? `Resumed 9-axis IMU (${samplesRef.current.length} pts kept)`
            : 'Recording 9-axis IMU — film hallway/room on glasses'
          : '9-axis live tracking active',
      }));
      pushTelemetryUi(now, true);
      return {started: true, recording: recordWalk, resumed: resume};
    },
    [detachSensorListeners, pushTelemetryUi],
  );

  // Auto-start live 9-axis compass & step-distance tracking when no permission gate blocks it
  useEffect(() => {
    const motionCtor: object | undefined =
      typeof DeviceMotionEvent === 'function' ? DeviceMotionEvent : undefined;
    const needsPrompt = motionCtor && typeof Reflect.get(motionCtor, 'requestPermission') === 'function';
    if (!needsPrompt && !sensorActiveRef.current) {
      void startSensorsAndRecord(false);
    }
  }, [startSensorsAndRecord]);

  // Pause listeners + checkpoint IMU if Meta OS dims/backgrounds HUD; resume active Scan on return
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) {
        if (isRecordingRef.current) {
          flushImuCheckpoint(false);
          detachSensorListeners();
          setWearers(prev =>
            prev.map(w => (w.wearer_id === 'wearer-b' ? {...w, sensor_active: false} : w)),
          );
          setImu(prev => ({
            ...prev,
            sensorStatus: 'idle',
            isRecording: true,
            steps: stepRef.current,
            distanceM: Math.round(stepRef.current * 0.72 * 10) / 10,
            sampleCount: samplesRef.current.length,
            lastSyncNote: `Checkpointed ${samplesRef.current.length} pts (paused while dimmed)`,
          }));
        } else if (sensorActiveRef.current) {
          detachSensorListeners();
          setWearers(prev =>
            prev.map(w => (w.wearer_id === 'wearer-b' ? {...w, sensor_active: false} : w)),
          );
          setImu(prev => ({
            ...prev,
            sensorStatus: 'idle',
            lastSyncNote: 'Sensors paused while dimmed',
          }));
        }
      } else if (isRecordingRef.current && !sensorActiveRef.current) {
        void startSensorsAndRecord(true, true);
      }
    };
    const onPageHide = () => {
      if (isRecordingRef.current) {
        flushImuCheckpoint(true);
      }
      detachSensorListeners();
    };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
      if (isRecordingRef.current) {
        flushImuCheckpoint(true);
      }
      detachSensorListeners();
    };
  }, [detachSensorListeners, flushImuCheckpoint, startSensorsAndRecord]);

  const toggleScanAction = useCallback(() => {
    setMode('scan');
    if (isRecordingRef.current) {
      void stopSensorsAndUpload();
      return;
    }
    void startSensorsAndRecord(true);
  }, [startSensorsAndRecord, stopSensorsAndUpload]);

  const speakOut = useCallback((text: string) => {
    try {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window && text) {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(text);
        utter.rate = 1.05;
        window.speechSynthesis.speak(utter);
      }
    } catch {
      /* TTS optional */
    }
  }, []);

  const findObjectAction = useCallback(
    async (query: string) => {
      setMode('ctrlf');
      if (!sensorActiveRef.current) {
        void startSensorsAndRecord(false);
      }
      const idx = objects.findIndex(o => o.label.toLowerCase().includes(query.toLowerCase()));
      if (idx >= 0) setItemIndex(idx);
      try {
        const res = await fetch(`${API_BASE}/api/ask`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            viewer_id: 'usr-conor',
            wearer_id: 'wearer-b',
            query,
            world_id: 'world_1',
          }),
        });
        const data = await res.json();
        if (data.object?.id) {
          const matchedIdx = objects.findIndex(
            o => o.id === data.object.id || o.label.toLowerCase() === String(data.object.label || '').toLowerCase(),
          );
          if (matchedIdx >= 0) {
            setItemIndex(matchedIdx);
          } else if (Array.isArray(data.object.position)) {
            const added: SpatialObject = {
              id: String(data.object.id),
              label: String(data.object.label || query),
              position: data.object.position,
              seen_by: String(data.object.seen_by || 'WorldGit'),
              timestamp: Number(data.object.timestamp) || Date.now() / 1000,
            };
            setObjects(prev => [added, ...prev]);
            setItemIndex(0);
          }
        }
        const msg = data.spoken_answer || data.directions || data.message || localNav.text;
        setSearchMessage(msg);
        speakOut(msg);
        return data;
      } catch {
        setSearchMessage(localNav.text);
        return {found: true, directions: localNav.text};
      }
    },
    [objects, localNav.text, speakOut],
  );

  // Poll Display Access calibration (/api/calibration), YOLO detections (/api/detections), and persistent notes (/api/notes)
  useEffect(() => {
    let active = true;
    const fetchCal = () => {
      fetch(`${API_BASE}/api/calibration`)
        .then(r => (r.ok ? r.json() : null))
        .then(data => {
          if (!active || !data || typeof data.size !== 'number') return;
          setCalibration({
            size: Number(data.size),
            horizontal: Number(data.horizontal || 0),
            vertical: Number(data.vertical || 0),
            roll: Number(data.roll || 0),
          });
        })
        .catch(() => {});
    };

    const fetchDetAndNotes = () => {
      fetch(`${API_BASE}/api/detections`)
        .then(r => (r.ok ? r.json() : null))
        .then(data => {
          if (!active || !data) return;
          if (Array.isArray(data.frame_size_px) && data.frame_size_px.length === 2) {
            setFrameSizePx([Number(data.frame_size_px[0]) || 540, Number(data.frame_size_px[1]) || 960]);
          }
          if (Array.isArray(data.detections) && data.detections.length > 0) {
            const parsed: RawYoloDetection[] = data.detections
              .filter((d: Record<string, unknown>) => Array.isArray(d.bbox_xyxy) && d.bbox_xyxy.length === 4)
              .map((d: Record<string, unknown>, idx: number) => ({
                object_id: String(d.object_id || `det-${idx}`),
                label: String(d.label || 'object'),
                confidence: Number(d.confidence || 0.8),
                bbox_xyxy: [
                  Number((d.bbox_xyxy as number[])[0]),
                  Number((d.bbox_xyxy as number[])[1]),
                  Number((d.bbox_xyxy as number[])[2]),
                  Number((d.bbox_xyxy as number[])[3]),
                ],
                note: typeof d.note === 'string' && d.note.trim() ? d.note.trim() : undefined,
              }));
            if (parsed.length > 0) {
              setRawDetections(parsed);
            }
          }
        })
        .catch(() => {});

      fetch(`${API_BASE}/api/notes`)
        .then(r => (r.ok ? r.json() : null))
        .then(data => {
          if (!active || !data || !Array.isArray(data.notes)) return;
          const nextMap: Record<string, string> = {};
          for (const n of data.notes) {
            const text = String(n?.text || '').trim();
            if (!text) continue;
            if (n.object_id) nextMap[String(n.object_id)] = text;
            if (n.label) nextMap[`label:${String(n.label).toLowerCase()}`] = text;
          }
          if (Object.keys(nextMap).length > 0) {
            setSavedNotesMap(prev => ({...prev, ...nextMap}));
          }
        })
        .catch(() => {});
    };

    fetchCal();
    fetchDetAndNotes();
    const detTimer = window.setInterval(fetchDetAndNotes, 500);
    const calTimer = window.setInterval(fetchCal, 4000);
    return () => {
      active = false;
      window.clearInterval(detTimer);
      window.clearInterval(calTimer);
    };
  }, []);

  // Live camera scan in Pins mode: capture frames via getUserMedia and POST to /api/frame for YOLO-World
  useEffect(() => {
    if (mode !== 'pins') return;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) return;
    let cancelled = false;
    let stream: MediaStream | null = null;
    let timerId: number | null = null;
    let inFlight = false;

    const video = document.createElement('video');
    video.playsInline = true;
    video.muted = true;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');

    navigator.mediaDevices
      .enumerateDevices()
      .then(devices => {
        const videoInputs = devices.filter(d => d.kind === 'videoinput');
        const glassesCam =
          videoInputs.find(d => /ray-?ban|meta|glasses|display|wearable|external/i.test(d.label)) ||
          videoInputs.find(d => d.label && !/back|rear|environment|wide|telephoto|macro/i.test(d.label));
        const videoConstraint: MediaTrackConstraints = glassesCam
          ? {deviceId: {exact: glassesCam.deviceId}, width: {ideal: 960}, height: {ideal: 540}}
          : {width: {ideal: 960}, height: {ideal: 540}};
        return navigator.mediaDevices.getUserMedia({
          video: videoConstraint,
          audio: false,
        });
      })
      .catch(() =>
        navigator.mediaDevices.getUserMedia({
          video: {width: {ideal: 960}, height: {ideal: 540}},
          audio: false,
        }),
      )
      .then(s => {
        if (cancelled) {
          s.getTracks().forEach(t => t.stop());
          return;
        }
        stream = s;
        video.srcObject = s;
        void video.play().catch(() => {});

        timerId = window.setInterval(() => {
          if (cancelled || inFlight || !ctx || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
            return;
          }
          const targetW = Math.min(960, video.videoWidth);
          const targetH = Math.round((video.videoHeight / video.videoWidth) * targetW);
          if (canvas.width !== targetW || canvas.height !== targetH) {
            canvas.width = targetW;
            canvas.height = targetH;
          }
          ctx.drawImage(video, 0, 0, targetW, targetH);
          inFlight = true;
          canvas.toBlob(
            async blob => {
              try {
                if (!blob || cancelled) return;
                const res = await fetch(`${API_BASE}/api/frame`, {
                  method: 'POST',
                  headers: {
                    'Content-Type': 'image/jpeg',
                    'X-Tracking-Mode': 'phone',
                  },
                  body: blob,
                });
                if (!res.ok || cancelled) return;
                const data = await res.json();
                if (Array.isArray(data?.frame_size_px) && data.frame_size_px.length >= 2) {
                  setFrameSizePx([Number(data.frame_size_px[0]) || 960, Number(data.frame_size_px[1]) || 540]);
                }
                if (Array.isArray(data?.detections)) {
                  const parsed: RawYoloDetection[] = data.detections
                    .filter((d: Record<string, unknown>) => Array.isArray(d.bbox_xyxy) && d.bbox_xyxy.length >= 4)
                    .map((d: Record<string, unknown>, idx: number) => ({
                      object_id: String(d.object_id || `${d.label || 'obj'}_${idx}`),
                      label: String(d.label || 'object'),
                      confidence: Number(d.confidence ?? 0.7),
                      bbox_xyxy: [
                        Number((d.bbox_xyxy as number[])[0]),
                        Number((d.bbox_xyxy as number[])[1]),
                        Number((d.bbox_xyxy as number[])[2]),
                        Number((d.bbox_xyxy as number[])[3]),
                      ],
                      note: typeof d.note === 'string' && d.note.trim() ? d.note.trim() : undefined,
                    }));
                  setRawDetections(parsed);
                }
              } catch {
                // Ignore transient frame errors; polling /api/detections remains active
              } finally {
                inFlight = false;
              }
            },
            'image/jpeg',
            0.72,
          );
        }, 280);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (timerId !== null) window.clearInterval(timerId);
      if (stream) stream.getTracks().forEach(t => t.stop());
      video.srcObject = null;
    };
  }, [mode]);

  // HeadCursorController: update cursor from head orientation when pointer hasn't moved recently
  useEffect(() => {
    if (mode !== 'pins' || !sensorActiveRef.current) return;
    if (Date.now() - lastPointerMoveMsRef.current < 1200) return;
    const yaw = selfWearer.yaw;
    const pitch = selfWearer.pitch ?? 0;
    if (!headOriginRef.current) {
      headOriginRef.current = {yaw, pitch};
      return;
    }
    const dYaw = ((yaw - headOriginRef.current.yaw + 540) % 360) - 180;
    const dPitch = pitch - headOriginRef.current.pitch;
    const nx = Math.max(0.04, Math.min(0.96, 0.5 + dYaw / 60));
    const ny = Math.max(0.04, Math.min(0.96, 0.5 - dPitch / 45));
    setHudCursor(prev => ({...prev, visible: true, x: nx, y: ny}));
  }, [mode, selfWearer.yaw, selfWearer.pitch]);

  // Exact HudProjection.kt + YoloOverlay.kt projection into 600x600 glasses optical display
  const hudOverlayItems = useMemo<HudOverlayItem[]>(() => {
    const [width, height] = frameSizePx;
    const cameraFocal = Math.hypot(width, height) / (2 * Math.tan((90 * Math.PI) / 180 / 2));
    const displayFocal = Math.hypot(600, 600) / (2 * Math.tan((20 * Math.PI) / 180 / 2));
    const clampedSize = Math.max(0.2, Math.min(3.0, calibration.size));
    const scale = (displayFocal / cameraFocal) * clampedSize;
    const rad = (calibration.roll * Math.PI) / 180;
    const c = Math.cos(rad);
    const s = Math.sin(rad);

    const projectPoint = (x: number, y: number): [number, number] => {
      const dx = (x - width / 2) * scale;
      const dy = (y - height / 2) * scale;
      return [
        300 + calibration.horizontal + c * dx - s * dy,
        300 + calibration.vertical + s * dx + c * dy,
      ];
    };

    const items: HudOverlayItem[] = [];
    for (const det of rawDetections) {
      const b = det.bbox_xyxy;
      const corners = [
        projectPoint(b[0], b[1]),
        projectPoint(b[2], b[1]),
        projectPoint(b[2], b[3]),
        projectPoint(b[0], b[3]),
      ];
      const box: [number, number, number, number] = [
        Math.min(...corners.map(p => p[0])),
        Math.min(...corners.map(p => p[1])),
        Math.max(...corners.map(p => p[0])),
        Math.max(...corners.map(p => p[1])),
      ];
      if (box[2] <= 0 || box[3] <= 0 || box[0] >= 600 || box[1] >= 600) {
        continue;
      }

      const visX1 = Math.max(6, Math.min(594, box[0]));
      const visY1 = Math.max(6, Math.min(594, box[1]));
      const visX2 = Math.max(visX1 + 20, Math.min(594, box[2]));
      const visY2 = Math.max(visY1 + 20, Math.min(594, box[3]));

      const labelText = det.label.slice(0, 28);
      const lw = Math.min(580, Math.max(58, labelText.length * 10 + 18));
      const lh = 28;
      const cx = (visX1 + visX2) / 2;
      const cy = (visY1 + visY2) / 2;
      const lx = Math.max(8, Math.min(592 - lw, cx - lw / 2));
      const ly = Math.max(8, Math.min(520 - lh, cy - lh / 2));

      const savedNote =
        savedNotesMap[det.object_id] ||
        savedNotesMap[`label:${det.label.toLowerCase()}`] ||
        det.note;

      const notePrompt =
        liveSpeech.active && liveSpeech.noteId === det.object_id
          ? liveSpeech.finishing
            ? 'Transcribing...'
            : 'stop recording'
          : savedNote || 'add note?';

      const lines = wrapNoteLines(notePrompt, 26);
      const maxLineChars = Math.max(...lines.map(l => l.length), 9);
      const nw = Math.min(584, Math.max(lw, maxLineChars * 9.5 + 18));
      const nh = lines.length * 22 + 10;
      const nx = Math.max(8, Math.min(592 - nw, lx));
      const ny = Math.min(592 - nh, ly + lh + 4);

      items.push({
        id: det.object_id,
        label: labelText,
        confidence: det.confidence,
        rawBox: det.bbox_xyxy,
        frameSize: frameSizePx,
        box: [visX1, visY1, visX2, visY2],
        labelBounds: {x: lx, y: ly, w: lw, h: lh},
        noteBounds: {x: nx, y: ny, w: nw, h: nh},
        noteLines: lines,
        note: savedNote,
      });
    }
    return items;
  }, [rawDetections, frameSizePx, calibration, savedNotesMap, liveSpeech]);

  // Auto-select whichever object the glasses cursor is over (when not actively recording)
  useEffect(() => {
    if (mode !== 'pins' || liveSpeech.active) return;
    const px = hudCursor.x * 600;
    const py = hudCursor.y * 600;

    let hovered = hudOverlayItems.find(
      i =>
        (px >= i.labelBounds.x - 10 &&
          px <= i.labelBounds.x + i.labelBounds.w + 10 &&
          py >= i.labelBounds.y - 10 &&
          py <= i.labelBounds.y + i.labelBounds.h + 10) ||
        (px >= i.noteBounds.x - 10 &&
          px <= i.noteBounds.x + i.noteBounds.w + 10 &&
          py >= i.noteBounds.y - 10 &&
          py <= i.noteBounds.y + i.noteBounds.h + 10),
    );
    if (!hovered) {
      let bestArea = Infinity;
      for (const item of hudOverlayItems) {
        const [x1, y1, x2, y2] = item.box;
        if (px >= x1 - 6 && px <= x2 + 6 && py >= y1 - 6 && py <= y2 + 6) {
          const area = Math.max(1, (x2 - x1) * (y2 - y1));
          if (area < bestArea) {
            bestArea = area;
            hovered = item;
          }
        }
      }
    }

    const nextId = hovered?.id ?? null;
    setHudCursor(prev => (prev.selectedId === nextId ? prev : {...prev, selectedId: nextId}));
  }, [mode, hudCursor.x, hudCursor.y, hudOverlayItems, liveSpeech.active]);

  const projectedObjects = useMemo(() => {
    return objects.slice(0, 8).map((obj, idx) => {
      const dx = obj.position[0] - selfWearer.position[0];
      const dz = obj.position[2] - selfWearer.position[2];
      const rawBearing = (Math.atan2(dx, -dz) * 180) / Math.PI;
      const rel = ((rawBearing - selfWearer.yaw + 540) % 360) - 180;
      const xPct = Math.max(10, Math.min(90, 50 + (rel / 90) * 40));
      const dy = (obj.position[1] ?? 1.0) - 1.2;
      const yPct = Math.max(18, Math.min(82, 50 - dy * 28 + (idx % 2 === 0 ? -6 : 6)));
      return {object: obj, xPct, yPct};
    });
  }, [objects, selfWearer.position, selfWearer.yaw]);

  const aimedObject = useMemo(() => {
    const selectedOverlay = hudOverlayItems.find(i => i.id === hudCursor.selectedId);
    if (selectedOverlay) {
      const matched = objects.find(o => o.label.toLowerCase() === selectedOverlay.label.toLowerCase());
      if (matched) return matched;
    }
    if (projectedObjects.length === 0) return activeTarget;
    let best = projectedObjects[0];
    let bestDist = Infinity;
    for (const item of projectedObjects) {
      const d = Math.hypot(item.xPct - cursorPos.xPct, item.yPct - cursorPos.yPct);
      if (d < bestDist) {
        bestDist = d;
        best = item;
      }
    }
    return best.object;
  }, [hudOverlayItems, hudCursor.selectedId, objects, projectedObjects, cursorPos, activeTarget]);

  const captureVoiceTranscript = useCallback(
    async (
      promptMsg: string,
      onStatus: (msg: string) => void,
      onFinal: (transcript: string) => Promise<void>,
    ) => {
      if (isListening) return;
      const SpeechRec =
        typeof window !== 'undefined'
          ? window.SpeechRecognition || window.webkitSpeechRecognition
          : undefined;

      if (typeof SpeechRec === 'function') {
        try {
          setIsListening(true);
          onStatus(promptMsg);
          const rec = new SpeechRec();
          rec.lang = 'en-US';
          rec.interimResults = true;
          rec.maxAlternatives = 1;
          let handled = false;
          rec.onresult = ev => {
            const last = ev.results[ev.results.length - 1];
            const text = last?.[0]?.transcript?.trim() || '';
            if (!text) return;
            onStatus(`Heard: "${text}"`);
            if (last.isFinal && !handled) {
              handled = true;
              setIsListening(false);
              void onFinal(text);
            }
          };
          rec.onerror = ev => {
            setIsListening(false);
            onStatus(`Mic error (${ev.error || 'denied'}) — tap again to retry`);
          };
          rec.onend = () => {
            setIsListening(false);
          };
          rec.start();
          return;
        } catch {
          setIsListening(false);
        }
      }

      if (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
        setIsListening(true);
        onStatus(`${promptMsg} (NVIDIA STT)...`);
        try {
          const stream = await navigator.mediaDevices.getUserMedia({audio: true});
          const ctx = new AudioContext({sampleRate: 16000});
          const src = ctx.createMediaStreamSource(stream);
          const proc = ctx.createScriptProcessor(4096, 1, 1);
          const chunks: Int16Array[] = [];
          proc.onaudioprocess = e => {
            const f32 = e.inputBuffer.getChannelData(0);
            const i16 = new Int16Array(f32.length);
            for (let i = 0; i < f32.length; i += 1) {
              const s = Math.max(-1, Math.min(1, f32[i]));
              i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            chunks.push(i16);
          };
          src.connect(proc);
          proc.connect(ctx.destination);
          await new Promise(resolve => setTimeout(resolve, 3500));
          proc.disconnect();
          src.disconnect();
          stream.getTracks().forEach(t => t.stop());
          await ctx.close();

          const total = chunks.reduce((acc, c) => acc + c.length, 0);
          const merged = new Int16Array(total);
          let offset = 0;
          for (const c of chunks) {
            merged.set(c, offset);
            offset += c.length;
          }
          const tRes = await fetch(`${API_BASE}/api/transcribe`, {
            method: 'POST',
            headers: {'Content-Type': 'application/octet-stream', 'X-Sample-Rate': '16000'},
            body: merged.buffer,
          });
          const tData = await tRes.json();
          const transcript = String(tData.text || '').trim();
          setIsListening(false);
          if (transcript) {
            onStatus(`Heard: "${transcript}"`);
            await onFinal(transcript);
          } else {
            onStatus('Did not catch speech — tap again to retry');
          }
        } catch {
          setIsListening(false);
          onStatus('Microphone unavailable — check browser mic permission');
        }
        return;
      }

      onStatus('Voice input not supported in this browser');
    },
    [isListening],
  );

  const askVoiceAction = useCallback(async () => {
    setMode('ctrlf');
    await captureVoiceTranscript(
      'Listening... ask where an item is',
      setSearchMessage,
      async transcript => {
        await findObjectAction(transcript);
      },
    );
  }, [captureVoiceTranscript, findObjectAction]);

  const pinMemoryAction = useCallback(
    async (
      text: string,
      kind: string,
      overridePos?: number[],
      landmarkLabel?: string,
    ) => {
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
        position: overridePos ? [...overridePos] : [...selfWearer.position],
        landmark: landmarkLabel || (overridePos ? 'Cursor Aim' : 'Current Gaze'),
        age_label: 'just now',
      };
      setPins(prev => [newPin, ...prev]);
      try {
        await fetch(`${API_BASE}/api/pin`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...newPin, world_id: 'world_1', created_at: Date.now() / 1000}),
        });
        setPinStatus(`Saved permanently to ${newPin.landmark}: "${text}"`);
      } catch {
        setPinStatus(`Pinned locally to ${newPin.landmark} (Hub unreachable)`);
      }
      return {pinned: true, pin: newPin};
    },
    [selfWearer.position],
  );

  const persistOverlayNote = useCallback(
    async (item: HudOverlayItem, text: string) => {
      const clean = text.trim();
      if (!clean) return;
      setSavedNotesMap(prev => ({
        ...prev,
        [item.id]: clean,
        [`label:${item.label.toLowerCase()}`]: clean,
      }));
      const matched3d = objects.find(o => o.label.toLowerCase() === item.label.toLowerCase());
      const pos3d = matched3d ? matched3d.position : selfWearer.position;
      void pinMemoryAction(clean, 'note', pos3d, item.label);
      try {
        await fetch(`${API_BASE}/api/notes`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            object_id: item.id,
            label: item.label,
            text: clean,
            author: 'Conor',
            bbox_xyxy: item.rawBox,
            frame_size_px: item.frameSize,
            position_3d: pos3d,
          }),
        });
      } catch {
        /* local state + /api/pin already persisted */
      }
      speakOut(`${item.label}: ${clean}`);
    },
    [objects, selfWearer.position, pinMemoryAction, speakOut],
  );

  const finishLiveSpeech = useCallback(() => {
    if (speechStopRef.current) {
      speechStopRef.current();
      speechStopRef.current = null;
    }
  }, []);

  const startLiveSpeech = useCallback(
    async (item: HudOverlayItem) => {
      if (liveSpeech.active) {
        finishLiveSpeech();
        return;
      }
      setLiveSpeech({
        active: true,
        finishing: false,
        noteId: item.id,
        text: '',
        error: null,
      });

      let latestWebText = '';
      let recInstance: SpeechRecInstance | null = null;
      const SpeechRec =
        typeof window !== 'undefined'
          ? window.SpeechRecognition || window.webkitSpeechRecognition
          : undefined;
      if (typeof SpeechRec === 'function') {
        try {
          recInstance = new SpeechRec();
          recInstance.lang = 'en-US';
          recInstance.continuous = true;
          recInstance.interimResults = true;
          recInstance.maxAlternatives = 1;
          recInstance.onresult = ev => {
            let combined = '';
            for (let i = 0; i < ev.results.length; i += 1) {
              combined += `${ev.results[i]?.[0]?.transcript || ''} `;
            }
            const trimmed = combined.trim();
            if (trimmed) {
              latestWebText = trimmed;
              setLiveSpeech(prev => (prev.active ? {...prev, text: trimmed} : prev));
            }
          };
          recInstance.start();
        } catch {
          recInstance = null;
        }
      }

      const chunks: Int16Array[] = [];
      let stream: MediaStream | null = null;
      let ctx: AudioContext | null = null;
      let proc: ScriptProcessorNode | null = null;
      let src: MediaStreamAudioSourceNode | null = null;
      let heardSpeech = false;
      let lastSpeechMs = Date.now();
      let stopped = false;

      const finalizeCapture = async () => {
        if (stopped) return;
        stopped = true;
        speechStopRef.current = null;
        setLiveSpeech(prev => ({...prev, finishing: true}));

        try {
          recInstance?.stop?.();
        } catch {}
        try {
          proc?.disconnect();
          src?.disconnect();
          stream?.getTracks().forEach(t => t.stop());
          if (ctx) await ctx.close();
        } catch {}

        let finalTranscript = latestWebText.trim();
        if (!finalTranscript && chunks.length > 0) {
          const total = chunks.reduce((acc, c) => acc + c.length, 0);
          const merged = new Int16Array(total);
          let offset = 0;
          for (const c of chunks) {
            merged.set(c, offset);
            offset += c.length;
          }
          if (merged.byteLength >= 3200) {
            try {
              const tRes = await fetch(`${API_BASE}/api/transcribe`, {
                method: 'POST',
                headers: {'Content-Type': 'application/octet-stream', 'X-Sample-Rate': '16000'},
                body: merged.buffer,
              });
              if (tRes.ok) {
                const tData = await tRes.json();
                finalTranscript = String(tData.text || '').trim();
              }
            } catch {}
          }
        }

        if (finalTranscript) {
          await persistOverlayNote(item, finalTranscript);
          setLiveSpeech({active: false, finishing: false, noteId: null, text: '', error: null});
        } else {
          setLiveSpeech({
            active: false,
            finishing: false,
            noteId: null,
            text: '',
            error: 'No speech detected — tap add note? and speak',
          });
        }
      };

      speechStopRef.current = () => {
        void finalizeCapture();
      };

      if (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({audio: true});
          ctx = new AudioContext({sampleRate: 16000});
          src = ctx.createMediaStreamSource(stream);
          proc = ctx.createScriptProcessor(4096, 1, 1);
          proc.onaudioprocess = e => {
            if (stopped) return;
            const f32 = e.inputBuffer.getChannelData(0);
            const i16 = new Int16Array(f32.length);
            let sumSq = 0;
            for (let i = 0; i < f32.length; i += 1) {
              const s = Math.max(-1, Math.min(1, f32[i]));
              sumSq += s * s;
              i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            chunks.push(i16);
            const rms = Math.sqrt(sumSq / Math.max(1, f32.length));
            if (rms > 0.015) {
              heardSpeech = true;
              lastSpeechMs = Date.now();
            } else if (heardSpeech && Date.now() - lastSpeechMs > 1700) {
              void finalizeCapture();
            }
          };
          src.connect(proc);
          proc.connect(ctx.destination);
        } catch {
          if (!recInstance) {
            setLiveSpeech({
              active: false,
              finishing: false,
              noteId: null,
              text: '',
              error: 'Microphone permission needed for voice notes',
            });
            speechStopRef.current = null;
            return;
          }
        }
      }

      window.setTimeout(() => {
        if (!stopped) {
          void finalizeCapture();
        }
      }, 6500);
    },
    [liveSpeech.active, finishLiveSpeech, persistOverlayNote],
  );

  const onOverlayMove = useCallback((xNorm: number, yNorm: number) => {
    lastPointerMoveMsRef.current = Date.now();
    const cx = Math.max(0.02, Math.min(0.98, xNorm));
    const cy = Math.max(0.02, Math.min(0.98, yNorm));
    setHudCursor(prev => ({...prev, visible: true, x: cx, y: cy}));
    setCursorPos({xPct: cx * 100, yPct: cy * 100});
  }, []);

  const onOverlayClick = useCallback(
    (xNormOverride?: number, yNormOverride?: number) => {
      if (liveSpeech.active) {
        finishLiveSpeech();
        return;
      }
      const cx = xNormOverride !== undefined ? Math.max(0.02, Math.min(0.98, xNormOverride)) : hudCursor.x;
      const cy = yNormOverride !== undefined ? Math.max(0.02, Math.min(0.98, yNormOverride)) : hudCursor.y;
      const px = cx * 600;
      const py = cy * 600;

      // 1. Check yoloNoteAt / selected object's note prompt, label, or bounding box
      const currentSelected = hudOverlayItems.find(i => i.id === hudCursor.selectedId);
      if (currentSelected) {
        const nb = currentSelected.noteBounds;
        const lb = currentSelected.labelBounds;
        const [bx1, by1, bx2, by2] = currentSelected.box;
        const inNote = px >= nb.x - 10 && px <= nb.x + nb.w + 10 && py >= nb.y - 10 && py <= nb.y + nb.h + 10;
        const inSelectedLabel = px >= lb.x - 8 && px <= lb.x + lb.w + 8 && py >= lb.y - 8 && py <= lb.y + lb.h + 8;
        const inBox = px >= bx1 - 6 && px <= bx2 + 6 && py >= by1 - 6 && py <= by2 + 6;
        if (inNote || inSelectedLabel || inBox || xNormOverride === undefined) {
          void startLiveSpeech(currentSelected);
          return;
        }
      }

      // 2. Check yoloLabelAt (exact label bounds hit, then closest label/box)
      let hit = hudOverlayItems.find(
        i =>
          px >= i.labelBounds.x - 8 &&
          px <= i.labelBounds.x + i.labelBounds.w + 8 &&
          py >= i.labelBounds.y - 8 &&
          py <= i.labelBounds.y + i.labelBounds.h + 8,
      );
      if (!hit) {
        let bestDist = Infinity;
        for (const item of hudOverlayItems) {
          const lcx = item.labelBounds.x + item.labelBounds.w / 2;
          const lcy = item.labelBounds.y + item.labelBounds.h / 2;
          const d = Math.hypot(lcx - px, lcy - py);
          if (d < bestDist) {
            bestDist = d;
            hit = item;
          }
        }
      }
      if (hit) {
        setHudCursor({visible: true, x: cx, y: cy, selectedId: hit.id});
      }
    },
    [liveSpeech.active, finishLiveSpeech, hudCursor, hudOverlayItems, startLiveSpeech],
  );

  const aimCursor = useCallback(
    (xPct: number, yPct: number) => {
      onOverlayMove(xPct / 100, yPct / 100);
    },
    [onOverlayMove],
  );

  const dropCursorPin = useCallback(
    async (xPctOverride?: number, yPctOverride?: number) => {
      onOverlayClick(
        xPctOverride !== undefined ? xPctOverride / 100 : undefined,
        yPctOverride !== undefined ? yPctOverride / 100 : undefined,
      );
      return {pinned: true};
    },
    [onOverlayClick],
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
    void dropCursorPin();
  }, [mode, dropCursorPin]);

  const showCrewOrStepWalk = useCallback(() => {
    if (mode !== 'crew') {
      setMode('crew');
      return;
    }
    if (sensorActiveRef.current) {
      pushTelemetryUi(Date.now(), true);
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
  }, [mode, pushTelemetryUi]);

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
    isListening,
    statusNote,
    imu,
    cursorPos,
    projectedObjects,
    aimedObject,
    pinDraft,
    pinStatus,
    hudOverlayItems,
    hudCursor,
    liveSpeech,
    onOverlayMove,
    onOverlayClick,
    setPinDraft,
    aimCursor,
    dropCursorPin,
    findObjectAction,
    askVoiceAction,
    pinMemoryAction,
    runCtrlZAction,
    startSensorsAndRecord,
    stopSensorsAndUpload,
    toggleScanAction,
    cycleTargetOrShowCtrlF,
    showPinsOrDropPin,
    showCrewOrStepWalk,
  };
}
