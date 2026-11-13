import {useEffect,useRef,useState} from 'react';
import {Toaster} from '@/components/ui/sonner';
import {Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle} from '@/components/ui/dialog';
import {request,type CredentialPublic} from '../components/flow/connection-panel';
import {GmailConnection} from './gmail-connection';
import './service-connections.css';

type Service='googleSheets'|'slack';
type DisplayCredential=CredentialPublic&{accountLabel?:string};
type Status={services:Record<Service,{configured:boolean}>;credentials:DisplayCredential[]};
type Attempt={authorizationUrl:string;expiresAt:number};
const entries=[
  {id:'googleSheets' as const,label:'Google Sheets',description:'시트를 읽고 결과 행을 기록합니다.',permission:'읽기·수정'},
  {id:'slack' as const,label:'Slack',description:'워크플로 결과를 채널로 보냅니다.',permission:'메시지 전송·채널 조회'},
];
const connected=(status:Status|undefined,service:Service)=>status?.credentials.find(c=>service==='slack'?c.service==='slack'||c.provider==='slack':c.service==='googleSheets');
const accountLabel=(credential:DisplayCredential,service:Service)=>credential.accountLabel?.trim()||
  (service==='googleSheets'?'계정 이메일 확인 필요':'워크스페이스 정보 확인 필요');

