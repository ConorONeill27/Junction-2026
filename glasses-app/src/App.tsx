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
        }),
        findObject: wg.findObjectAction,
        pinMemory: wg.pinMemoryAction,
        runCtrlZ: wg.runCtrlZAction,
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
    wg.findObjectAction,
    wg.pinMemoryAction,
    wg.runCtrlZAction,
  ]);

  const pageTitle =
    wg.mode === 'ctrlf'
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
                />
              </div>
            </Panel>
          </ScrollView>

          <div className="action-dock">
            <ButtonRail>
              <Button title="Ctrl+F" onClick={wg.cycleTargetOrShowCtrlF} />
              <Button title="Pins" onClick={wg.showPinsOrDropPin} />
              <Button title="Ctrl+Z" onClick={() => void wg.runCtrlZAction()} />
              <Button title="Crew" onClick={wg.showCrewOrStepWalk} />
            </ButtonRail>
          </div>
        </div>
      </Page>
    </WearablesApp>
  );
}
