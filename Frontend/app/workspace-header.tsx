import {useEffect, useState} from 'react';
import {Plus, HelpCircle} from 'lucide-react';
import {ProductTour, shouldStartTour, type TourPanel} from './product-tour';
import './workspace-chrome.css';

/** One cached vector asset for all product marks and the favicon. */
export function WorkspaceBrandMark() {
  return <span className="workspace-brandmark">
    <img src="/brand/a4b-mark.svg" width="32" height="32" alt="" aria-hidden="true"/>
  </span>;
}

type HeaderProps = {
  onHome: () => void;
  onNew: () => void;
  disabled?: boolean;
  actionLabel?: string;
  onTourPanelChange?: (panel: TourPanel) => void;
  currentTourPanel?: TourPanel;
};

export function WorkspaceHeader({onHome, onNew, disabled = false, actionLabel = '새 워크플로우', onTourPanelChange, currentTourPanel}: HeaderProps) {
  const [help, setHelp] = useState(false);
  const scope = new URLSearchParams(location.search).get('screen') || 'workflows';

  useEffect(() => {
    if (!disabled && shouldStartTour(scope)) setHelp(true);
  }, [scope, disabled]);

  return <>
    <header className="workspace-header">
      <button className="workspace-brand" onClick={onHome} disabled={disabled} aria-label="서비스 소개로 이동">
        <WorkspaceBrandMark/><strong>A4B</strong>
      </button>
      <div className="workspace-header-actions">
        <button data-tour="help" className="as-text-button" onClick={() => setHelp(true)}>
          <HelpCircle size={16} aria-hidden="true"/>사용 안내
        </button>
        <button data-tour="create-workflow" className="as-button as-primary" onClick={onNew} disabled={disabled}>
          <Plus size={16} aria-hidden="true"/>{actionLabel}
        </button>
      </div>
    </header>
    <ProductTour scope={scope} open={help} onClose={() => setHelp(false)}
      onStart={scope === 'workflows' ? onNew : undefined} onPanelChange={onTourPanelChange} currentPanel={currentTourPanel}/>
  </>;
}
