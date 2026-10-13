"use client";
import {Plus,Trash2} from 'lucide-react';
import {Select,SelectContent,SelectItem,SelectTrigger,SelectValue} from '@/components/ui/select';
import {Switch} from '@/components/ui/switch';
import {clone,type Json} from '@/lib/workflow';

export function HttpSettings({parameters,onChange}:{parameters:Json;onChange:(parameters:Json)=>void}) {
 const pagination=parameters.options?.pagination?.pagination||{};
 const response=parameters.options?.response?.response||{};
 const mode=pagination.paginationMode||'off';
 const rows=Array.isArray(pagination.parameters?.parameters)?pagination.parameters.parameters:[];
 function updatePagination(values:Json) {
  const next=clone(parameters);next.options??={};next.options.pagination??={};
  next.options.pagination.pagination={...pagination,...values};onChange(next);
 }
 function updateResponse(values:Json) {
  const next=clone(parameters);next.options??={};next.options.response??={};
  next.options.response.response={...response,...values};onChange(next);
 }
 function updateRow(index:number,values:Json) {
  const next=clone(rows);next[index]={...next[index],...values};updatePagination({parameters:{...pagination.parameters,parameters:next}});
 }
 return <>
  <div className="section-label">여러 페이지의 데이터 가져오기</div>
  <div className="field"><label htmlFor="http-pagination-mode">다음 페이지를 찾는 방법</label>
   <Select value={mode} onValueChange={paginationMode=>updatePagination({paginationMode,...(paginationMode==='responseContainsNextURL'&&!pagination.nextURL?{nextURL:'={{ $response.body.next }}'}:{}),...(paginationMode==='updateAParameterInEachRequest'&&!rows.length?{parameters:{parameters:[{type:'qs',name:'page',value:'={{ $pageCount + 1 }}'}]}}:{}),paginationCompleteWhen:pagination.paginationCompleteWhen||(paginationMode==='responseContainsNextURL'?'other':'responseIsEmpty'),...(paginationMode==='responseContainsNextURL'&&!pagination.completeExpression?{completeExpression:'={{ !$response.body.next }}'}:{}),limitPagesFetched:pagination.limitPagesFetched??true,maxRequests:pagination.maxRequests??100})}>
    <SelectTrigger id="http-pagination-mode"><SelectValue/></SelectTrigger><SelectContent>
     <SelectItem value="off">한 번만 가져오기</SelectItem><SelectItem value="responseContainsNextURL">응답에 있는 다음 페이지 주소</SelectItem><SelectItem value="updateAParameterInEachRequest">페이지 번호·커서 바꾸기</SelectItem>
    </SelectContent>
   </Select>
  </div>
  {mode!=='off'&&<>
   {mode==='responseContainsNextURL'?<div className="field"><label htmlFor="http-next-url">다음 페이지 주소가 있는 항목</label><input id="http-next-url" value={String(pagination.nextURL??'')} placeholder="={{ $response.body.next }}" onChange={event=>updatePagination({nextURL:event.target.value})}/><small>상대 주소도 사용할 수 있어요. 다음 주소가 없을 때 참이 되는 종료 조건을 아래에 지정해주세요.</small></div>:<>
    {rows.map((row:Json,index:number)=><div className="assignment" key={index}>
     <div className="assignment-head"><b>{index+1}번째 변경 항목</b><button className="icon-btn ml-auto" aria-label={`${index+1}번째 페이지 항목 삭제`} onClick={()=>updatePagination({parameters:{...pagination.parameters,parameters:rows.filter((_:Json,i:number)=>i!==index)}})}><Trash2/></button></div>
     <div className="field"><label htmlFor={`http-page-location-${index}`}>항목을 보낼 곳</label><Select value={row.type||'qs'} onValueChange={type=>updateRow(index,{type})}><SelectTrigger id={`http-page-location-${index}`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="qs">주소의 쿼리 값</SelectItem><SelectItem value="body">보낼 JSON 데이터</SelectItem><SelectItem value="headers">요청 헤더</SelectItem></SelectContent></Select></div>
     <div className="field"><label htmlFor={`http-page-name-${index}`}>항목 이름</label><input id={`http-page-name-${index}`} value={String(row.name??'')} placeholder="page" onChange={event=>updateRow(index,{name:event.target.value})}/></div>
     <div className="field"><label htmlFor={`http-page-value-${index}`}>매번 보낼 값</label><input id={`http-page-value-${index}`} value={String(row.value??'')} placeholder="={{ $pageCount + 1 }}" onChange={event=>updateRow(index,{value:event.target.value})}/></div>
    </div>)}
    <button className="btn ghost full" onClick={()=>updatePagination({parameters:{...pagination.parameters,parameters:[...rows,{type:'qs',name:'',value:''}]}})}><Plus/>페이지 변경 항목 추가</button>
    <small className="field-help">{'={{ $pageCount + 1 }}'}은 1, 2, 3…으로 늘어나요. 앞 응답의 커서는 {'={{ $response.body.next_cursor }}'}처럼 가져와요.</small>
   </>}
   <div className="field"><label htmlFor="http-page-stop">언제 가져오기를 끝낼까요?</label><Select value={pagination.paginationCompleteWhen||'responseIsEmpty'} onValueChange={paginationCompleteWhen=>updatePagination({paginationCompleteWhen,...(paginationCompleteWhen==='receiveSpecificStatusCodes'&&!pagination.statusCodesWhenComplete?{statusCodesWhenComplete:'204'}:{}),...(paginationCompleteWhen==='other'&&!pagination.completeExpression?{completeExpression:'={{ !$response.body.next }}'}:{})})}><SelectTrigger id="http-page-stop"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="responseIsEmpty">응답이 비었을 때</SelectItem><SelectItem value="receiveSpecificStatusCodes">지정한 응답 번호를 받았을 때</SelectItem><SelectItem value="other">종료 조건이 맞을 때</SelectItem></SelectContent></Select></div>
   {pagination.paginationCompleteWhen==='receiveSpecificStatusCodes'&&<div className="field"><label htmlFor="http-page-status">종료할 응답 번호</label><input id="http-page-status" value={String(pagination.statusCodesWhenComplete??'204')} placeholder="204,404" onChange={event=>updatePagination({statusCodesWhenComplete:event.target.value})}/></div>}
   {pagination.paginationCompleteWhen==='other'&&<div className="field"><label htmlFor="http-page-complete">종료 조건</label><input id="http-page-complete" value={String(pagination.completeExpression??'')} placeholder="={{ !$response.body.next }}" onChange={event=>updatePagination({completeExpression:event.target.value})}/><small>조건이 참이 되는 응답까지 받은 뒤 끝나요.</small></div>}
   <div className="field"><label htmlFor="http-page-limit">최대 페이지 수</label><input id="http-page-limit" type="number" min="1" max="1000" value={pagination.maxRequests??100} onChange={event=>updatePagination({limitPagesFetched:true,maxRequests:event.target.value===''?'':Number(event.target.value)})}/><small>지정한 페이지까지만 가져와요. 이후 페이지가 있으면 실행 결과에 안내해요. 전체 결과는 5,000개 항목·4MB까지 지원해요.</small></div>
   <div className="field"><label htmlFor="http-page-delay">페이지 사이 대기 (밀리초)</label><input id="http-page-delay" type="number" min="0" max="60000" value={pagination.requestInterval??0} onChange={event=>updatePagination({requestInterval:event.target.value===''?'':Number(event.target.value)})}/><small>1,000밀리초는 1초예요. 서비스의 호출 간격에 맞춰요.</small></div>
  </>}
  <div className="section-label">받을 결과</div>
  <div className="field"><label htmlFor="http-response-format">응답 형식</label><Select value={response.responseFormat||parameters.responseFormat||'autodetect'} onValueChange={responseFormat=>updateResponse({responseFormat})}><SelectTrigger id="http-response-format"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="autodetect">자동으로 확인</SelectItem><SelectItem value="json">JSON 데이터</SelectItem><SelectItem value="text">글자</SelectItem><SelectItem value="file">파일</SelectItem></SelectContent></Select></div>
  {(response.responseFormat==='file'||parameters.responseFormat==='file'&&!response.responseFormat)&&<div className="field"><label htmlFor="http-response-file">받은 파일 항목 이름</label><input id="http-response-file" value={response.outputPropertyName??'data'} onChange={event=>updateResponse({outputPropertyName:event.target.value})}/><small>다음 파일 처리 단계에서도 같은 이름을 선택해주세요.</small></div>}
  <label className="inline-switch"><Switch checked={!!response.fullResponse} onCheckedChange={fullResponse=>updateResponse({fullResponse})}/>응답 번호·헤더도 함께 받기</label>
  <label className="inline-switch"><Switch checked={!!response.neverError} onCheckedChange={neverError=>updateResponse({neverError})}/>오류 응답도 결과로 전달</label>
  {response.neverError&&<small className="field-help">응답 번호를 확인하는 조건 단계를 이어 오류와 성공을 구분해주세요.</small>}
 </>;
}
