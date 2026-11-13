import {TextStyle, TextView} from '@wearables-ui-toolkit/mrbd';
import type {SpatialObject, SpatialPin, Wearer, WorldDiff} from './useWorldGit';
import {VOICE_HINTS, type HudMode} from './webmcp';

interface HudViewsProps {
  mode: HudMode;
  activeTarget: SpatialObject;
  objectsCount: number;
  localNav: {arrow: string; dist: number; text: string};
  searchMessage: string;
  statusNote: string;
  pins: SpatialPin[];
  diff: WorldDiff;
  undoSteps: string[];
  wearers: Wearer[];
  selfWearer: Wearer;
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
}: HudViewsProps) {
  if (mode === 'ctrlf') {
    return (
      <>
        <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
          {localNav.arrow} {localNav.dist}m • {activeTarget.label}
        </TextView>
        <TextView as="p" textStyle={TextStyle.BODY2}>
          {searchMessage || localNav.text}
        </TextView>
        <TextView as="p" textStyle={TextStyle.META1}>
          Indexed by {activeTarget.seen_by} • {objectsCount} items ({statusNote}) • {VOICE_HINTS.ctrlf.description}
        </TextView>
      </>
    );
  }

  if (mode === 'pins') {
    return (
      <>
        <TextView as="p" textStyle={TextStyle.BODY2_EMPHASIZED}>
          Spatial Triggers &amp; Friend&apos;s World ({pins.length})
        </TextView>
        {pins.slice(0, 3).map(pin => (
          <TextView key={pin.id} as="p" textStyle={TextStyle.BODY2}>
            [{pin.pin_type.toUpperCase()}] {pin.author} ({pin.age_label || 'now'}): {pin.text}
          </TextView>
        ))}
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
        Live Shared Space ({wearers.length} wearers)
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
              ? `(${w.position[0]}, ${w.position[2]}) yaw ${w.yaw}°`
              : `${d}m away`}
          </TextView>
        );
      })}
    </>
  );
}