export function ServiceConnections(){
  const [status,setStatus]=useState<Status>();
  const [notice,setNotice]=useState('');
  const [busy,setBusy]=useState<Service|null>(null);
  const [managing,setManaging]=useState<Service|null>(null);
  const [pending,setPending]=useState<{service:Service;attempt:Attempt;previous?:string}|null>(null);
  const popup=useRef<Window|null>(null);
  const statusRequest=useRef(0);
  const changing=useRef(false);
  async function load(){
    const requestId=++statusRequest.current;
    try{const next=await request<Status>('/api/service-connections');if(requestId===statusRequest.current&&!changing.current)setStatus(next);}
    catch(e){if(requestId===statusRequest.current&&!changing.current)setNotice((e as Error).message);}
  }
  useEffect(()=>{void load();const focus=()=>void load();window.addEventListener('focus',focus);return()=>window.removeEventListener('focus',focus);},[]);
  useEffect(()=>{
    if(!pending)return;
    const finish=()=>{popup.current?.close();popup.current=null;changing.current=false;statusRequest.current++;setPending(null);setBusy(null);void load();};
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==popup.current||event.data?.type!=='easyflow-oauth')return;
      if(event.data.ok)finish();else{setNotice('계정 연결을 완료하지 못했어요. 다시 시도해주세요.');finish();}
    };
    const timer=window.setInterval(async()=>{
      if(Date.now()>=pending.attempt.expiresAt){changing.current=false;statusRequest.current++;setPending(null);setBusy(null);setNotice('연결 요청이 만료됐어요. 다시 시도해주세요.');return;}
      try{const latest=await request<Status>('/api/service-connections');const after=connected(latest,pending.service);
        if(after&&after.updatedAt!==pending.previous)finish();
      }catch{}
    },1200);
    window.addEventListener('message',receive);
    return()=>{window.clearInterval(timer);window.removeEventListener('message',receive);};
  },[pending]);
  async function connect(service:Service){
    if(busy)return;changing.current=true;statusRequest.current++;setBusy(service);setNotice('');
    let windowRef:Window|null=null;
    try{windowRef=window.open('about:blank','a4b-'+service+'-oauth','popup,width=560,height=720');}catch{}
    popup.current=windowRef;
    try{const attempt=await request<Attempt>('/api/service-connections/connect',{service});setPending({service,attempt,previous:connected(status,service)?.updatedAt});
      if(windowRef)windowRef.location.replace(attempt.authorizationUrl);else location.assign(attempt.authorizationUrl);
    }catch(e){windowRef?.close();popup.current=null;changing.current=false;setBusy(null);setNotice((e as Error).message);}
  }
  async function manage(service:Service,id:string,action:'test'|'disconnect'){
    if(busy)return;if(action==='disconnect'){changing.current=true;statusRequest.current++;}setBusy(service);setNotice('');
    try{if(action==='test'){const result=await request<{message:string}>('/api/credentials/test',{id});setNotice(result.message);}
      else{await request('/api/credentials?id='+encodeURIComponent(id),undefined,'DELETE');setManaging(null);changing.current=false;await load();}
    }catch(e){setNotice((e as Error).message);}finally{changing.current=false;setBusy(null);}
  }
  const managedEntry=entries.find(entry=>entry.id===managing);
  const managedCredential=managing?connected(status,managing):undefined;
  return <section className="service-connections" aria-labelledby="service-connections-heading">
    <Toaster theme="light" position="top-center"/>
    <div className="entry-page-heading"><div><h1 id="service-connections-heading">서비스 연결</h1></div></div>
    {notice&&!managing&&<p role="status" className="service-connection-feedback">{notice}</p>}
    <div className="service-connection-grid"><GmailConnection disabled={false} variant="card"/>
      {entries.map(({id,label,description,permission})=>{
        const credential=connected(status,id),configured=status?.services[id].configured;
        return <article className="service-connection-card" key={id} aria-label={label+' 서비스'}>
          <div className="service-connection-card-heading"><span className="service-connection-logo"><img src={`/service-icons/${id.toLowerCase()}.svg`} width={22} height={22} alt=""/></span><h3>{label}</h3><span className={'service-connection-state'+(credential?' connected':'')}>{credential?'연결됨':status?configured?'연결 안 됨':'설정 대기':'확인 중'}</span></div>
          {credential&&<div className="service-connection-account"><span>연결된 계정</span><strong>{accountLabel(credential,id)}</strong></div>}
          <p>{description}</p><span className="service-connection-permissions">{permission}</span>
          {!configured&&status&&!credential&&<p>서버의 {label} 연결 설정이 필요합니다.</p>}
          <div className="service-connection-actions">
            {credential
              ? <button type="button" className="as-button service-connection-manage" disabled={!!busy} onClick={()=>{setNotice('');setManaging(id);}} aria-label={label+' 연결 관리'}>연결 관리</button>
              : <button type="button" className="as-button as-primary" disabled={!!busy||!configured} onClick={()=>void connect(id)}>{id==='slack'?'Slack에서 연결':'Google에서 연결'}</button>}
          </div>
        </article>;
      })}
    </div>
    <Dialog open={!!managing&&!!managedCredential} onOpenChange={value=>{if(!value)setManaging(null);}}>
      {managedEntry&&managedCredential&&<DialogContent className="as-dialog">
        <DialogHeader>
          <DialogTitle>{managedEntry.label} 연결 관리</DialogTitle>
          <DialogDescription>{managedEntry.label} 계정의 연결 상태를 확인하고 관리하세요.</DialogDescription>
        </DialogHeader>
        <div className="gmail-connected-account">
          {managedEntry&&<img src={`/service-icons/${managedEntry.id.toLowerCase()}.svg`} width={20} height={20} alt=""/>}
          <div><span>연결된 계정</span><strong>{accountLabel(managedCredential,managedEntry.id)}</strong></div>
        </div>
        {managedEntry.id==='googleSheets'&&!managedCredential.accountLabel?.trim()&&<p className="gmail-account-notice">계정을 다시 연결하면 연결된 Google 계정의 이메일을 표시할 수 있어요.</p>}
        {notice&&<p role="status" className="service-connection-feedback">{notice}</p>}
        {pending?.service===managedEntry.id&&<a href={pending.attempt.authorizationUrl} className="as-text-button" referrerPolicy="no-referrer">팝업이 보이지 않으면 계정 연결 계속</a>}
        <div className="service-connection-dialog-actions">
          <button type="button" className="as-button as-primary" disabled={!!busy} onClick={()=>void connect(managedEntry.id)}>계정 다시 연결</button>
          <button type="button" className="as-button" disabled={!!busy} onClick={()=>void manage(managedEntry.id,managedCredential.id,'test')}>연결 확인</button>
          <button type="button" className="as-text-button service-connection-disconnect" disabled={!!busy} onClick={()=>void manage(managedEntry.id,managedCredential.id,'disconnect')}>A4B 연결 해제</button>
        </div>
      </DialogContent>}
    </Dialog>
    {pending&&<a href={pending.attempt.authorizationUrl} className="as-text-button" referrerPolicy="no-referrer">팝업이 보이지 않으면 계정 연결 계속</a>}
    <p className="service-connections-note">서비스에서 허용한 권한은 연결된 워크플로우 단계에서 사용합니다.</p>
  </section>;
}
