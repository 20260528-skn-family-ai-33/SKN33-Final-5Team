"use client";

import {useCallback, useEffect, useRef, useState, type FormEvent} from 'react';
import {AlertCircle, CheckCircle2, KeyRound, Loader2, PlugZap, RefreshCw, ShieldCheck, Trash2, X, Sparkles, Globe, Search, ArrowRight} from 'lucide-react';
import {toast} from 'sonner';
import {Table, TableHeader, TableBody, TableRow, TableHead, TableCell} from '@/components/ui/table';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '@/components/ui/select';
import {AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle} from '@/components/ui/alert-dialog';
import type {CredentialPublic, Provider} from '@/server/contracts';

export type {CredentialPublic} from '@/server/contracts';
export type CredentialProvider = Provider;

export async function request<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const hasBody = body !== undefined;
  const response = await fetch(path, {
    method: method || (hasBody ? 'POST' : 'GET'),
    headers: hasBody ? {'Content-Type': 'application/json'} : undefined,
    body: hasBody ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let result: any;
  try { result = text ? JSON.parse(text) : null; }
  catch { throw new Error('응답을 읽지 못했어요. 잠시 후 다시 시도해주세요.'); }
  if (!response.ok) throw new Error(result?.error || '요청에 실패했어요. 다시 시도해주세요.');
  return result as T;
}

type CredentialField = {key: string; label: string; placeholder: string; secret?: boolean; multiline?:boolean; help?: string};
const providers: Record<Provider, {label: string; description: string; fields: CredentialField[]}> = {
  openai: {
    label: 'OpenAI', description: '글 요약, 질문 답변처럼 AI가 필요한 단계에서 사용해요.',
    fields: [{key: 'apiKey', label: 'OpenAI API 키', placeholder: 'sk-…', secret: true, help: 'OpenAI에서 발급받은 API 키를 붙여넣으세요.'}],
  },
  slack: {
    label: 'Slack', description: '워크플로 결과를 Slack 채널에 보낼 때 사용해요.',
    fields: [{key: 'token', label: 'Slack 봇 토큰', placeholder: 'xoxb-…', secret: true, help: '메시지를 보낼 수 있는 봇 토큰을 입력하세요.'}],
  },
  bearer: {
    label: 'API 토큰 (Bearer)', description: '토큰으로 로그인하는 API에 연결해요.',
    fields: [{key: 'token', label: 'API 토큰', placeholder: '서비스에서 발급받은 토큰', secret: true, help: 'Bearer라는 글자 없이 토큰만 넣으세요.'}],
  },
  basic: {
    label: '아이디와 비밀번호 (Basic)', description: '아이디와 비밀번호를 사용하는 API에 연결해요.',
    fields: [{key: 'username', label: '아이디', placeholder: 'API 계정 아이디'}, {key: 'password', label: '비밀번호', placeholder: 'API 계정 비밀번호', secret: true}],
  },
  header: {
    label: 'API 키 (Header)', description: '요청의 헤더에 API 키를 넣어야 하는 서비스에 연결해요.',
    fields: [{key: 'name', label: '헤더 이름', placeholder: '예: X-API-Key', help: '서비스 안내에 적힌 헤더 이름을 그대로 넣으세요.'}, {key: 'value', label: 'API 키 값', placeholder: '서비스에서 발급받은 API 키', secret: true}],
  },
  jwt: {
    label: '서명으로 인증하기 (JWT)', description: '외부 요청의 서명을 확인하거나 응답에 서명을 넣을 때 사용해요.',
    fields: [{key:'secret',label:'서명 비밀 키',placeholder:'상대 서비스와 약속한 비밀 키',secret:true,help:'요청하는 서비스와 같은 비밀 키를 입력하세요.'}],
  },
};

const services: Record<string, {label:string;description:string;provider:Provider;fields:CredentialField[]}> = {
 ...Object.fromEntries(Object.entries(providers).map(([id,value])=>[id,{...value,provider:id as Provider}])),
 github:{label:'GitHub',provider:'bearer',description:'이슈를 읽거나 만들 때 사용해요.',fields:[{key:'token',label:'GitHub 액세스 토큰',placeholder:'GitHub에서 발급받은 토큰',secret:true,help:'대상 저장소의 이슈 읽기 또는 쓰기 권한이 필요해요.'}]},
 notion:{label:'Notion',provider:'bearer',description:'페이지와 데이터 소스를 연결해요.',fields:[{key:'token',label:'Notion 연결 토큰',placeholder:'Notion에서 발급받은 토큰',secret:true,help:'대상 페이지를 이 연결과 공유해주세요.'}]},
 telegram:{label:'Telegram',provider:'bearer',description:'봇이 받은 업데이트를 읽거나 알림을 보내요.',fields:[{key:'token',label:'Telegram 봇 토큰',placeholder:'BotFather에서 받은 토큰',secret:true}]},
 discord:{label:'Discord',provider:'header',description:'채널에 만든 웹훅으로 알림을 보내요.',fields:[{key:'url',label:'Discord 웹훅 주소',placeholder:'https://discord.com/api/webhooks/…',secret:true,help:'채널 설정의 연동에서 웹훅 주소를 복사하세요.'}]},
 airtable:{label:'Airtable',provider:'bearer',description:'베이스에 저장한 레코드를 읽거나 수정해요.',fields:[{key:'token',label:'Airtable 개인 액세스 토큰',placeholder:'Airtable에서 발급받은 토큰',secret:true,help:'대상 베이스 접근과 data.records 권한을 선택하세요.'}]},
 googleSheets:{label:'Google Sheets',provider:'bearer',description:'시트의 값을 읽거나 행을 추가해요.',fields:[{key:'token',label:'Google 액세스 토큰',placeholder:'Sheets 접근이 허용된 액세스 토큰',secret:true,help:'토큰은 만료되므로 필요할 때 새 토큰으로 연결해야 해요.'}]},
 oauth2:{label:'다른 앱 로그인 (OAuth 2)',provider:'bearer',description:'서비스의 로그인 창에서 권한을 승인해 연결해요.',fields:[]},
};
const oauthFields:CredentialField[]=[{key:'clientId',label:'앱 클라이언트 ID',placeholder:'서비스에 등록한 앱의 클라이언트 ID'},{key:'clientSecret',label:'앱 클라이언트 비밀 값 (선택)',placeholder:'발급받은 경우 입력하세요',secret:true},{key:'authorizationUrl',label:'서비스 로그인 주소',placeholder:'https://서비스주소/oauth/authorize'},{key:'tokenUrl',label:'서비스 토큰 주소',placeholder:'https://서비스주소/oauth/token'},{key:'scope',label:'접근 권한 (선택)',placeholder:'서비스 안내에 적힌 scope'}];

function ServiceIcon({service}:{service:string}){const names:Record<string,string>={googleSheets:'googlesheets'};const logos=['openai','slack','github','notion','telegram','discord','airtable','googleSheets'];return <span className={'service-mark service-'+service}>{logos.includes(service)?<img src={'/service-icons/'+(names[service]||service)+'.svg'} alt="" onError={e=>{e.currentTarget.style.display='none';}}/>:service==='openai'?<Sparkles size={23}/>:<Globe size={23}/>}</span>;}

export function ConnectionPanel({onClose, onChanged}: {onClose?: () => void; onChanged?: () => void | Promise<void>}) {
  const [credentials, setCredentials] = useState<CredentialPublic[]>([]);
  const [service, setService] = useState('openai');
  const [serviceSearch,setServiceSearch]=useState('');
  function selectService(value:string){setService(value);setData({});setError('');setName('');}
  const [name, setName] = useState('');
  const [data, setData] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [actionId, setActionId] = useState('');
  const [error, setError] = useState('');
  const [listError, setListError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<CredentialPublic | null>(null);
  const [testResults, setTestResults] = useState<Record<string, {message: string; ok: boolean}>>({});
  const [guideOpen,setGuideOpen]=useState(()=>{try{return localStorage.getItem('a4b-connection-guide')!=='seen';}catch{return true;}});
  function closeGuide(){setGuideOpen(false);try{localStorage.setItem('a4b-connection-guide','seen');}catch{}}
  const [googleMethod,setGoogleMethod]=useState('oauth2');
  const [loginPending,setLoginPending]=useState(false);
  const oauthWindow=useRef<Window|null>(null);
  const selectedProvider = services[service];
  const provider = selectedProvider.provider;
  const useOAuth=service==='oauth2'||service==='googleSheets'&&googleMethod==='oauth2';
  const jwtAlgorithm=data.algorithm||'HS256';
  const jwtPurpose=data.purpose||'verify';
  const jwtFields:CredentialField[]=jwtAlgorithm.startsWith('HS')?providers.jwt.fields:[{key:jwtPurpose==='sign'?'privateKey':'publicKey',label:jwtPurpose==='sign'?'서명할 개인 키 (PEM)':'서명을 확인할 공개 키 (PEM)',placeholder:'-----BEGIN '+(jwtPurpose==='sign'?'PRIVATE':'PUBLIC')+' KEY-----',multiline:true,help:jwtPurpose==='sign'?'응답에 서명을 만들 개인 키를 입력하세요.':'요청을 보낸 서비스의 공개 키를 입력하세요.'}];
  const fields=useOAuth?oauthFields.filter(field=>service!=='googleSheets'||['clientId','clientSecret'].includes(field.key)):provider==='jwt'?jwtFields:selectedProvider.fields;
  const optionalFields=data.tokenAuthMethod==='basic'?['scope']:['clientSecret','scope'];
  const canSave = !!name.trim() && fields.every(field => optionalFields.includes(field.key) || !!data[field.key]?.trim());

  const load = useCallback(async () => {
    setLoading(true);
    setListError('');
    try {
      const result = await request<{credentials: CredentialPublic[]}>('/api/credentials');
      setCredentials(result.credentials || []);
    } catch (err) { setListError((err as Error).message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(()=>{const receive=(event:MessageEvent)=>{if(event.origin!==location.origin||event.source!==oauthWindow.current||event.data?.type!=='easyflow-oauth')return;setLoginPending(false);if(event.data.ok){setData({});setName('');toast.success('로그인으로 계정을 연결했어요.');void load();void onChanged?.();}else setError('로그인을 완료하지 못했어요. 앱 정보와 반환 주소를 확인하고 다시 연결해주세요.');oauthWindow.current?.close();oauthWindow.current=null;};window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);},[load,onChanged]);
  useEffect(()=>{if(!loginPending)return;const timer=setInterval(()=>{if(oauthWindow.current?.closed){setLoginPending(false);oauthWindow.current=null;}},700);return()=>clearInterval(timer);},[loginPending]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave || saving) return;
    setSaving(true);
    setError('');
    let popup:Window|null=null;
    try {
      if(useOAuth){
        popup=window.open('about:blank','easyflow-account-login','popup,width=620,height=760');if(!popup)throw new Error('브라우저에서 이 프로그램의 팝업을 허용하고 다시 눌러주세요.');oauthWindow.current=popup;
        const result=await request<{authorizationUrl:string;redirectUri:string}>('/api/oauth/begin',{name:name.trim(),...data,...(service==='googleSheets'?{service:'googleSheets'}:{})});
        popup.location.replace(result.authorizationUrl);setLoginPending(true);return;
      }
      await request('/api/credentials', {name: name.trim(), provider, data: {...data,...(provider==='jwt'?{algorithm:jwtAlgorithm,keyType:jwtAlgorithm.startsWith('HS')?'passphrase':'pemKey'}:{}),...(!providers[service as Provider]?{service}:{})}});
      setData({});
      setName('');
      toast.success('연결 계정을 저장했어요. 단계 설정에서 선택하세요.');
      await load();
      await onChanged?.();
    } catch (err) { popup?.close();setError((err as Error).message); }
    finally { setSaving(false); }
  }

  async function test(credential: CredentialPublic) {
    setActionId(credential.id);
    try {
      const result = await request<{message: string}>('/api/credentials/test', {id: credential.id});
      const message = result.message || '연결 확인을 마쳤어요.';
      setTestResults(previous => ({...previous, [credential.id]: {message, ok: true}}));
      toast.success(message);
    } catch (err) {
      const message = (err as Error).message;
      setTestResults(previous => ({...previous, [credential.id]: {message, ok: false}}));
      toast.error(message);
    } finally { setActionId(''); }
  }

  async function remove() {
    if (!deleteTarget) return;
    setActionId(deleteTarget.id);
    try {
      await request('/api/credentials?id=' + encodeURIComponent(deleteTarget.id), undefined, 'DELETE');
      setDeleteTarget(null);
      toast.success('연결 계정을 삭제했어요.');
      await load();
      await onChanged?.();
    } catch (err) { toast.error((err as Error).message); }
    finally { setActionId(''); }
  }

  return <div className="panel-content">
    <div className="panel-title">
      <div><h1>연결 계정</h1><p>AI와 앱을 직접 연결하고, 필요한 단계에서 계정을 선택하세요.</p></div>
      {onClose && <button className="icon-btn" aria-label="연결 계정 닫기" onClick={onClose}><X/></button>}
    </div>
    <div className="connection-onboarding"><button className="btn" aria-expanded={guideOpen} onClick={()=>guideOpen?closeGuide():setGuideOpen(true)}>계정 연결 방법 {guideOpen?'접기':'보기'}</button>{guideOpen&&<div className="help-box mt-3"><button className="icon-btn float-right" aria-label="연결 안내 닫기" onClick={closeGuide}><X size={14}/></button><b>서비스 선택 → 키·토큰 입력 → 저장 → 연결 확인</b><p>챗봇은 OpenAI API 키를 연결하세요. 앱 작업은 해당 서비스의 토큰이나 로그인 정보가 필요합니다. 저장한 계정은 챗봇 또는 단계 설정에서 선택합니다.</p></div>}</div>
    <div className="connection-layout"><section className="service-catalog" aria-label="연결할 서비스 선택"><h2>어떤 서비스를 연결할까요?</h2><div className="service-search"><Search size={16}/><input aria-label="서비스 검색" placeholder="서비스 이름 검색" value={serviceSearch} onChange={e=>setServiceSearch(e.target.value)}/></div><div className="service-grid">{Object.entries(services).filter(([id,s])=>(s.label+' '+id).toLowerCase().includes(serviceSearch.toLowerCase())).map(([id,s])=><button className={'service-choice '+(service===id?'selected':'')} key={id} aria-pressed={service===id} disabled={saving||loginPending} onClick={()=>selectService(id)}><ServiceIcon service={id}/><span><b>{s.label}</b><small>{id==='googleSheets'||id==='oauth2'?'로그인 / OAuth':id==='basic'?'아이디 · 비밀번호':'키 · 토큰 연결'}</small></span>{service===id&&<CheckCircle2 size={16}/>}</button>)}</div></section>

      <form className="connection-card" onSubmit={event => void save(event)}>
        <div className="connection-icon"><KeyRound size={26}/></div>
        <div className="selected-service-heading"><ServiceIcon service={service}/><div><small>선택한 서비스</small><h2>{selectedProvider.label} 연결</h2></div></div>

        <p className="field-help">{selectedProvider.description}</p>
        <div className="field">
          <label htmlFor="credential-name">연결 이름</label>
          <input id="credential-name" value={name} maxLength={120} disabled={saving} onChange={event => setName(event.target.value)} placeholder={`예: 내 ${selectedProvider.label} 계정`} required/>
          <small>단계에서 알아보기 쉬운 이름을 붙여주세요.</small>
        </div>
        {service==='googleSheets'&&<div className="field"><label htmlFor="google-connect-method">연결 방법</label><Select value={googleMethod} onValueChange={value=>{setGoogleMethod(value);setData({});setError('');}} disabled={saving||loginPending}><SelectTrigger id="google-connect-method"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="oauth2">Google 로그인 · 자동 갱신</SelectItem><SelectItem value="token">액세스 토큰 직접 입력</SelectItem></SelectContent></Select></div>}
        {useOAuth&&<details className="help-box mb-4"><summary>로그인 연결을 위한 앱 등록 안내</summary>{service==='googleSheets'?<>Google Cloud에서 Sheets API를 켜고 OAuth 클라이언트를 ‘웹 애플리케이션’ 유형으로 만드세요. 발급된 클라이언트 ID와 비밀 값을 아래에 입력하세요. 승인된 반환 주소에 <code className="block mt-2 break-all">{location.origin}/api/oauth/callback</code>을 등록하세요. 로그인 후에는 만료된 토큰을 자동 갱신해요. <a href="https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred" target="_blank" rel="noreferrer" className="underline block mt-2">Google 앱 등록 안내</a></>:<>서비스에 등록한 앱 정보를 입력하세요. 반환 주소는 <code className="block mt-2 break-all">{location.origin}/api/oauth/callback</code>이에요.</>}</details>}
        {service==='oauth2'&&<div className="field"><label htmlFor="oauth-token-auth">서비스의 클라이언트 인증 방식</label><Select value={data.tokenAuthMethod||'body'} onValueChange={value=>setData(previous=>({...previous,tokenAuthMethod:value}))} disabled={saving||loginPending}><SelectTrigger id="oauth-token-auth"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="body">본문에 ID·비밀 값 전송</SelectItem><SelectItem value="basic">Basic 인증 헤더로 전송</SelectItem><SelectItem value="none">공개 클라이언트 · 비밀 값 없이</SelectItem></SelectContent></Select><small>서비스의 OAuth 안내에 적힌 인증 방식을 선택하세요.</small></div>}
        {provider==='jwt'&&<><div className="field"><label htmlFor="jwt-algorithm">서비스에서 사용하는 서명 방식</label><Select value={jwtAlgorithm} onValueChange={value=>setData(previous=>({algorithm:value,purpose:previous.purpose||'verify'}))} disabled={saving}><SelectTrigger id="jwt-algorithm"><SelectValue/></SelectTrigger><SelectContent>{['HS256','HS384','HS512','RS256','RS384','RS512','PS256','PS384','PS512','ES256','ES384','ES512'].map(value=><SelectItem key={value} value={value}>{value}{value.startsWith('HS')?' · 같은 비밀 키 사용':' · 개인 키와 공개 키 사용'}</SelectItem>)}</SelectContent></Select><small>상대 서비스의 JWT 안내와 같은 방식을 선택하세요.</small></div>{!jwtAlgorithm.startsWith('HS')&&<div className="field"><label htmlFor="jwt-purpose">어디에 사용할까요?</label><Select value={jwtPurpose} onValueChange={value=>setData(previous=>({...previous,purpose:value}))} disabled={saving}><SelectTrigger id="jwt-purpose"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="verify">Webhook 요청의 서명 확인</SelectItem><SelectItem value="sign">Webhook 응답에 서명 추가</SelectItem></SelectContent></Select></div>}</>}
        {fields.map(field => <div className="field" key={service + field.key}>
          <label htmlFor={'credential-' + field.key}>{field.label}</label>
          {field.multiline?<textarea id={'credential-'+field.key} className="code-input" rows={6} autoComplete="off" spellCheck={false} value={data[field.key]||''} disabled={saving} onChange={event=>setData(previous=>({...previous,[field.key]:event.target.value}))} placeholder={field.placeholder} required/>:<input id={'credential-' + field.key} type={field.secret ? 'password' : 'text'} autoComplete={field.secret ? 'new-password' : 'off'} spellCheck={false} value={data[field.key] || ''} disabled={saving||loginPending} onChange={event => setData(previous => ({...previous, [field.key]: event.target.value}))} placeholder={field.placeholder} required={!optionalFields.includes(field.key)}/>}
          {field.help && <small>{field.help}</small>}
        </div>)}
        {error && <div className="error-box mb-4" role="alert">{error}</div>}
        <button type="submit" className="btn primary full" disabled={saving || loginPending || !canSave}>{saving||loginPending ? <Loader2 className="animate-spin"/> : <PlugZap/>}{saving ? '연결 준비 중…' : loginPending?'로그인 창에서 승인해주세요':useOAuth?service==='googleSheets'?'Google 로그인으로 연결':'로그인으로 연결':'연결 계정 저장'}</button>
        <p className="muted flex items-start gap-2 mt-4 mb-0! text-xs!"><ShieldCheck size={16} className="shrink-0 mt-1"/><span>비밀값은 서버에 암호화해 저장해요. 저장 후에는 화면에 다시 표시하지 않아요.</span></p>
      </form>

    </div>
    <section className="credential-section">
      <div className="panel-title">
        <div><h2>저장된 연결 계정</h2><p>연결 확인 결과를 살펴보거나 사용하지 않는 계정을 삭제하세요.</p></div>
        <button className="btn" onClick={() => void load()} disabled={loading || !!actionId}><RefreshCw className={loading ? 'animate-spin' : ''}/>새로고침</button>
      </div>
      {listError && <div className="error-box mb-4" role="alert">{listError}</div>}
      {loading && credentials.length === 0 ? <p className="muted flex items-center gap-2" role="status"><Loader2 size={17} className="animate-spin"/>연결 계정을 불러오는 중…</p> : credentials.length > 0 ? <Table>
        <TableHeader><TableRow><TableHead>연결 이름</TableHead><TableHead>서비스</TableHead><TableHead>연결 확인</TableHead><TableHead className="text-right">관리</TableHead></TableRow></TableHeader>
        <TableBody>{credentials.map(credential => {
          const result = testResults[credential.id];
          const ResultIcon = result?.ok ? CheckCircle2 : AlertCircle;
          return <TableRow key={credential.id}>
            <TableCell className="font-medium">{credential.name}</TableCell>
            <TableCell>{services[credential.service||credential.provider]?.label || credential.provider}{credential.authType==='oauth2'&&<small className="block muted mt-1">로그인 연결 · 자동 갱신</small>}</TableCell>
            <TableCell>{result ? <span className={`flex items-start gap-2 text-sm ${result.ok ? 'text-[#24734b]' : 'text-[#b33b53]'}`}><ResultIcon size={15} className="shrink-0 mt-1"/>{result.message}</span> : <span className="muted text-sm">아직 확인하지 않았어요</span>}</TableCell>
            <TableCell><div className="flex justify-end gap-2"><button className="btn" disabled={!!actionId} onClick={() => void test(credential)}>{actionId === credential.id ? <Loader2 className="animate-spin"/> : <PlugZap/>}연결 확인</button><button className="icon-btn" disabled={!!actionId} aria-label={`${credential.name} 연결 삭제`} onClick={() => setDeleteTarget(credential)}><Trash2 size={17}/></button></div></TableCell>
          </TableRow>;
        })}</TableBody>
      </Table> : !listError && <div className="empty-state py-10!"><KeyRound size={34}/><h3>아직 저장한 연결 계정이 없어요</h3><p>위에서 첫 계정을 추가하면 필요한 단계에 연결할 수 있어요.</p></div>}
    </section>
    <AlertDialog open={!!deleteTarget} onOpenChange={open => {if (!open && !actionId) setDeleteTarget(null);}}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>이 연결 계정을 삭제할까요?</AlertDialogTitle><AlertDialogDescription>‘{deleteTarget?.name}’을 사용하는 단계는 새 연결 계정을 선택해야 실제 실행할 수 있어요.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel disabled={!!actionId}>돌아가기</AlertDialogCancel><button className="btn primary" disabled={!!actionId} onClick={() => void remove()}>{actionId ? <Loader2 className="animate-spin"/> : <Trash2/>}{actionId ? '삭제 중…' : '계정 삭제'}</button></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>;
}
