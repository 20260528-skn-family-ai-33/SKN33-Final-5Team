import {useEffect, useState, type ReactNode} from 'react';
import {Plus, HelpCircle} from 'lucide-react';
import {ProductTour, shouldStartTour, type TourPanel} from './product-tour';
import './workspace-chrome.css';
import {AccountMenu} from './auth';
import {AiStatus} from './ai-status';

/** One cached vector asset for all product marks and the favicon. */
export function WorkspaceBrandMark() {
  return <span className="workspace-brandmark">
    <img src="/brand/a4b-mark.svg" width="32" height="32" alt="" aria-hidden="true"/>
  </span>;
}

type HeaderProps = {
  onNew: () => void;
  disabled?: boolean;
  actionLabel?: string;
  actions?: ReactNode;
};

export function WorkspaceHeader({onNew, disabled = false, actionLabel = '새 워크플로우', actions}: HeaderProps) {
  const preview = new URLSearchParams(location.search).get('screen') === 'preview';

  return <header className="workspace-header">
    <div className="workspace-brand">
      <WorkspaceBrandMark/><strong>A4B</strong>
    </div>
    <div className="workspace-header-actions">
      <AccountMenu showLocalStatus={!preview}/>
      {!preview && <AiStatus/>}
      {actions}
      <button data-tour="create-workflow" className="as-button as-primary" onClick={onNew} disabled={disabled}>
        <Plus size={16} aria-hidden="true"/>{actionLabel}
      </button>
    </div>
  </header>;
}

export function WorkspaceHelp({onStart, deferAutoStart = false, onTourPanelChange, currentTourPanel}: {
  onStart?: () => void;
  deferAutoStart?: boolean;
  onTourPanelChange?: (panel: TourPanel) => void;
  currentTourPanel?: TourPanel;
}) {
  const [open, setOpen] = useState(false);
  const scope = new URLSearchParams(location.search).get('screen') || 'workflows';

  useEffect(() => {
    if (!deferAutoStart && shouldStartTour(scope)) setOpen(true);
  }, [scope, deferAutoStart]);

  return <>
    <button type="button" data-tour="help" className="as-button as-secondary" onClick={() => setOpen(true)}>
      <HelpCircle size={16} aria-hidden="true"/>사용 안내
    </button>
    <ProductTour scope={scope} open={open} onClose={() => setOpen(false)}
      onStart={scope === 'workflows' ? onStart : undefined}
      onPanelChange={onTourPanelChange} currentPanel={currentTourPanel}/>
  </>;
}
