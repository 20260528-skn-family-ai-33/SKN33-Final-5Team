import type {ReactNode} from 'react';
import {AccountMenu} from './auth';
import {House, Workflow, CalendarClock, History, Layers, FolderOpen, Unplug, Check} from 'lucide-react';

export type WorkspaceDestination = 'home' | 'workflow' | 'workflows' | 'schedules' | 'history' | 'assets' | 'connections';

/** The same destinations and labels are available throughout the work area. */
export function WorkspaceNav({active, onNavigate, disabled = false}: {
  active: string;
  onNavigate: (destination: WorkspaceDestination) => void;
  disabled?: boolean;
}) {
  return <nav className="workspace-nav" aria-label="작업 공간">
    {([
      ['home', '홈', House],
      ['workflow', '워크플로우', Workflow],
      ['workflows', '작업 라이브러리', Layers],
      ['schedules', '예약 실행', CalendarClock],
      ['history', '실행 기록', History],
      ['assets', '자료함', FolderOpen],
      ['connections', '서비스 연결', Unplug],
    ] as const).map(([destination, label, Icon]) =>
      <button data-tour={'menu-'+destination} key={destination} type="button" disabled={disabled}
        aria-current={active === destination ? 'page' : undefined}
        onClick={() => onNavigate(destination)}>
        <Icon size={17} aria-hidden="true"/>{label}
      </button>)}
  </nav>;
}

export function TaskLibraryHeader({active, onNavigate, actions, disabled = false}: {
  active: 'workflows' | 'nodes';
  onNavigate: (destination: 'workflows' | 'nodes') => void;
  actions?: ReactNode;
  disabled?: boolean;
}) {
  return <div className="task-library-header">
    <div className="task-library-title">
      <div>
        <h1>작업 라이브러리</h1>
      </div>
      {actions}
    </div>
    <nav className="task-library-tabs" aria-label="라이브러리 보기">
      {([
        ['workflows', '저장한 워크플로우', Workflow],
        ['nodes', '재사용 작업', Layers],
      ] as const).map(([destination,label,Icon])=>
        <button className="as-button" key={destination} type="button" aria-current={active===destination?'page':undefined}
          disabled={disabled} onClick={()=>onNavigate(destination)}>
          <Icon size={16} aria-hidden="true"/>
          <span>{label}</span>
          <Check className="task-library-selection" size={14} aria-hidden="true"/>
        </button>
      )}
    </nav>
  </div>;
}

export function WorkspaceSidebar({active, onNavigate, help, disabled = false, children}: {
  active: string;
  onNavigate: (destination: WorkspaceDestination) => void;
  help: ReactNode;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return <aside className="workspace-sidebar">
    <WorkspaceNav active={active} onNavigate={onNavigate} disabled={disabled}/>
    {children}
    <div className="workspace-sidebar-footer">
      {help}
      <div className="workspace-sidebar-account"><AccountMenu showLocalStatus/></div>
    </div>
  </aside>;
}
