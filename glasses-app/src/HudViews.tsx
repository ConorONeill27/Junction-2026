import type {MouseEvent} from 'react';
import {TextStyle, TextView} from '@wearables-ui-toolkit/mrbd';
import type {
  HeadCursorState,
  HudOverlayItem,
  ImuTelemetry,
  LiveSpeechState,
  SpatialObject,
  SpatialPin,
  Wearer,
  WorldDiff,
} from './useWorldGit';
import {VOICE_HINTS, type HudMode} from './webmcp';

interface HudViewsProps {
  mode: HudMode;
  activeTarget: SpatialObject;
  objectsCount: number;
  localNav: {arrow: string; angle: number; turn: string; dist: number; text: string};
  searchMessage: string;
  statusNote: string;
  pins: SpatialPin[];
  diff: WorldDiff;
  undoSteps: string[];
  wearers: Wearer[];
  selfWearer: Wearer;
  imu: ImuTelemetry;
  cursorPos: {xPct: number; yPct: number};
  projectedObjects: Array<{object: SpatialObject; xPct: number; yPct: number}>;
  aimedObject: SpatialObject;
  pinDraft: string;
  pinStatus: string;
  hudOverlayItems: HudOverlayItem[];
  hudCursor: HeadCursorState;
  liveSpeech: LiveSpeechState;
  onOverlayMove: (xNorm: number, yNorm: number) => void;
  onOverlayClick: (xNorm?: number, yNorm?: number) => void;
  onPinDraftChange: (v: string) => void;
  onAimCursor: (xPct: number, yPct: number) => void;
  onDropCursorPin: (xPct?: number, yPct?: number) => void;
}

function CompassGuide({
  angle,
  arrow,
  label,
}: {
  angle: number;
  arrow: string;
  label: string;
}) {
  const aligned = angle <= 25 || angle >= 335;
  const accent = aligned ? '#34d399' : '#38bdf8';

  return (
    <div className="compass-ring" aria-label={`Compass pointing ${Math.round(angle)} degrees toward ${label}`}>
      <svg width="144" height="144" viewBox="0 0 144 144" role="img">
        <circle cx="72" cy="72" r="64" fill="rgba(15, 23, 42, 0.55)" stroke="rgba(255, 255, 255, 0.24)" strokeWidth="2" />
        <circle cx="72" cy="72" r="46" fill="none" stroke="rgba(255, 255, 255, 0.1)" strokeWidth="1" strokeDasharray="3 5" />

        <line x1="72" y1="8" x2="72" y2="17" stroke="rgba(255,255,255,0.75)" strokeWidth="2.5" strokeLinecap="round" />
        <line x1="136" y1="72" x2="127" y2="72" stroke="rgba(255,255,255,0.35)" strokeWidth="2" strokeLinecap="round" />
        <line x1="72" y1="136" x2="72" y2="127" stroke="rgba(255,255,255,0.35)" strokeWidth="2" strokeLinecap="round" />
        <line x1="8" y1="72" x2="17" y2="72" stroke="rgba(255,255,255,0.35)" strokeWidth="2" strokeLinecap="round" />

        <text x="72" y="27" textAnchor="middle" fill="rgba(255,255,255,0.72)" fontSize="10" fontWeight="700">FWD</text>
        <text x="120" y="75" textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="9" fontWeight="600">R</text>
        <text x="72" y="123" textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="9" fontWeight="600">B</text>
        <text x="24" y="75" textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="9" fontWeight="600">L</text>

        <g style={{transform: `rotate(${angle}deg)`, transformOrigin: 'center', transition: 'transform 90ms linear'}}>
          <circle cx="72" cy="72" r="64" fill="none" />
          <polygon points="72,14 82,42 72,36 62,42" fill={accent} />
          <line x1="72" y1="36" x2="72" y2="54" stroke={accent} strokeWidth="3" strokeLinecap="round" />
        </g>

        <circle cx="72" cy="72" r="24" fill="rgba(8, 12, 22, 0.9)" stroke={accent} strokeWidth="1.5" />
        <text x="72" y="70" textAnchor="middle" fill="#ffffff" fontSize="16" fontWeight="700">
          {arrow}
        </text>
        <text x="72" y="85" textAnchor="middle" fill={accent} fontSize="11" fontWeight="600">
          {Math.round(angle)}°
        </text>
      </svg>
    </div>
  );
}

