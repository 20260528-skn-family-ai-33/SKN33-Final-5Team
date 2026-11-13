export function StudioMessage({role,content}:{role:string;content:string}){
 return <div className={'as-message '+role}><span className="as-message-label">{role==='user'?'나':'A4B'}</span><p>{content}</p></div>;
}
