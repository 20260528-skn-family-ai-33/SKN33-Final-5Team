import {writeFileSync} from 'node:fs';
const base=process.env.EASYFLOW_TEST_URL||'http://127.0.0.1:3011';
if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base))throw new Error('local test server only');
async function req(path:string,body?:unknown){const r=await fetch(base+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw new Error(JSON.stringify(data));return data as any;}
const source='배송과 반품 안내\n반품은 수령 후 7일 이내 접수합니다.\n배송 지연은 담당자에게 문의하세요.';
const flow={id:'integrated-document-demo',name:'문서 읽기와 실제 근거 확인',settings:{},nodes:[{id:'s',name:'시작',type:'n8n-nodes-base.manualTrigger',typeVersion:1,position:[0,0],parameters:{}},{id:'d',name:'여러 형식 문서 읽기',type:'easyflow.documentRead',typeVersion:1,position:[300,0],parameters:{binaryPropertyName:'data'}},{id:'k',name:'반품 원문 근거 확인',type:'easyflow.knowledgeSearch',typeVersion:1,position:[600,0],parameters:{text:'={{ $json.text }}',query:'반품',filename:'={{ $json.filename }}',limit:3}}],connections:{'시작':{main:[[{node:'여러 형식 문서 읽기',type:'main',index:0}]]},'여러 형식 문서 읽기':{main:[[{node:'반품 원문 근거 확인',type:'main',index:0}]]}}};
await req('/api/workflows',flow);
const upload=await req('/api/files',{fileName:'반품정책_샘플.txt',mimeType:'text/plain',property:'data',data:Buffer.from(source).toString('base64')});
const start=await req('/api/runs',{workflowId:flow.id,mode:'learning',input:upload.input});
let execution:any;
for(let i=0;i<100;i++){execution=(await req('/api/executions?id='+start.executionId)).execution;if(['success','error','canceled'].includes(execution.status))break;await new Promise(r=>setTimeout(r,200));}
if(execution.status!=='success')throw new Error(execution.error||'not completed');
const citation=execution.runs[2].output[0][0].documentEvidence.citations[0];if(!citation.quote.includes('7일'))throw new Error('missing actual quote');
const review={id:execution.id,runIndex:2,output:0,itemIndex:0,citationIndex:0,checked:true};await req('/api/evidence-review',review);
if(!(await req('/api/evidence-review?id='+execution.id)).checks['2:0:0:0'].checked)throw new Error('review not persisted');
const invalid=await fetch(base+'/api/evidence-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...review,citationIndex:999})});if(invalid.ok)throw new Error('invented evidence accepted');
const chat=await req('/api/assistant/sessions',{});if((await req('/api/assistant/sessions/'+chat.id)).revision!==0)throw new Error('chat retrieval failed');
writeFileSync('examples/document-evidence.workflow.json',JSON.stringify(flow,null,2));writeFileSync('examples/반품정책_샘플.txt',source);
writeFileSync('docs/integration-api-verification.json',JSON.stringify({checkedAt:new Date().toISOString(),actualFileUpload:true,actualDocling:true,executionStatus:execution.status,citation,reviewPersisted:true,invalidCitationRejected:true,chatSessionCreated:true,liveModel:false,externalActions:false},null,2));
console.log('PASS: real upload -> Docling -> evidence search -> stored review; invented evidence rejected; chat session API');
