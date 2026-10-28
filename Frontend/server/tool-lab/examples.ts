import {PROTOCOL,parseDataset} from './contracts';
import type {Generator} from './generator';

const dateCode=`return $input.all().map(({json:r})=>{
 const m=/^(19|20)\\d{6}$/.exec(r.date);if(!m)throw new Error('날짜 형식 오류');
 const s=r.date,y=Number(s.slice(0,4)),month=Number(s.slice(4,6)),day=Number(s.slice(6,8));
 const d=new Date(Date.UTC(y,month-1,day));
 if(d.getUTCFullYear()!==y||d.getUTCMonth()!==month-1||d.getUTCDate()!==day)throw new Error('날짜 형식 오류');
 return {date:s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8)};
});`;
const phoneCode=`return $input.all().map(({json:r})=>{
 const s=r.phone.replace(/[\\s-]/g,'');if(!/^\\+?\\d{8,15}$/.test(s))throw new Error('전화번호 형식 오류');
 return {phone:s};
});`;
const taxCode=`return $input.all().map(({json:r})=>{
 const amount=r.amount;if(!Number.isInteger(amount)||amount<0||amount>10000000)throw new Error('금액 형식 오류');
 const tax=Math.floor((amount+5)/10);return {amount,tax,total:amount+tax};
});`;
export const demoDataset=()=>parseDataset({protocol:PROTOCOL,human_reviewed:false,tasks:[
 {spec:{id:'date_normalize',title:'달력 날짜 변환',scope:'pure_json_transform',
  instruction:'1900~2099년의 YYYYMMDD 문자열을 YYYY-MM-DD로 변환. 존재하지 않는 날짜나 다른 형식은 Error("날짜 형식 오류"). 입력 순서 유지.',
  input_fields:[{name:'date',type:'string',nullable:false}],output_fields:[{name:'date',type:'string',nullable:false}],unresolved_conditions:[]},
  cases:[{id:'example',visibility:'example',input:[{date:'20261026'}],expectation:{kind:'rows',rows:[{date:'2026-10-26'}]}},
   {id:'leap',visibility:'hidden',input:[{date:'20240229'}],expectation:{kind:'rows',rows:[{date:'2024-02-29'}]}},
   {id:'invalid_day',visibility:'hidden',input:[{date:'20260229'}],expectation:{kind:'error',message:'날짜 형식 오류'}},
   {id:'empty',visibility:'hidden',input:[],expectation:{kind:'rows',rows:[]}}]},
 {spec:{id:'phone_normalize',title:'전화번호 표기 정리',scope:'pure_json_transform',
  instruction:'문자열의 공백·하이픈만 제거. 맨 앞 +는 보존. 결과는 선택적 + 뒤 숫자 8~15개여야 하며 아니면 Error("전화번호 형식 오류"). 입력 순서 유지.',
  input_fields:[{name:'phone',type:'string',nullable:false}],output_fields:[{name:'phone',type:'string',nullable:false}],unresolved_conditions:[]},
  cases:[{id:'example',visibility:'example',input:[{phone:'010-1234-5678'}],expectation:{kind:'rows',rows:[{phone:'01012345678'}]}},
   {id:'international',visibility:'hidden',input:[{phone:'+82 10-1234-5678'}],expectation:{kind:'rows',rows:[{phone:'+821012345678'}]}},
   {id:'letters',visibility:'hidden',input:[{phone:'abc01012345678'}],expectation:{kind:'error',message:'전화번호 형식 오류'}},
   {id:'short',visibility:'hidden',input:[{phone:'123'}],expectation:{kind:'error',message:'전화번호 형식 오류'}}]},
 {spec:{id:'tax_rounding',title:'부가세 반올림 계산',scope:'pure_json_transform',
  instruction:'amount는 0~10000000 정수 원. 부가세 tax=amount의 10%를 원 단위 반올림(0.5 올림), total=amount+tax. 그 외 입력은 Error("금액 형식 오류"). 입력 순서 유지.',
  input_fields:[{name:'amount',type:'number',nullable:false}],output_fields:[{name:'amount',type:'number',nullable:false},{name:'tax',type:'number',nullable:false},{name:'total',type:'number',nullable:false}],unresolved_conditions:[]},
  cases:[{id:'example',visibility:'example',input:[{amount:100}],expectation:{kind:'rows',rows:[{amount:100,tax:10,total:110}]}},
   {id:'half_rounding',visibility:'hidden',input:[{amount:5}],expectation:{kind:'rows',rows:[{amount:5,tax:1,total:6}]}},
   {id:'zero',visibility:'hidden',input:[{amount:0}],expectation:{kind:'rows',rows:[{amount:0,tax:0,total:0}]}},
   {id:'negative',visibility:'hidden',input:[{amount:-1}],expectation:{kind:'error',message:'금액 형식 오류'}}]},
]});

/** Handwritten demonstration responses. Never label these as Qwen/GPT results. */
export function fixtureGenerator(wrongBoundary=false):Generator{
 return {identity:{provider:'fixture',model:wrongBoundary?'handwritten-boundary-bug':'handwritten-reference',settings:{live_model:false}},
  async invoke(messages){
   const id=JSON.parse(messages[1].content).task.spec.id;
   const codes:Record<string,string>={date_normalize:dateCode,phone_normalize:phoneCode,
    tax_rounding:wrongBoundary?taxCode.replace('Math.floor((amount+5)/10)','Math.floor(amount/10)'):taxCode};
   if(!codes[id])throw new Error('시연 응답이 없는 업무입니다.');
   const code='function transform(rows) {\n'+codes[id].replaceAll('$input.all()','rows.map(json=>({json}))')+'\n}';
   return {content:JSON.stringify({status:'candidate',code,explanation:'검증 체계 점검용 수동 작성 코드',questions:[]})};
  }};
}
