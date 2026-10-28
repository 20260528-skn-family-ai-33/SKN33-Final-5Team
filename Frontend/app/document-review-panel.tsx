import {useEffect,useState} from 'react';
import {request} from '../components/flow/connection-panel';
import {blocksFor,type DocumentReviewView,type ReviewableAsset,type ReviewGroup} from '../lib/document-review';
import type {AnalysisBlock} from '../server/document-analysis-contract';
import {reasonLabel} from './document-analysis-summary';

const statusLabel={pending:'검토 대기',in_progress:'검토 중',deferred:'보류 있음',completed:'사람 검토 완료'};
function groupLabel(group:ReviewGroup){
 const loc=group.native_location;
 if(group.scope.kind==='document')return '문서 전체';
 if(loc.converted_page)return `변환 PDF ${loc.converted_page}쪽`;
 if(loc.page)return `${loc.page}쪽`;
 if(loc.slide)return `슬라이드 ${loc.slide}`;
 if(loc.sheet)return `${loc.sheet} 시트의 이미지`;
 if(loc.frame)return `이미지 ${loc.frame}프레임`;
 return '이미지 객체';
}
export function ReviewBlocks({blocks}:{blocks:AnalysisBlock[]}){
 if(!blocks.length)return <p className="as-small-muted">이 범위에 추출된 내용이 없습니다.</p>;
 return <>{blocks.map(block=>{
  const cells=Array.isArray(block.data.cells)?block.data.cells as Record<string,unknown>[]:[];
  return <div className="dr-block" key={block.id}>
   <small>{block.engine} · {block.content_types.includes('table')?'표':'본문'}</small>
   {cells.length?<div className="as-table-scroll"><table><thead><tr><th>행·열</th><th>셀 내용</th><th>행·열 병합</th></tr></thead><tbody>{cells.map((cell,index)=><tr key={index}>
    <td>{Number(cell.start_row_offset_idx??cell.row??0)+1} · {Number(cell.start_col_offset_idx??cell.column??0)+1}</td>
    <td className="dr-cell">{String(cell.text??'')}</td><td>{String(cell.row_span??1)} · {String(cell.col_span??1)}</td>
   </tr>)}</tbody></table></div>:<p className="as-preserve">{block.text}</p>}
  </div>;
 })}</>;
}

