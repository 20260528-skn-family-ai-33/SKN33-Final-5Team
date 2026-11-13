import {createContext,useCallback,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {LogIn,LogOut,ShieldCheck,ArrowLeft,UserRound,Monitor} from 'lucide-react';
import './auth.css';

export type Member={id:string;email:string;name:string;role:'admin'|'member'};
type AuthState={loading:boolean;enabled:boolean;user:Member|null;error:string;refresh:()=>Promise<void>};
const AuthContext=createContext<AuthState>({loading:false,enabled:false,user:null,error:'',refresh:async()=>{}});
export const useAuth=()=>useContext(AuthContext);
function clearMemberNavigation(){
 try{for(const key of Object.keys(localStorage))if(['a4b-builder-session','a4b-selected-agent'].includes(key)||key.startsWith('a4b-chat-session-'))localStorage.removeItem(key);}catch{}
}
export function AuthProvider({children}:{children:ReactNode}){
 const [state,setState]=useState<Omit<AuthState,'refresh'>>({loading:true,enabled:true,user:null,error:''});
 const generation=useRef(0),member=useRef<string|null|undefined>(undefined);
 const refresh=useCallback(async()=>{
  const current=++generation.current;
  try{
   const response=await fetch('/api/auth/me',{credentials:'same-origin',cache:'no-store'}),value=await response.json();
   if(!response.ok)throw new Error(value.error||'로그인 상태를 확인하지 못했습니다.');
   if(typeof value.enabled!=='boolean'||typeof value.authenticated!=='boolean'||value.authenticated&&!value.user?.id)throw new Error('로그인 상태를 확인하지 못했습니다.');
   if(current!==generation.current)return;
   const id=value.user?.id||null;
   if(value.enabled){
    let previous:string|null=null;try{previous=localStorage.getItem('a4b-auth-member');}catch{}
    if((member.current===undefined?previous:member.current)!==id)clearMemberNavigation();
    try{if(id)localStorage.setItem('a4b-auth-member',id);else localStorage.removeItem('a4b-auth-member');}catch{}
   }
   member.current=id;setState({loading:false,enabled:value.enabled,user:value.user||null,error:''});
  }catch(error){if(current===generation.current)setState({loading:false,enabled:true,user:null,error:(error as Error).message});}
 },[]);
 useEffect(()=>{
  void refresh();const focus=()=>void refresh();
  const expired=()=>{generation.current++;member.current=null;clearMemberNavigation();setState({loading:false,enabled:true,user:null,error:'로그인이 만료되었습니다. 다시 로그인하세요.'});};
  const storage=(event:StorageEvent)=>{if(event.key==='a4b-auth-member')void refresh();};
  window.addEventListener('focus',focus);window.addEventListener('storage',storage);window.addEventListener('a4b:unauthenticated',expired);
  const timer=setInterval(focus,60000);
  return()=>{generation.current++;clearInterval(timer);window.removeEventListener('focus',focus);window.removeEventListener('storage',storage);window.removeEventListener('a4b:unauthenticated',expired);};
 },[refresh]);
 return <AuthContext.Provider value={{...state,refresh}}><div key={state.user?.id||'signed-out'}>{children}</div></AuthContext.Provider>;
}
export function loginUrl(){
 const target=new URL(location.href),screen=target.searchParams.get('screen');
 if(!screen||['intro','preview'].includes(screen))target.searchParams.set('screen','home');
 target.searchParams.delete('auth_error');
 return '/auth/login?returnTo='+encodeURIComponent(target.pathname+target.search);
}
export function AccountMenu({showLocalStatus=false}:{showLocalStatus?:boolean}){
 const auth=useAuth(),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function logout(){
  if(busy)return;setBusy(true);setError('');
  try{
   // A JSON fetch keeps a same-origin Origin header with the app's no-referrer policy.
   const response=await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'}),value=await response.json();
   if(!response.ok)throw new Error(value.error||'로그아웃하지 못했습니다. 다시 시도하세요.');
   if(typeof value.logoutUrl!=='string'||new URL(value.logoutUrl).protocol!=='https:')throw new Error('로그아웃 주소를 확인하지 못했습니다.');
   clearMemberNavigation();try{localStorage.removeItem('a4b-auth-member');}catch{}
   window.location.assign(value.logoutUrl);
  }catch(error){setError((error as Error).message);setBusy(false);}
 }
 if(auth.loading)return null;
 if(!auth.enabled)return showLocalStatus?<span className="a4b-account-local" title="로컬 미리보기는 로그인 없이 실행됩니다."><Monitor size={15} aria-hidden="true"/>로그인 계정 없음</span>:null;
 if(!auth.user)return <a className="a4b-account-button" href={loginUrl()}><LogIn size={15} aria-hidden="true"/>로그인</a>;
 return <div className="a4b-account-menu"><span className="a4b-account-identity" title={auth.user.email}><UserRound size={16} aria-hidden="true"/><span className="a4b-account-label">로그인 계정</span><span className="a4b-account-name">{auth.user.name}</span></span><button className="a4b-account-button" type="button" disabled={busy} onClick={()=>void logout()}><LogOut size={15} aria-hidden="true"/>{busy?'로그아웃 중…':'로그아웃'}</button>{error&&<span role="alert">{error}</span>}</div>;
}
export function SignIn({onHome}:{onHome:()=>void}){
 const auth=useAuth();const reason=new URLSearchParams(location.search).get('auth_error');
 return <main className="a4b-auth-page"><section className="a4b-auth-card" aria-labelledby="auth-title"><span className="workspace-brandmark"><img src="/brand/a4b-logo.png" width="1254" height="1254" alt="A4B"/></span><h1 id="auth-title">내 작업 공간에 로그인</h1><p>문서와 워크플로우를 저장하고,<br/>이전 작업을 이어서 진행하세요.</p>
 {(auth.error||reason)&&<div className="a4b-auth-error" role="alert">{auth.error|| (reason==='account_disabled'?'서비스 이용이 중지된 계정입니다. 관리자에게 문의하세요.':'로그인을 완료하지 못했습니다. 다시 시도해주세요.')}</div>}
 {auth.loading?<p role="status">로그인 상태를 확인하고 있습니다.</p>:<a className="a4b-auth-primary" href={loginUrl()}><LogIn size={18} aria-hidden="true"/>로그인 · 회원가입</a>}
 <p className="a4b-auth-note"><ShieldCheck size={15} aria-hidden="true"/>내 자료는 내 계정의 작업 공간에 보관됩니다.</p>
 <button className="a4b-auth-home" onClick={onHome}><ArrowLeft size={15} aria-hidden="true"/>서비스 소개로 돌아가기</button></section></main>;
}
export function AuthNotice(){
 const reason=new URLSearchParams(location.search).get('auth_error');if(!reason)return null;
 return <div className="a4b-auth-notice" role="alert">{reason==='account_disabled'?'서비스 이용이 중지된 계정입니다. 관리자에게 문의하세요.':'로그인을 완료하지 못했습니다. 다시 시도해주세요.'} <a href="/auth/login">다시 로그인</a></div>;
}