export function HudViews({
  mode,
  activeTarget,
  objectsCount,
  localNav,
  searchMessage,
  statusNote,
  pins,
  diff,
  undoSteps,
  wearers,
  selfWearer,
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
}: HudViewsProps) {
  const compassSummary =
    imu.headingDeg !== null
      ? `${imu.cardinal} ${Math.round(imu.headingDeg)}°`
      : `Yaw ${Math.round(selfWearer.yaw)}°`;

  if (mode === 'scan') {
    const recLabel = imu.isRecording
      ? `REC IMU • ${imu.sampleCount} pts`
      : imu.sensorStatus === 'active'
        ? '9-Axis Live • Idle'
        : `Sensors ${imu.sensorStatus.toUpperCase()}`;
    return (
      <>
        <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
          {recLabel} • {compassSummary}
        </TextView>
        <TextView as="p" textStyle={TextStyle.BODY2}>
          Tilt {imu.pitchDeg}°/{imu.rollDeg}° • Gyro {imu.gyroYawDeg}° • {imu.steps} steps ({imu.distanceM}m)
        </TextView>
        <TextView as="p" textStyle={TextStyle.META1}>
          {imu.lastSyncNote} • {VOICE_HINTS.scan.description}
        </TextView>
      </>
    );
  }

  if (mode === 'ctrlf') {
    const liveBadge = imu.sensorStatus === 'active' ? 'LIVE' : 'READY';
    return (
      <>
        <div className="compass-stage">
          <CompassGuide angle={localNav.angle} arrow={localNav.arrow} label={activeTarget.label} />
          <div className="compass-details">
            <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
              {activeTarget.label} • {localNav.turn}
            </TextView>
            <TextView as="p" textStyle={TextStyle.BODY2}>
              {searchMessage || localNav.text}
            </TextView>
            <TextView as="p" textStyle={TextStyle.META1}>
              {liveBadge} • {compassSummary} • {statusNote}
            </TextView>
          </div>
        </div>
        <TextView as="p" textStyle={TextStyle.META1}>
          {VOICE_HINTS.ctrlf.description}
        </TextView>
      </>
    );
  }

  if (mode === 'pins') {
    const handleSvgMouse = (e: MouseEvent<SVGSVGElement>, isClick: boolean) => {
      const rect = e.currentTarget.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const xNorm = (e.clientX - rect.left) / rect.width;
      const yNorm = (e.clientY - rect.top) / rect.height;
      onOverlayMove(xNorm, yNorm);
      if (isClick) {
        onOverlayClick(xNorm, yNorm);
      }
    };

    const selectedItem = hudOverlayItems.find(i => i.id === hudCursor.selectedId);
    const savedNotesItems = hudOverlayItems.filter(i => Boolean(i.note) && i.id !== hudCursor.selectedId);
    const cursorX = Math.max(8, Math.min(592, hudCursor.x * 600));
    const cursorY = Math.max(8, Math.min(592, hudCursor.y * 600));

    return (
      <>
        <div className="compass-stage">
          <div className="compass-ring">
            <svg
              width="264"
              height="264"
              viewBox="0 0 600 600"
              role="img"
              aria-label="Calibrated object notes overlay"
              onMouseMove={e => handleSvgMouse(e, false)}
              onClick={e => handleSvgMouse(e, true)}
            >
              <rect x="0" y="0" width="600" height="600" rx="12" fill="rgba(4, 8, 14, 0.85)" stroke="rgba(255, 255, 255, 0.22)" strokeWidth="2" />

              {hudOverlayItems.map(item => {
                const selected = item.id === hudCursor.selectedId;
                const hasSavedNote = Boolean(item.note);
                const strokeColor = selected ? '#64a5ff' : hasSavedNote ? '#48eba0' : '#00ff00';
                const labelFill = selected ? '#1e69f5' : hasSavedNote ? '#125c40' : 'rgba(0, 0, 0, 0.84)';
                const [x1, y1, x2, y2] = item.box;
                const {x: lx, y: ly, w: lw, h: lh} = item.labelBounds;
                return (
                  <g key={item.id}>
                    <rect
                      x={x1}
                      y={y1}
                      width={Math.max(12, x2 - x1)}
                      height={Math.max(12, y2 - y1)}
                      fill="none"
                      stroke={strokeColor}
                      strokeWidth="3"
                    />
                    <rect
                      x={lx}
                      y={ly}
                      width={lw}
                      height={lh}
                      rx="4"
                      fill={labelFill}
                      stroke={strokeColor}
                      strokeWidth="1.5"
                    />
                    <text
                      x={lx + 8}
                      y={ly + lh - 7}
                      fill="#ffffff"
                      fontSize="17"
                      fontWeight="600"
                    >
                      {item.label}
                    </text>
                  </g>
                );
              })}

              {savedNotesItems.map(item => {
                const {x: nx, y: ny, w: nw, h: nh} = item.noteBounds;
                return (
                  <g key={`saved-${item.id}`}>
                    <rect
                      x={nx}
                      y={ny}
                      width={nw}
                      height={nh}
                      rx="4"
                      fill="rgba(10, 24, 18, 0.94)"
                      stroke="#48eba0"
                      strokeWidth="1.5"
                    />
                    {item.noteLines.map((line, idx) => (
                      <text
                        key={idx}
                        x={nx + 8}
                        y={ny + 20 + idx * 22}
                        fill="#ffffff"
                        fontSize="16"
                      >
                        {line}
                      </text>
                    ))}
                  </g>
                );
              })}

              {selectedItem ? (
                <g key={`selected-${selectedItem.id}`}>
                  <rect
                    x={selectedItem.noteBounds.x}
                    y={selectedItem.noteBounds.y}
                    width={selectedItem.noteBounds.w}
                    height={selectedItem.noteBounds.h}
                    rx="4"
                    fill={
                      liveSpeech.active && liveSpeech.noteId === selectedItem.id && !liveSpeech.finishing
                        ? '#c31e28'
                        : 'rgba(0, 0, 0, 0.92)'
                    }
                    stroke="#64a5ff"
                    strokeWidth="2"
                  />
                  {selectedItem.noteLines.map((line, idx) => (
                    <text
                      key={idx}
                      x={selectedItem.noteBounds.x + 8}
                      y={selectedItem.noteBounds.y + 20 + idx * 22}
                      fill="#ffffff"
                      fontSize="16"
                      fontWeight="600"
                    >
                      {line}
                    </text>
                  ))}
                </g>
              ) : null}

              {hudCursor.visible ? (
                <g>
                  <circle cx={cursorX} cy={cursorY} r="8" fill="none" stroke="rgba(0, 0, 0, 0.85)" strokeWidth="4" />
                  <circle cx={cursorX} cy={cursorY} r="7" fill="#ffffff" />
                </g>
              ) : null}

              {liveSpeech.active ? (
                <g>
                  <circle cx="300" cy="300" r="39" fill="rgba(0, 0, 0, 0.78)" />
                  <rect x="291" y="275" width="18" height="32" rx="9" fill="#ffffff" />
                  <path
                    d="M 284 295 A 16 16 0 0 0 316 295"
                    fill="none"
                    stroke="#ffffff"
                    strokeWidth="3"
                  />
                  <line x1="300" y1="315" x2="300" y2="326" stroke="#ffffff" strokeWidth="3" />
                  <line x1="290" y1="326" x2="310" y2="326" stroke="#ffffff" strokeWidth="3" />

                  <rect
                    x="24"
                    y="502"
                    width="552"
                    height="74"
                    rx="10"
                    fill="rgba(12, 16, 24, 0.92)"
                    stroke="#ffffff"
                    strokeWidth="1.5"
                  />
                  <text x="40" y="546" fill="#ffffff" fontSize="22" fontWeight="600">
                    {(liveSpeech.text || 'Listening...').slice(-42)}
                  </text>
                </g>
              ) : null}
            </svg>
          </div>

          <div className="compass-details">
            <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
              {selectedItem ? `Target: ${selectedItem.label}` : 'Aim cursor at object'}
            </TextView>
            <TextView as="p" textStyle={TextStyle.BODY2}>
              {liveSpeech.active
                ? liveSpeech.text || 'Listening for voice note...'
                : selectedItem?.note
                  ? `Saved: "${selectedItem.note}"`
                  : pinStatus || 'Select label, then click add note?'}
            </TextView>
            <TextView as="p" textStyle={TextStyle.META1}>
              {liveSpeech.error || `${hudOverlayItems.length} calibrated objects • ${pins.length} saved`}
            </TextView>
          </div>
        </div>
        <TextView as="p" textStyle={TextStyle.META1}>
          {VOICE_HINTS.pins.description}
        </TextView>
      </>
    );
  }

  if (mode === 'ctrlz') {
    return (
      <>
        <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
          Reality git diff: {diff.moved.length} moved, {diff.removed.length} missing, {diff.added.length} added
        </TextView>
        {undoSteps.slice(0, 3).map((step, i) => (
          <TextView key={i} as="p" textStyle={TextStyle.BODY2}>
            {i + 1}. {step}
          </TextView>
        ))}
      </>
    );
  }

  return (
    <>
      <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
        Live Shared Space ({wearers.length} wearers) • {compassSummary}
      </TextView>
      {wearers.map(w => {
        const d =
          Math.round(
            Math.hypot(
              w.position[0] - selfWearer.position[0],
              w.position[2] - selfWearer.position[2],
            ) * 10,
          ) / 10;
        return (
          <TextView key={w.wearer_id} as="p" textStyle={TextStyle.BODY2}>
            {w.name}:{' '}
            {w.wearer_id === 'wearer-b'
              ? `(${w.position[0]}, ${w.position[2]}) yaw ${w.yaw}° • ${imu.steps} steps`
              : `${d}m away`}
          </TextView>
        );
      })}
    </>
  );
}
