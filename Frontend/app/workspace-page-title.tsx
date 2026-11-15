import type {ReactNode} from 'react';
import type {LucideIcon} from 'lucide-react';

export function WorkspacePageTitle({icon:Icon,children,id}:{icon:LucideIcon;children:ReactNode;id?:string}){
 return <div className="workspace-page-title"><Icon size={22} aria-hidden="true"/><h1 id={id}>{children}</h1></div>;
}
