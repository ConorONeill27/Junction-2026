import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {HudMode} from './webmcp';

const WS_PROTO =
  typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const API_BASE = '';
const WS_URL =
  typeof window !== 'undefined' && window.location.host
    ? `${WS_PROTO}//${window.location.host}/ws/stream`
    : 'ws://127.0.0.1:4173/ws/stream';

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
    dist,
    text: `${target.label} seen by ${target.seen_by}. Walk ${dist}m ${turn}.`,
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
            if (Math.abs(err) <= 18) {
              fusedYawRef.current = norm360(fusedYawRef.current + 0.08 * err);
            }
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
    statusNote,
    imu,
    findObjectAction,
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
