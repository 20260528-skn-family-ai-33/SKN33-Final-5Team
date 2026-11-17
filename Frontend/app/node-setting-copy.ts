/** Labels describe settings without exposing the storage keys used to edit them. */
const settingLabels:Record<string,string>={
 max_results:'조회 한도',limit:'한도',count:'건수',threshold:'기준값',query:'검색 조건',
 channel_id:'채널 ID',group_by:'묶을 열',value_column:'계산할 열',sum_field:'합계 열',
 field:'열 이름',compare_field:'비교할 열',format:'결과 형식',output_format:'결과 형식',
 null_policy:'빈 값 처리',model:'모델',temperature:'모델 온도',fields:'사용할 열',
 absolute_tolerance:'허용 오차',relative_tolerance:'비율 허용 오차',
 precision:'소수 자릿수',decimal_places:'소수 자릿수',offset:'시작 위치',
 top_k:'선택할 개수',timeout:'대기 시간',timeout_seconds:'대기 시간(초)',
 min_length:'최소 길이',max_length:'최대 길이',batch_size:'한 번에 처리할 개수',
 sheet_name:'시트 이름',range:'셀 범위',spreadsheet_id:'스프레드시트',
 file_name:'파일 이름',filename:'파일 이름',subject:'메일 제목',recipient:'받는 사람',
 text:'내용',message:'메시지',search:'검색어',pattern:'찾을 내용',replacement:'바꿀 내용',
};

export const isSafeSettingKey=(key:string)=>/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)&&
 !/(?:secret|token|password|credential|api.?key|authorization)/i.test(key)&&
 !['__proto__','prototype','constructor'].includes(key);

export const settingLabel=(key:string)=>settingLabels[key]||'추가 설정';

const emptyPolicies:Record<string,string>={ask:'실행 전에 확인',exclude:'해당 행 제외',keep:'그대로 유지',error:'처리를 중단하고 알림'};
export function settingValueText(key:string,value:string|number|boolean|null):string{
 if(value===null)return '없음';
 if(typeof value==='boolean')return value?'예':'아니요';
 if(typeof value!=='string')return String(value);
 if(key==='null_policy'||key==='missing_policy')return emptyPolicies[value]||'처리 방식 확인 필요';
 if(key==='format'||key==='output_format')return ({csv:'CSV',json:'JSON',xlsx:'Excel'} as Record<string,string>)[value]||value;
 return value;
}

/** Generated plan mirrors are shown as current rules, while authored prose stays intact. */
export function instructionText(settings:Record<string,unknown>):string|null{
 if(typeof settings.instructions!=='string'||!settings.instructions)return null;
 if(settings.execution_plan&&typeof settings.execution_plan==='object'){
  try{
   const parsed:unknown=JSON.parse(settings.instructions);
   if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)&&
    Array.isArray((parsed as {filters?:unknown}).filters)&&Array.isArray((parsed as {sort?:unknown}).sort)&&
    ['match','missing_policy','duplicate_policy'].every(key=>key in parsed))return null;
  }catch{/* User-authored prose is displayed without rewriting it. */}
 }
 return settings.instructions;
}
