import {useEffect,useRef,useState} from 'react';
import {Sheet,MessageSquare} from 'lucide-react';
import {Toaster} from '@/components/ui/sonner';
import {request,type CredentialPublic} from '../components/flow/connection-panel';
import {GmailConnection} from './gmail-connection';
import './service-connections.css';

type Service='googleSheets'|'slack';
type Status={services:Record<Service,{configured:boolean}>;credentials:CredentialPublic[]};
type Attempt={authorizationUrl:string;expiresAt:number};
const entries=[
  {id:'googleSheets' as const,label:'Google Sheets',description:'시트를 읽고 결과 행을 기록합니다.',permission:'읽기·수정',Icon:Sheet},
  {id:'slack' as const,label:'Slack',description:'워크플로 결과를 채널로 보냅니다.',permission:'메시지 전송',Icon:MessageSquare},
];
const connected=(status:Status|undefined,service:Service)=>status?.credentials.find(c=>service==='slack'?c.service==='slack'||c.provider==='slack':c.service==='googleSheets');

export function ServiceConnections(){
  const [status,setStatus]=useState<Status>();
  const [notice,setNotice]=useState('');
  const [busy,setBusy]=useState<Service|null>(null);
  const [pending,setPending]=useState<{service:Service;attempt:Attempt;previous?:string}|null>(null);
  const popup=useRef<Window|null>(null);
  async function load(){try{setStatus(await request<Status>('/api/service-connections'));}catch(e){setNotice((e as Error).message);}}
  useEffect(()=>{void load();const focus=()=>void load();window.addEventListener('focus',focus);return()=>window.removeEventListener('focus',focus);},[]);
  useEffect(()=>{
    if(!pending)return;
    const finish=()=>{popup.current?.close();popup.current=null;setPending(null);setBusy(null);void load();};
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==popup.current||event.data?.type!=='easyflow-oauth')return;
      if(event.data.ok)finish();else{setNotice('계정 연결을 완료하지 못했어요. 다시 시도해주세요.');finish();}
    };
    const timer=window.setInterval(async()=>{
      if(Date.now()>=pending.attempt.expiresAt){setPending(null);setBusy(null);setNotice('연결 요청이 만료됐어요. 다시 시도해주세요.');return;}
      try{const latest=await request<Status>('/api/service-connections');const after=connected(latest,pending.service);
        if(after&&after.updatedAt!==pending.previous)finish();
      }catch{}
    },1200);
    window.addEventListener('message',receive);
    return()=>{window.clearInterval(timer);window.removeEventListener('message',receive);};
  },[pending]);
  async function connect(service:Service){
    if(busy)return;setBusy(service);setNotice('');
    let windowRef:Window|null=null;
    try{windowRef=window.open('about:blank','a4b-'+service+'-oauth','popup,width=560,height=720');}catch{}
    popup.current=windowRef;
    try{const attempt=await request<Attempt>('/api/service-connections/connect',{service});setPending({service,attempt,previous:connected(status,service)?.updatedAt});
      if(windowRef)windowRef.location.replace(attempt.authorizationUrl);else location.assign(attempt.authorizationUrl);
    }catch(e){windowRef?.close();popup.current=null;setBusy(null);setNotice((e as Error).message);}
  }
  async function manage(service:Service,id:string,action:'test'|'disconnect'){
    if(busy)return;setBusy(service);setNotice('');
    try{if(action==='test'){const result=await request<{message:string}>('/api/credentials/test',{id});setNotice(result.message);}
      else{await request('/api/credentials?id='+encodeURIComponent(id),undefined,'DELETE');await load();}
    }catch(e){setNotice((e as Error).message);}finally{setBusy(null);}
  }
  return <section className="service-connections" aria-labelledby="service-connections-heading">
    <Toaster theme="light" position="top-center"/>
    <div className="entry-page-heading"><div><h1 id="service-connections-heading">서비스 연결</h1></div></div>
    {notice&&<p role="status" className="service-connection-feedback">{notice}</p>}
    <div className="service-connection-grid"><GmailConnection disabled={false} variant="card"/>
      {entries.map(({id,label,description,permission,Icon})=>{
        const credential=connected(status,id),configured=status?.services[id].configured;
        return <article className="service-connection-card" key={id} aria-label={label+' 서비스'}>
          <div className="service-connection-card-heading"><span className="service-connection-logo"><Icon size={22} aria-hidden="true"/></span><h3>{label}</h3><span className={'service-connection-state'+(credential?' connected':'')}>{credential?'연결됨':status?configured?'연결 안 됨':'설정 대기':'확인 중'}</span></div>
          {credential&&<div className="service-connection-account"><span>연결된 계정</span><strong>{credential.name}</strong></div>}
          <p>{description}</p><span className="service-connection-permissions">{permission}</span>
          {!configured&&status&&!credential&&<p>서버의 {label} 연결 설정이 필요합니다.</p>}
          <div className="service-connection-actions"><button type="button" className="as-button as-primary" disabled={!!busy||!configured} onClick={()=>void connect(id)}>{credential?'계정 다시 연결':id==='slack'?'Slack에서 연결':'Google에서 연결'}</button>
            {credential&&<><button type="button" className="as-button" disabled={!!busy} onClick={()=>void manage(id,credential.id,'test')}>연결 확인</button><button type="button" className="as-text-button service-connection-disconnect" disabled={!!busy} onClick={()=>void manage(id,credential.id,'disconnect')}>연결 해제</button></>}
          </div>
        </article>;
      })}
    </div>
    {pending&&<a href={pending.attempt.authorizationUrl} className="as-text-button" referrerPolicy="no-referrer">팝업이 보이지 않으면 계정 연결 계속</a>}
    <p className="service-connections-note">서비스에서 허용한 권한은 연결된 워크플로우 단계에서 사용합니다.</p>
  </section>;
}
