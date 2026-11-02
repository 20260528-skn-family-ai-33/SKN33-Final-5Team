import type {ReactNode} from 'react';
import {Workflow, History, Layers} from 'lucide-react';

export type WorkspaceDestination = 'workflows' | 'history' | 'nodes';

/** The same destinations and labels are available throughout the work area. */
export function WorkspaceNav({active, onNavigate, disabled = false}: {
  active: string;
  onNavigate: (destination: WorkspaceDestination) => void;
  disabled?: boolean;
}) {
  return <nav className="workspace-nav" aria-label="작업 공간">
    {([
      ['workflows', '워크플로우', Workflow],
      ['history', '실행 기록', History],
      ['nodes', '작업 라이브러리', Layers],
    ] as const).map(([destination, label, Icon]) =>
      <button data-tour={'menu-'+destination} key={destination} type="button" disabled={disabled}
        aria-current={active === destination ? 'page' : undefined}
        onClick={() => onNavigate(destination)}>
        <Icon size={17} aria-hidden="true"/>{label}
      </button>)}
  </nav>;
}

export function WorkspaceSidebar({active,onNavigate,onHome,disabled=false,children}:{active:string;onNavigate:(destination:WorkspaceDestination)=>void;onHome?:()=>void;disabled?:boolean;children?:ReactNode}){
 return <aside className="workspace-sidebar"><WorkspaceNav active={active} onNavigate={onNavigate} disabled={disabled}/>{children}{onHome&&<div className="workspace-sidebar-footer"><button type="button" disabled={disabled} onClick={onHome}>서비스 소개</button></div>}</aside>;
}