export function DocumentReviewPanel({assetId,onUpdated,disabled=false}:{assetId:string;onUpdated:(asset:ReviewableAsset)=>void;disabled?:boolean}){
 const [open,setOpen]=useState(false),[view,setView]=useState<DocumentReviewView|null>(null),[loading,setLoading]=useState(false),[saving,setSaving]=useState(false);
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[notes,setNotes]=useState<Record<string,string>>({}),[note,setNote]=useState(''),[acknowledge,setAcknowledge]=useState(false);
 async function load(){
  setLoading(true);setError('');
  try{const result=await request('/api/agent-builder/document_review?id='+encodeURIComponent(assetId)) as DocumentReviewView;
   setView(result);setNotes(Object.fromEntries(Object.entries(result.review.decisions).map(([id,c])=>[id,c.note])));setNote(result.review.note);setAcknowledge(false);
  }catch(error){setError((error as Error).message);}finally{setLoading(false);}
 }
 useEffect(()=>{if(open)void load();},[open,assetId]);
 async function save(action:'decide'|'complete'|'reopen',group?:ReviewGroup,decision?:'adopt'|'keep'|'hold'){
  if(!view||saving||disabled)return;setSaving(true);setError('');setNotice('');
  try{
   const result=await request('/api/agent-builder/document_review',{id:assetId,expectedRevision:view.review.revision,requestId:crypto.randomUUID(),
    sourceSha256:view.review.source_sha256,analysisSha256:view.review.analysis_sha256,action,
    ...(group?{groupId:group.id,decision}:{}),note:group?notes[group.id]||'':note,acknowledge}) as DocumentReviewView;
   setView(result);onUpdated(result.asset);setAcknowledge(false);
   setNotice(action==='complete'?'검토 완료를 저장했습니다.':action==='reopen'?'검토를 다시 열었습니다.':'결정을 저장하고 현재 사용 결과에 반영했습니다.');
  }catch(error){setError((error as Error).message);}finally{setSaving(false);}
 }
 const completed=view?.review.status==='completed',locked=disabled||saving||loading;
 const unresolved=view?.groups.filter(group=>!['adopt','keep'].includes(group.decision)).length||0;
 const original='/api/agent-builder/original?id='+encodeURIComponent(assetId);
 const format=view?.asset.format||'';
 const image=['jpg','jpeg','png','webp','bmp'].includes(format);
 const converted=!!view?.asset.parser_metadata.document_analysis.conversions?.[0]?.artifact.endsWith('.pdf');
 return <section className="dr-panel" aria-label="문서 결과 검토">
  <button type="button" className="as-button" onClick={()=>setOpen(!open)} aria-expanded={open}>{open?'비교 화면 접기':'원본·대조 후보 검토'}</button>
  {open&&<>
   {loading&&<p role="status">검토 내용을 불러오고 있습니다.</p>}
   {error&&<div role="alert"><p className="as-validation">{error}</p><button className="as-button" disabled={locked} onClick={()=>void load()}>최신 검토 다시 불러오기</button></div>}
   {view&&<>
    <div className="dr-heading"><strong>{statusLabel[view.review.status]}</strong><small>저장 버전 {view.review.revision}</small><a href={original} target="_blank" rel="noreferrer">원본 파일 열기</a></div>
    <p className="as-small-muted">후보 채택은 표시된 범위의 기본 추출 내용을 교체합니다. 원본 파일과 최초 파서 출력·주의사항은 보존됩니다.</p>
    {image?<details><summary>전체 원본 이미지 보기</summary><a href={original} target="_blank" rel="noreferrer"><img className="dr-source" src={original} alt="검토할 원본 이미지"/></a></details>:
     format==='pdf'||converted?<iframe className="dr-pdf" title="검토할 PDF" src={format==='pdf'?original:'/api/agent-builder/document_preview?id='+encodeURIComponent(assetId)+'&converted=0'}/>:
     <p className="as-small-muted">이 형식은 원본 파일을 내려받아 확인하세요. 아래 기본 추출 내용과 후보를 비교할 수 있습니다.</p>}
    {!view.groups.length&&<p>대조 후보가 없습니다. 원본과 추출 내용·주의사항을 확인한 뒤 검토를 완료할 수 있습니다.</p>}
    {view.groups.map((group,index)=><article key={group.id} className="dr-group" aria-label={'대조 후보 '+(index+1)}>
     <div className="dr-heading"><strong>{groupLabel(group)} · {group.engine} 후보</strong><span>{({pending:'결정 전',adopt:'후보 채택',keep:'기본 결과 유지',hold:'보류'})[group.decision]}</span></div>
     {group.scope.kind==='image'&&!image&&<a href={'/api/agent-builder/document_preview?id='+encodeURIComponent(assetId)+'&blockId='+encodeURIComponent(group.scope.object_id!)} target="_blank" rel="noreferrer">해당 이미지 원본 보기</a>}
     {Array.from(new Set(blocksFor(view,group.candidate_block_ids).flatMap(block=>Array.isArray(block.data.context_for_regions)?block.data.context_for_regions.filter((id):id is string=>typeof id==='string'):[]))).map(id=>
      <a key={id} href={'/api/agent-builder/document_preview?id='+encodeURIComponent(assetId)+'&blockId='+encodeURIComponent(id)} target="_blank" rel="noreferrer"><img className="dr-source-region" src={'/api/agent-builder/document_preview?id='+encodeURIComponent(assetId)+'&blockId='+encodeURIComponent(id)} alt="검토할 표 영역 원본"/></a>)}
     <div className="dr-columns"><section aria-label="기본 추출 내용"><h4>기본 추출 내용</h4><ReviewBlocks blocks={blocksFor(view,group.base_block_ids)}/></section>
      <section aria-label="대조 후보 내용"><h4>대조 후보 내용</h4><ReviewBlocks blocks={blocksFor(view,group.candidate_block_ids)}/></section></div>
     <label className="as-field">이 후보의 검토 메모<textarea aria-label={'후보 '+(index+1)+' 검토 메모'} maxLength={2000} rows={2} disabled={locked||completed} value={notes[group.id]||''} onChange={event=>setNotes({...notes,[group.id]:event.target.value})}/></label>
     <div className="as-run-actions"><button className="as-button as-primary" disabled={locked||completed} onClick={()=>void save('decide',group,'adopt')}>후보 채택·저장</button>
      <button className="as-button" disabled={locked||completed} onClick={()=>void save('decide',group,'keep')}>기본 결과 유지·저장</button>
      <button className="as-button" disabled={locked||completed} onClick={()=>void save('decide',group,'hold')}>보류·저장</button></div>
    </article>)}
    {view.asset.parser_metadata.document_analysis.review_reasons.length>0&&<details open><summary>파서가 남긴 주의사항</summary><ul>{view.asset.parser_metadata.document_analysis.review_reasons.map(reason=><li key={reason}>{reasonLabel(reason)}</li>)}</ul></details>}
    <label className="as-field">문서 검토 메모<textarea aria-label="문서 검토 메모" rows={2} maxLength={2000} value={note} disabled={locked||completed} onChange={event=>setNote(event.target.value)}/></label>
    {completed?<><p>검토 완료: {new Date(view.review.completed_at!).toLocaleString('ko-KR')} · 원래 파서 상태는 그대로 보존됩니다.</p><button className="as-button" disabled={locked} onClick={()=>void save('reopen')}>검토 다시 열기</button></>:
     <><label className="dr-check"><input type="checkbox" checked={acknowledge} disabled={locked} onChange={event=>setAcknowledge(event.target.checked)}/>원본과 파서 주의사항을 확인했습니다.</label>
      {unresolved>0&&<p className="as-validation">결정 전·보류 후보 {unresolved}개를 채택 또는 기본 결과 유지로 결정하세요.</p>}
      <button className="as-button as-primary" disabled={locked||!acknowledge||unresolved>0} onClick={()=>void save('complete')}>검토 완료·저장</button></>}
    <details><summary>저장 이력</summary>{view.history.length?view.history.map(event=><p key={event.revision}>버전 {event.revision} · {new Date(event.at).toLocaleString('ko-KR')} · {event.action==='complete'?'검토 완료':event.action==='reopen'?'검토 다시 열기':({adopt:'후보 채택',keep:'기본 결과 유지',hold:'보류'} as Record<string,string>)[event.decision||'']} {event.note&&'· '+event.note}</p>):<p>아직 저장된 검토 결정이 없습니다.</p>}</details>
   </>}
   {notice&&<p role="status">{notice}</p>}
  </>}
 </section>;
}
