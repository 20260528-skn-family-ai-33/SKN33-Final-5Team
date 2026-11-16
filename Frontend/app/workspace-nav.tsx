import {WorkspacePageTitle} from './workspace-page-title';
import {useId,useRef,useState,type ReactNode} from 'react';
import {AccountMenu,useAuth} from './auth';
import {House, Workflow, CalendarClock, History, Layers, FolderOpen, Unplug, Pin, PinOff, Activity} from 'lucide-react';

export type WorkspaceDestination = 'home' | 'workflow' | 'workflows' | 'schedules' | 'history' | 'assets' | 'connections' | 'monitoring';

/** The same destinations and labels are available throughout the work area. */
export function WorkspaceNav({active, onNavigate, disabled = false}: {
  active: string;
  onNavigate: (destination: WorkspaceDestination) => void;
  disabled?: boolean;
}) {
  const auth=useAuth();
  return <nav className="workspace-nav" aria-label="작업 공간">
    {([
      ['home', '홈', House],
      ['workflow', '워크플로우', Workflow],
      ['workflows', '작업 라이브러리', Layers],
      ['schedules', '예약 관리', CalendarClock],

      ['history', '실행 기록', History],
      ['assets', '자료함', FolderOpen],
      ['connections', '서비스 연결', Unplug],
    ] as const).map(([destination, label, Icon]) =>
      <button data-tour={'menu-'+destination} key={destination} type="button" disabled={disabled}
        aria-label={label} title={label} aria-current={active === destination ? 'page' : undefined}
        onClick={() => onNavigate(destination)}>
        <Icon size={17} aria-hidden="true"/><span className="workspace-menu-label">{label}</span>
      </button>)}
    {auth.user?.role==='admin'&&<button type="button" disabled={disabled} aria-label="모니터링" title="모니터링" aria-current={active==='monitoring'?'page':undefined} onClick={()=>onNavigate('monitoring')}><Activity size={17} aria-hidden="true"/><span className="workspace-menu-label">모니터링</span></button>}
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
      <WorkspacePageTitle icon={Layers}>작업 라이브러리</WorkspacePageTitle>
      {actions}
    </div>
    <nav className="task-library-tabs" aria-label="라이브러리 보기">
      {([
        ['workflows', '저장한 워크플로우', Workflow],
        ['nodes', '작업 템플릿', Layers],
      ] as const).map(([destination,label,Icon])=>
        <button className="as-button" key={destination} type="button" aria-current={active===destination?'page':undefined}
          disabled={disabled} onClick={()=>onNavigate(destination)}>
          <Icon size={16} aria-hidden="true"/>
          <span>{label}</span>
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
  const contentId=useId(),pointerFocus=useRef(false);
  const [pinned,setPinned]=useState(()=>{try{return localStorage.getItem('a4b-sidebar-pinned')==='true';}catch{return false;}});
  const [hovered,setHovered]=useState(false),[focused,setFocused]=useState(false),[suppressed,setSuppressed]=useState(false);
  const expanded=pinned||!suppressed&&(hovered||focused);
  function rememberPinned(next:boolean){setPinned(next);try{localStorage.setItem('a4b-sidebar-pinned',String(next));}catch{}}
  function togglePin(){rememberPinned(!pinned);setSuppressed(false);}
  return <aside className="workspace-sidebar" data-expanded={expanded} data-pinned={pinned}
    onPointerEnter={event=>{if(event.pointerType!=='touch'){setHovered(true);setSuppressed(false);}}}
    onPointerLeave={()=>{setHovered(false);if(pointerFocus.current)setFocused(false);setSuppressed(false);}}
    onPointerDownCapture={()=>{pointerFocus.current=true;}}
    onKeyDownCapture={event=>{if(event.key==='Tab'||event.key.startsWith('Arrow')){pointerFocus.current=false;setFocused(true);setSuppressed(false);}if(event.key==='Escape'&&!pinned){setHovered(false);setFocused(false);setSuppressed(true);}}}
    onFocusCapture={()=>{if(!pointerFocus.current){setFocused(true);setSuppressed(false);}}}
    onBlurCapture={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node|null)){pointerFocus.current=false;setFocused(false);setSuppressed(false);}}}>
    <div className="workspace-sidebar-header">
      <button className="workspace-sidebar-pin" type="button" hidden={!expanded} aria-label={pinned?'메뉴 고정 해제':'메뉴 고정'} title={pinned?'메뉴 고정 해제':'메뉴 고정'} aria-pressed={pinned} aria-controls={contentId} onClick={togglePin}>
        {pinned?<PinOff size={15} aria-hidden="true"/>:<Pin size={15} aria-hidden="true"/>}
      </button>
    </div>
    <div id={contentId} className="workspace-sidebar-content">
    <WorkspaceNav active={active} onNavigate={destination=>{if(!pinned){setHovered(false);setFocused(false);setSuppressed(true);}onNavigate(destination);}} disabled={disabled}/>
    {children}
    <div className="workspace-sidebar-footer">
      {help}
      <div className="workspace-sidebar-account"><AccountMenu showLocalStatus/></div>
    </div>
    </div>
  </aside>;
}
