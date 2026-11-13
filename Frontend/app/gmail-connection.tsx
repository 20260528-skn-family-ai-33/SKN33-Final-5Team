import {useEffect, useRef, useState} from 'react';
import {Mail} from 'lucide-react';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger} from '@/components/ui/dialog';
import './service-connections.css';

type GmailStatus = {configured: boolean; connected: boolean; connectionVersion: number; email: string | null; accountError?: string};
type GmailAttempt = {authorizationUrl: string; connectionVersion: number; expiresAt: number};
const endpoint = '/api/agent-builder/gmail';

async function gmailRequest<T>(path = endpoint, method = 'GET'): Promise<T> {
  const response = await fetch(path, {method, credentials: 'same-origin', cache: 'no-store'});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Gmail 설정을 확인하지 못했어요.');
  return value as T;
}

export function GmailConnection({disabled, variant = 'button', onStatusChange}: {disabled: boolean; variant?: 'button' | 'card'; onStatusChange?:(connected:boolean)=>void}) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<GmailStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [verified, setVerified] = useState(false);
  const [attempt, setAttempt] = useState<GmailAttempt>();
  const popup = useRef<Window | null>(null);
  const statusRequest = useRef(0);
  const statusPending = useRef<{request:number;promise:Promise<void>} | null>(null);
  const mutation = useRef(false);

  async function refresh() {
    if (mutation.current) return;
    if (statusPending.current?.request === statusRequest.current) return statusPending.current.promise;
    const request = ++statusRequest.current;
    const promise = (async () => {
      try {
        const current = await gmailRequest<GmailStatus>();
        if (request === statusRequest.current) { setStatus(current); setError(''); setVerified(false); }
      } catch (err) { if (request === statusRequest.current) setError((err as Error).message); }
    })().finally(() => { if (statusPending.current?.request === request) statusPending.current = null; });
    statusPending.current = {request,promise};
    return promise;
  }

  useEffect(() => {
    if (variant !== 'card') return;
    void refresh();
    const resume = () => { if (document.visibilityState !== 'hidden') void refresh(); };
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    return () => { window.removeEventListener('focus', resume); document.removeEventListener('visibilitychange', resume); };
  }, [variant]);
  useEffect(() => () => { statusRequest.current++; }, []);
  useEffect(() => { onStatusChange?.(!error && !status?.accountError && status?.connected === true); }, [error, status?.connected, status?.accountError, onStatusChange]);

  useEffect(() => {
    if (!open) return;
    void refresh();
    let active = true;
    function finish() {
      if (mutation.current) return;
      statusRequest.current++;
      popup.current?.close();
      popup.current = null;
      try { sessionStorage.removeItem('a4b-gmail-return'); } catch {}
      setAttempt(undefined);
      setOpen(false);
      void refresh();
    }
    function receive(event: MessageEvent) {
      if (event.origin !== location.origin || !popup.current || event.source !== popup.current || event.data?.type !== 'easyflow-oauth') return;
      if (event.data.ok) finish();
      else {
        statusRequest.current++;
        popup.current.close();
        popup.current = null;
        setAttempt(undefined);
        setError('Google에서 Gmail 연결을 완료하지 못했어요. 설정을 확인하고 다시 연결해주세요.');
      }
    }
    window.addEventListener('message', receive);
    const timer = attempt && window.setInterval(async () => {
      if (Date.now() >= attempt.expiresAt) {
        setAttempt(undefined);
        setError('Google 연결 요청이 만료됐어요. 다시 연결해주세요.');
        return;
      }
      try {
        const current = await gmailRequest<GmailStatus>();
        if (active && current.connected && current.connectionVersion > attempt.connectionVersion) finish();
      } catch {}
    }, 1200);
    return () => {
      active = false;
      if (timer !== undefined) window.clearInterval(timer);
      window.removeEventListener('message', receive);
    };
  }, [open, attempt]);

  async function connect() {
    if (disabled || mutation.current) return;
    mutation.current = true;
    statusRequest.current++;
    setOpen(true);
    setError('');
    setVerified(false);
    setAttempt(undefined);
    try { sessionStorage.setItem('a4b-gmail-return', location.pathname + location.search + location.hash); } catch {}
    let login: Window | null = null;
    try { login = window.open('about:blank', 'a4b-gmail-oauth', 'popup,width=560,height=720'); } catch {}
    popup.current = login;
    setBusy(true);
    try {
      const pending = await gmailRequest<GmailAttempt>(endpoint, 'POST');
      setAttempt(pending);
      try { login?.location.replace(pending.authorizationUrl); } catch { login?.close(); }
    } catch (err) {
      login?.close();
      popup.current = null;
      setError((err as Error).message);
    } finally { mutation.current = false; setBusy(false); }
  }

  async function manage(action: 'test' | 'disconnect') {
    if (disabled || mutation.current) return;
    mutation.current = true;
    if (action === 'disconnect') {
      popup.current?.close();
      popup.current = null;
      try { sessionStorage.removeItem('a4b-gmail-return'); } catch {}
    }
    const request = ++statusRequest.current;
    setBusy(true);
    setError('');
    setVerified(false);
    setAttempt(undefined);
    try {
      if (action === 'test') {
        const result = await gmailRequest<{email: string}>(endpoint + '/test', 'POST');
        if (request === statusRequest.current) {
          setStatus(current => current && {...current, email: result.email, accountError: undefined});
          setVerified(true);
        }
      } else {
        await gmailRequest(endpoint, 'DELETE');
        if (request === statusRequest.current) setStatus(current => current && {...current, connected: false, email: null, accountError: undefined});
      }
    } catch (err) { setError((err as Error).message); }
    finally { mutation.current = false; setBusy(false); }
  }

  return <Dialog open={open} onOpenChange={value => {
    setOpen(value);
    if (value) { setError(''); setVerified(false); }
    else setAttempt(undefined);
  }}>
    {variant === 'card' ? <article className="service-connection-card" aria-label="Gmail 서비스">
      <div className="service-connection-card-heading">
        <span className="service-connection-logo"><Mail size={22} aria-hidden="true"/></span><h3>Gmail</h3>
        <span className={'service-connection-state' + (error || status?.accountError ? ' attention' : status?.connected ? ' connected' : '')} role="status">
          {error ? '상태 확인 필요' : !status ? '확인 중' : status.accountError ? '연결 확인 필요' : status.connected ? '연결됨' : status.configured ? '연결 안 됨' : '설정 대기'}
        </span>
      </div>
      {status?.connected && <div className="service-connection-account">
        <span>연결된 계정</span><strong>{status.email || '계정 정보를 확인하지 못했어요.'}</strong>
      </div>}
      <p>메일과 첨부파일을 읽고 내용을 요약하세요.</p>
      <span className="service-connection-permissions">읽기 전용</span>
      {error && !open && <p role="alert">{error}</p>}
      {!open && status?.accountError && <p className="gmail-account-notice" role="status">{status.accountError}</p>}
      {!open && verified && status?.connected && <p className="gmail-account-notice" role="status">Gmail 읽기 권한을 확인했어요.</p>}
      {status && !status.configured && !status.connected && <p className="gmail-account-notice">서버의 Gmail 연결 설정이 필요합니다. 설정 후 Google 계정을 연결할 수 있어요.</p>}
      <div className="service-connection-actions">
        {status?.connected
          ? <DialogTrigger asChild><button type="button" className="as-button service-connection-manage" disabled={disabled || busy} aria-label="Gmail 연결 관리">연결 관리</button></DialogTrigger>
          : <button type="button" className="as-button as-primary" disabled={disabled || busy || !status?.configured} onClick={() => void connect()} aria-label="Gmail 계정 연결">Google에서 연결</button>}
      </div>
    </article> : <DialogTrigger asChild><button type="button" className="as-text-button" disabled={disabled}>
      <Mail size={16} aria-hidden="true"/>Gmail
    </button></DialogTrigger>}
    <DialogContent className="as-dialog">
      <DialogHeader>
        <DialogTitle>Gmail 연결</DialogTitle>
        <DialogDescription>Google에서 읽기 권한을 허용하면 워크플로우에서 메일과 첨부파일을 읽고 요약할 수 있어요.</DialogDescription>
      </DialogHeader>
      {status?.connected && <div className="gmail-connected-account">
        <Mail size={20} aria-hidden="true"/><div><span>연결된 계정</span><strong>{status.email || '계정 정보를 확인하지 못했어요.'}</strong></div>
      </div>}
      {verified && status?.connected && <p role="status">Gmail 읽기 권한을 확인했어요.</p>}
      {status?.connected && status.accountError && <p className="gmail-account-notice" role="status">{status.accountError}</p>}
      {status && !status.configured && !status.connected && <p>서버에 Gmail OAuth 앱을 등록하면 연결할 수 있어요.</p>}
      {!status && !error && <p role="status">연결 상태를 확인하고 있어요.</p>}
      {error && <p role="alert">{error}</p>}
      {attempt && <a href={attempt.authorizationUrl} className="as-text-button" referrerPolicy="no-referrer">팝업이 보이지 않으면 이 탭에서 Google 승인 계속</a>}
      <div className="service-connection-dialog-actions">
        {status?.configured && <button type="button" className="as-button as-primary" disabled={disabled || busy} onClick={() => void connect()}>
          {status.connected ? '계정 다시 연결' : 'Google에서 연결'}
        </button>}
        {status?.connected && <>
          <button type="button" className="as-button" disabled={disabled || busy} onClick={() => void manage('test')}>연결 확인</button>
        </>}
        <button type="button" className="as-text-button" disabled={disabled || busy} onClick={() => void refresh()}>상태 새로고침</button>
        {status?.connected && <button type="button" className="as-text-button service-connection-disconnect" disabled={disabled || busy} onClick={() => void manage('disconnect')}>A4B 연결 해제</button>}
      </div>
    </DialogContent>
  </Dialog>;
}
