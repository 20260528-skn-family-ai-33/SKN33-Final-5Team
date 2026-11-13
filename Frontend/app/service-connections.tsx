import {useEffect,useState} from 'react';
import {Sheet,MessageSquare} from 'lucide-react';
import {Toaster} from '@/components/ui/sonner';
import {ConnectionPanel,request,type CredentialPublic} from '../components/flow/connection-panel';
import {GmailConnection} from './gmail-connection';
import './service-connections.css';

export function ServiceConnections() {
  const [selected,setSelected]=useState<'googleSheets'|'slack'|null>(null);
  const [credentials,setCredentials]=useState<CredentialPublic[]>([]);
  useEffect(()=>{void load();},[]);
  async function load(){try{const result=await request<{credentials:CredentialPublic[]}>('/api/credentials');setCredentials(result.credentials||[]);}catch{/* The connection panel shows a retryable error. */}}
  const external=[
    {id:'googleSheets' as const,label:'Google Sheets',description:'재고 시트를 읽고 결과 행을 기록합니다.',Icon:Sheet},
    {id:'slack' as const,label:'Slack',description:'재고 부족 알림을 채널로 보냅니다.',Icon:MessageSquare},
  ];
  return <section className="service-connections" aria-labelledby="service-connections-heading">
    <Toaster theme="light" position="top-center"/>
    <div className="entry-page-heading"><div>
      <h1 id="service-connections-heading">서비스 연결</h1>
    </div></div>
    <div className="service-connection-category"><h2>메일</h2><span>1개 서비스</span></div>
    <div className="service-connection-grid"><GmailConnection disabled={false} variant="card"/></div>
    <div className="service-connection-category"><h2>외부 API</h2><span>2개 서비스</span></div>
    <div className="service-connection-grid">{external.map(({id,label,description,Icon})=>{
      const connected=credentials.some(credential=>id==='slack'?credential.provider==='slack':credential.service==='googleSheets');
      return <article className="service-connection-card" key={id}>
        <div className="service-connection-card-heading"><span className="service-connection-logo"><Icon size={22}/></span><h3>{label}</h3><span className={'service-connection-state'+(connected?' connected':'')}>{connected?'연결됨':'연결 필요'}</span></div>
        <p>{description}</p>
        <button type="button" className="as-button as-secondary" onClick={()=>setSelected(id)}>{connected?'연결 관리':'연결하기'}</button>
      </article>;
    })}</div>
    {selected&&<ConnectionPanel key={selected} initialService={selected} allowedServices={['googleSheets','slack']} onClose={()=>setSelected(null)} onChanged={load}/>}
    <p className="service-connections-note">연결한 계정은 현재 작업 공간에서 사용합니다. 각 서비스에서 허용한 권한은 연결 관리에서 확인할 수 있어요.</p>
  </section>;
}
