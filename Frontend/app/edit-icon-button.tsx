import type {ComponentProps} from 'react';
import {Pencil} from 'lucide-react';
import {WorkspaceIconButton} from './workspace-icon-button';

export function EditIconButton({className='',title='이름 변경',...props}:Omit<ComponentProps<'button'>,'children'>&{'aria-label':string}){
 return <WorkspaceIconButton {...props} title={title} iconSize={15} className={'workspace-edit-button '+className}><Pencil size={15} strokeWidth={2} aria-hidden="true"/></WorkspaceIconButton>;
}
