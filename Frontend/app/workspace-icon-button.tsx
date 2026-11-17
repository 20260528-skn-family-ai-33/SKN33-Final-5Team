import type {ComponentProps,CSSProperties} from 'react';

type Props=ComponentProps<'button'>&{'aria-label':string;size?:'compact'|'standard';variant?:'ghost'|'outline';iconSize?:number};
export function WorkspaceIconButton({children,className='',size='compact',variant='ghost',iconSize=16,type='button',style,...props}:Props){
 return <button {...props} type={type} className={'workspace-icon-button is-'+size+' is-'+variant+' '+className}
  style={{'--workspace-icon-size':iconSize+'px',...style} as CSSProperties}>{children}</button>;
}
