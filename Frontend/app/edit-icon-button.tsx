import type {ComponentProps} from 'react';
import {Pencil} from 'lucide-react';
import './edit-icon-button.css';

export function EditIconButton({className='',title='이름 변경',...props}:Omit<ComponentProps<'button'>,'children'>&{'aria-label':string}){
 return <button {...props} type="button" title={title} className={'workspace-edit-button '+className}><Pencil size={15} strokeWidth={2} aria-hidden="true"/></button>;
}
