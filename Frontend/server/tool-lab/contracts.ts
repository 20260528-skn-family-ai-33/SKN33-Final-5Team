import {z} from 'zod';
import {BINARY_KEY} from '../binary';

export const PROTOCOL='a4b-tool-lab-v1';
const scalar=z.union([z.string(),z.number().finite(),z.boolean(),z.null()]);
export const rowsSchema=z.array(z.record(scalar)).max(500);
const field=z.object({name:z.string().min(1).max(80),type:z.enum(['string','number','boolean']),nullable:z.boolean()}).strict();
const specSchema=z.object({
 id:z.string().regex(/^[a-z][a-z0-9_-]{0,59}$/),title:z.string().min(1).max(100),
 instruction:z.string().min(1).max(6000),scope:z.literal('pure_json_transform'),
 input_fields:z.array(field).min(1).max(30),output_fields:z.array(field).min(1).max(30),
 unresolved_conditions:z.array(z.string().min(1).max(200)).max(10),
}).strict();
const caseSchema=z.object({id:z.string().regex(/^[a-z][a-z0-9_-]{0,59}$/),visibility:z.enum(['example','hidden']),input:rowsSchema,
 expectation:z.discriminatedUnion('kind',[
  z.object({kind:z.literal('rows'),rows:rowsSchema}).strict(),
  z.object({kind:z.literal('error'),message:z.string().min(1).max(200)}).strict(),
 ]),}).strict();
const datasetSchema=z.object({protocol:z.literal(PROTOCOL),human_reviewed:z.boolean(),
 tasks:z.array(z.object({spec:specSchema,cases:z.array(caseSchema).min(2).max(40)}).strict()).min(1).max(30),}).strict();
export type Rows=z.infer<typeof rowsSchema>;
export type Spec=z.infer<typeof specSchema>;
export type Task=z.infer<typeof datasetSchema>['tasks'][number];
export type Dataset=z.infer<typeof datasetSchema>;
export const generationSchema=z.object({status:z.enum(['candidate','needs_clarification','out_of_scope']),
 code:z.string().max(20000).nullable(),explanation:z.string().min(1).max(1000),questions:z.array(z.string().min(1).max(300)).max(3),}).strict();
export type Generation=z.infer<typeof generationSchema>;
export const RESPONSE_SCHEMA={type:'object',additionalProperties:false,required:['status','code','explanation','questions'],properties:{
 status:{type:'string',enum:['candidate','needs_clarification','out_of_scope']},
 code:{type:['string','null']},explanation:{type:'string'},questions:{type:'array',items:{type:'string'}},
}};
export const CONDITIONAL_RESPONSE_SCHEMA={anyOf:['candidate','needs_clarification','out_of_scope'].map(status=>({
 type:'object',additionalProperties:false,required:['status','code','explanation','questions'],properties:{
  status:{type:'string',enum:[status]},code:status==='candidate'?{type:'string',minLength:1}:{type:'null'},
  explanation:{type:'string',minLength:1},questions:{type:'array',items:{type:'string',minLength:1},
   ...(status==='candidate'?{maxItems:0}:status==='needs_clarification'?{minItems:1,maxItems:3}:{maxItems:3})},
 }}))};

export function validateRows(rows:Rows,fields:Spec['output_fields']){
 for(const row of rows){
  if(Object.keys(row).some(key=>!fields.some(f=>f.name===key)))throw new Error('규격에 없는 열이 있습니다.');
  for(const f of fields){
   if(!Object.hasOwn(row,f.name)||!(row[f.name]===null?f.nullable:typeof row[f.name]===f.type))throw new Error('필수 열 또는 자료형이 맞지 않습니다: '+f.name);
  }
 }
}
export function parseDataset(value:unknown):Dataset{
 const data=datasetSchema.parse(value);
 if(Buffer.byteLength(JSON.stringify(data))>1_000_000)throw new Error('평가 자료는 1MB 이내여야 합니다.');
 const tasks=new Set<string>();
 for(const task of data.tasks){
  if(tasks.has(task.spec.id))throw new Error('업무 ID가 중복됐습니다.');tasks.add(task.spec.id);
  for(const fields of [task.spec.input_fields,task.spec.output_fields]){
   if(new Set(fields.map(f=>f.name)).size!==fields.length)throw new Error('열 이름이 중복됐습니다.');
   if(fields.some(f=>['__proto__','prototype','constructor',BINARY_KEY].includes(f.name)))throw new Error('예약된 열 이름은 사용할 수 없습니다.');
  }
  if(!task.cases.some(c=>c.visibility==='hidden'&&c.expectation.kind==='rows'))throw new Error('숨긴 정상 결과 평가 사례가 필요합니다.');
  if(!task.cases.some(c=>c.visibility==='example'))throw new Error('공개 예시가 필요합니다.');
  const ids=new Set<string>();
  for(const c of task.cases){
   if(ids.has(c.id))throw new Error('평가 사례 ID가 중복됐습니다.');ids.add(c.id);
   if(c.expectation.kind==='rows'){validateRows(c.input,task.spec.input_fields);validateRows(c.expectation.rows,task.spec.output_fields);}
  }
 }
 return data;
}
export function parseGeneration(raw:string):Generation{
 const result=generationSchema.parse(JSON.parse(raw));
 if(result.status==='candidate'&&(!result.code?.trim()||result.questions.length))throw new Error('도구 후보에는 코드가 필요하고 미정 질문이 없어야 합니다.');
 if(result.status!=='candidate'&&result.code!==null)throw new Error('질문·범위 안내에는 실행 코드를 포함할 수 없습니다.');
 if(result.status==='needs_clarification'&&!result.questions.length)throw new Error('추가 확인할 질문이 필요합니다.');
 return result;
}

export function safeResponseIssue(error:unknown):string{
 if(error instanceof z.ZodError)return '응답 JSON 필드 규격 오류: '+error.issues.map(i=>i.path.join('.')+' ('+i.code+')').join(', ').slice(0,300);
 if(error instanceof SyntaxError)return 'JSON 객체 하나가 필요합니다. 코드 블록과 잘못된 문자열 이스케이프를 제거하세요.';
 const messages=['도구 후보에는 코드가 필요하고 미정 질문이 없어야 합니다.','질문·범위 안내에는 실행 코드를 포함할 수 없습니다.','추가 확인할 질문이 필요합니다.'];
 return error instanceof Error&&messages.includes(error.message)?error.message:'응답 JSON 계약을 확인하세요.';
}
