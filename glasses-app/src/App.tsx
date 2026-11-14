import {useEffect} from 'react';
import {
  App as WearablesApp,
  Button,
  ButtonRail,
  Page,
  Panel,
  ScrollView,
} from '@wearables-ui-toolkit/mrbd';
import {HudViews} from './HudViews';
import {useWorldGit} from './useWorldGit';
import {registerWorldGitTools} from './webmcp';

export default function App() {
  const wg = useWorldGit();

  useEffect(() => {
    const ac = new AbortController();
    registerWorldGitTools(
      {
        getStateSummary: () => ({
          mode: wg.mode,
          active_target: wg.activeTarget.label,
          directions: wg.searchMessage || wg.localNav.text,
          pin_count: wg.pins.length,
          diff_changes: wg.diff.moved.length + wg.diff.added.length + wg.diff.removed.length,
          heading_deg: wg.imu.headingDeg,
          cardinal: wg.imu.cardinal,
          pitch_deg: wg.imu.pitchDeg,
          roll_deg: wg.imu.rollDeg,
          steps: wg.imu.steps,
          is_recording_imu: wg.imu.isRecording,
          imu_samples: wg.imu.sampleCount,
        }),
        findObject: wg.findObjectAction,
        pinMemory: wg.pinMemoryAction,
        runCtrlZ: wg.runCtrlZAction,
        startImuScan: () => wg.startSensorsAndRecord(true),
        stopImuScan: wg.stopSensorsAndUpload,
      },
      ac.signal,
    );
    return () => ac.abort();
  }, [
    wg.mode,
    wg.activeTarget.label,
    wg.searchMessage,
    wg.localNav.text,
    wg.pins.length,
    wg.diff,
    wg.imu,
    wg.findObjectAction,
    wg.pinMemoryAction,
    wg.runCtrlZAction,
    wg.startSensorsAndRecord,
    wg.stopSensorsAndUpload,
  ]);

  const pageTitle =
    wg.mode === 'scan'
      ? wg.imu.isRecording
        ? 'WorldGit • REC IMU'
        : 'WorldGit • IMU Scan'
      : wg.mode === 'ctrlf'
        ? 'WorldGit • Ctrl+F'
        : wg.mode === 'pins'
          ? 'WorldGit • Pins'
          : wg.mode === 'ctrlz'
            ? 'WorldGit • Ctrl+Z'
            : 'WorldGit • Crew';

  return (
    <WearablesApp>
      <Page headerText={pageTitle} enableSystemBarInset={false}>
        <div className="action-page-shell">
          <ScrollView insetForHeader ariaLabel="WorldGit HUD content" tabIndex={0}>
            <Panel width="100%">
              <div className="content-inset" data-testid="worldgit-hud">
                <HudViews
                  mode={wg.mode}
                  activeTarget={wg.activeTarget}
                  objectsCount={wg.objects.length}
                  localNav={wg.localNav}
                  searchMessage={wg.searchMessage}
                  statusNote={wg.statusNote}
                  pins={wg.pins}
                  diff={wg.diff}
                  undoSteps={wg.undoSteps}
                  wearers={wg.wearers}
                  selfWearer={wg.selfWearer}
                  imu={wg.imu}
                />
              </div>
            </Panel>
          </ScrollView>

          <div className="action-dock">
            <ButtonRail>
              <Button title="Find" onClick={wg.cycleTargetOrShowCtrlF} />
              <Button title="Pins" onClick={wg.showPinsOrDropPin} />
              <Button title="Undo" onClick={() => void wg.runCtrlZAction()} />
              <Button title="Crew" onClick={wg.showCrewOrStepWalk} />
              <Button title={wg.imu.isRecording ? 'Stop' : 'Scan'} onClick={wg.toggleScanAction} />
            </ButtonRail>
          </div>
        </div>
      </Page>
    </WearablesApp>
  );
}
