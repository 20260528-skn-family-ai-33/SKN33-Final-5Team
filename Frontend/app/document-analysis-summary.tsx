import type {DocumentAnalysis} from '../server/document-analysis-contract';

export const reasonLabel=(reason:string)=>{
 if(reason.includes('table_structure_missing'))return '기본 처리에서 표 셀 구조를 확보하지 못했습니다. 대조 후보를 확인하세요.';
 if(reason.includes('floating_textbox'))return '떠 있는 텍스트 상자의 위치와 읽기 순서를 확인해야 합니다.';
 if(reason.includes('word_auxiliary'))return '머리말·꼬리말·각주 등 본문 밖 내용은 포함하지 않았습니다.';
 if(reason.includes('dependency_missing')||reason.includes('unavailable'))return '일부 처리 도구 또는 로컬 모델이 준비되지 않았습니다.';
 if(reason.includes('supplement_limit'))return '보완 처리 상한으로 일부 영역을 처리하지 못했습니다.';
 if(reason.includes('formula_cache'))return '저장된 계산 결과가 없는 수식이 있습니다.';
 if(reason.includes('important_native_values'))return '원본의 중요한 숫자와 파서 결과를 대조해야 합니다.';
 if(reason.includes('converted_source'))return '변환본의 내용 보존 여부를 확인해야 합니다.';
 if(reason.includes('without_extracted_text')||reason.includes('empty_ocr'))return '글자를 추출하지 못한 처리 단위가 있습니다.';
 if(reason.includes('reference'))return '참조된 외부 자료는 읽지 않았습니다.';
 if(reason.includes('notes'))return '발표자 메모는 본문에 포함하지 않았습니다.';
 if(reason.includes('chart')||reason.includes('semantic_analysis'))return '차트·수식 등 의미 확인이 필요한 영역이 있습니다.';
 if(reason.includes('timeout'))return '일부 처리의 제한 시간이 초과됐습니다.';
 return '일부 객체·영역 또는 처리 결과를 원본과 대조해야 합니다.';
};
const statusLabel:Record<string,string>={succeeded:'처리 완료',partial:'부분 처리',failed:'처리 실패'};
const phaseLabel:Record<string,string>={primary:'기본 처리',fallback:'대체 처리',classification:'영역 분류',supplement:'영역 보완',comparison:'구조 대조'};
export function DocumentAnalysisSummary({analysis,showText=false,humanReviewed=false}:{analysis?:DocumentAnalysis;showText?:boolean;humanReviewed?:boolean}){
 if(!analysis)return null;
 const reasons=[...new Set(analysis.review_reasons.map(reasonLabel))];
 return <section aria-label="문서 내용 분석 결과" className="as-help">
  <p><strong>{humanReviewed?'사람 검토 완료':analysis.review_required?'원본 검토 필요':'문서 분석 완료'}</strong> · {analysis.units.length}개 처리 단위 · {analysis.blocks.length}개 객체·영역</p>
  {reasons.map(reason=><p className="as-validation" key={reason}>{reason}</p>)}
  <details><summary>처리 경로 확인</summary>
   <p className="as-small-muted">기본 처리: {analysis.primary_processor} · 원본 위치와 영역별 출처를 보존합니다.</p>
   {analysis.routes.map(route=><p key={route.id}>{phaseLabel[route.phase]||route.phase}: {route.processor} · {statusLabel[route.status]||route.status}</p>)}
  </details>
  {showText&&<details><summary>문서·이미지에서 읽은 내용</summary><p className="as-small-muted">분석 본문의 앞부분입니다. 각 값은 원본과 대조하세요.</p>
   <p className="as-preserve">{analysis.blocks.filter(b=>b.selected!==false&&b.text.trim()).map(b=>b.text).join('\n').slice(0,12000)}</p></details>}
  <p className="as-small-muted">처리 완료는 문서 전체의 정확성을 보증하지 않습니다.</p>
 </section>;
}
