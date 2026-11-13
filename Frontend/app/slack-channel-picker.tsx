import {useEffect,useState,type FormEvent} from 'react';
import {request} from '@/components/flow/connection-panel';
import './slack-channel-picker.css';

type Channel={id:string;name:string;isPrivate:boolean};
type ChannelResult={channels:Channel[];truncated:boolean};

export function SlackChannelPicker({disabled=false,onSelect,onReconnect}:{
 disabled?:boolean;
 onSelect:(channelId:string)=>void|Promise<void>;
 onReconnect:()=>void;
}){
 const [channels,setChannels]=useState<Channel[]>([]);
 const [truncated,setTruncated]=useState(false);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const [errorCode,setErrorCode]=useState('');
 const [reload,setReload]=useState(0);
 const [search,setSearch]=useState('');
 const [selected,setSelected]=useState('');
 const [manual,setManual]=useState(false);
 const [manualId,setManualId]=useState('');
 const [submitting,setSubmitting]=useState(false);

 useEffect(()=>{
  const abort=new AbortController();
  setLoading(true);setError('');setErrorCode('');
  void request<ChannelResult>('/api/service-connections/slack/channels',undefined,undefined,abort.signal)
   .then(result=>{if(abort.signal.aborted)return;setChannels(result.channels);setTruncated(result.truncated);})
   .catch((cause:Error & {cause?:{code?:string}})=>{
    if(abort.signal.aborted)return;
    setError(cause.message);setErrorCode(cause.cause?.code||'');
   })
   .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
  return()=>abort.abort();
 },[reload]);

 const available=channels.filter(channel=>channel.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
 const channelId=manual?manualId.trim().toUpperCase():selected;
 const valid=/^[CGD][A-Z0-9]{8,24}$/.test(channelId);
 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();
  if(disabled||submitting||!valid)return;
  setSubmitting(true);
  try{await onSelect(channelId);}finally{setSubmitting(false);}
 }
 return <form className="slack-channel-picker" aria-label="Slack 채널 선택" onSubmit={submit}>
  <strong>요약을 보낼 Slack 채널을 선택하세요.</strong>
  <p>연결된 Slack 봇이 참여한 채널을 표시합니다. 계정 연결만으로는 전송할 채널이 정해지지 않습니다.</p>
  {loading?<p role="status">Slack 채널을 확인하고 있습니다…</p>:error?<div role="status" className="slack-channel-picker-status"><p>{error}</p>{['slack_scope_required','slack_not_connected','slack_reconnect_required'].includes(errorCode)&&<button type="button" className="as-button" onClick={onReconnect}>서비스 연결 열기</button>}<button type="button" className="as-button" onClick={()=>setReload(value=>value+1)}>다시 확인</button></div>:channels.length?<>
   <label>채널 검색<input type="search" value={search} onChange={event=>{setSearch(event.target.value);setSelected('');}} disabled={disabled||submitting||manual} placeholder="채널 이름 검색"/></label>
   <label>전송할 채널<select aria-label="전송할 Slack 채널" value={selected} onChange={event=>setSelected(event.target.value)} disabled={disabled||submitting||manual}>
    <option value="">채널을 선택하세요</option>
    {available.map(channel=><option key={channel.id} value={channel.id}>{channel.isPrivate?'🔒 ':''}#{channel.name}</option>)}
   </select></label>
   {truncated&&<p>표시 가능한 채널 일부만 보여줍니다. 채널 이름을 검색하거나 ID를 직접 입력하세요.</p>}
  </>:<p role="status">전송 가능한 채널을 찾지 못했습니다. Slack에서 봇을 채널에 초대한 뒤 다시 확인하세요.</p>}
  <label className="slack-channel-picker-manual"><input type="checkbox" checked={manual} onChange={event=>setManual(event.target.checked)} disabled={disabled||submitting}/>채널 ID 직접 입력</label>
  {manual&&<label>Slack 채널 ID<input aria-label="Slack 채널 ID 직접 입력" value={manualId} onChange={event=>setManualId(event.target.value)} disabled={disabled||submitting} placeholder="C0123456789"/></label>}
  <button type="submit" className="as-button as-primary" disabled={disabled||submitting||!valid}>{submitting?'적용 중…':'이 채널로 설정'}</button>
 </form>;
}
