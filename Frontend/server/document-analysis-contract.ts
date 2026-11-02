export const DOCUMENT_FORMATS=new Set(['pdf','hwp','hwpx','doc','docx','ppt','pptx','xls','xlsx','csv','tsv','txt','md','jpg','jpeg','png','bmp','webp','tif','tiff','heic']);
export const routingEnabled=()=>process.env.A4B_DOCUMENT_ROUTING!=='0';
export const analysisSupportsFormat=(format:string)=>DOCUMENT_FORMATS.has(format)||
 (format==='json'&&process.env.A4B_DOCUMENT_BENCHMARK_ARM==='docling-paddle-fixed-v1');
export type AnalysisBlock={id:string;parent_object_id:string|null;unit_id:string|null;content_types:string[];
 native_location:Record<string,unknown>;text:string;engine:string;selected?:boolean;status:string;
 bbox:null|{values:number[];coordinate_system:string};data:Record<string,unknown>;phase?:string;artifact?:string|null;parser_version?:string};
export type DocumentAnalysis={contract_version:'a4b.document_analysis.v1';source_sha256:string;source_format:string;
 primary_processor:string;status:'succeeded'|'partial';review_required:boolean;review_reasons:string[];
 units:{id:string;native_location:Record<string,unknown>}[];blocks:AnalysisBlock[];
 routes:{id:string;phase:string;processor:string;status:string;elapsed_seconds:number}[];
 coverage:Record<string,unknown>;warnings:string[];conversions?:{artifact:string}[]};

export function validateDocumentAnalysis(value:unknown):value is DocumentAnalysis {
 if(!value||typeof value!=='object'||Array.isArray(value))return false;
 const v=value as DocumentAnalysis;
 if(v.contract_version!=='a4b.document_analysis.v1'||!/^[a-f0-9]{64}$/.test(v.source_sha256)||
  !(DOCUMENT_FORMATS.has(v.source_format)||v.source_format==='json'&&v.primary_processor==='docling+paddleocr')||!['succeeded','partial'].includes(v.status)||typeof v.review_required!=='boolean'||
  !Array.isArray(v.review_reasons)||!Array.isArray(v.blocks)||v.blocks.length>20000||!Array.isArray(v.units)||v.units.length>500||
  !Array.isArray(v.routes)||!Array.isArray(v.warnings)||v.warnings.some(w=>typeof w!=='string')||
  v.review_reasons.some(w=>typeof w!=='string')||v.status==='succeeded'&&v.review_required)return false;
 if(v.units.some(u=>!u||typeof u!=='object'||typeof u.id!=='string'||!u.id||!u.native_location||typeof u.native_location!=='object')||
  new Set(v.units.map(u=>u.id)).size!==v.units.length)return false;
 if(v.routes.some(r=>!r||typeof r!=='object'||typeof r.id!=='string'||typeof r.processor!=='string'||typeof r.phase!=='string'||
  !['succeeded','partial','failed'].includes(r.status)||typeof r.elapsed_seconds!=='number'||!Number.isFinite(r.elapsed_seconds)||r.elapsed_seconds<0))return false;
 const ids=new Set<string>(),units=new Set(v.units.map(u=>u.id));
 for(const b of v.blocks){
  if(!b||typeof b.id!=='string'||ids.has(b.id)||!Array.isArray(b.content_types)||!b.content_types.length||
   b.content_types.some(t=>typeof t!=='string')||typeof b.text!=='string'||typeof b.engine!=='string'||!b.native_location||typeof b.native_location!=='object'||
   !b.data||typeof b.data!=='object'||!['succeeded','review_required'].includes(b.status)||
   b.parent_object_id!==null&&typeof b.parent_object_id!=='string'||b.unit_id!==null&&!units.has(b.unit_id))return false;
  ids.add(b.id);
  if(b.bbox&&(!Array.isArray(b.bbox.values)||b.bbox.values.length!==4||b.bbox.values.some(x=>typeof x!=='number'||!Number.isFinite(x))||
   b.bbox.values[0]>b.bbox.values[2]||b.bbox.values[1]>b.bbox.values[3]||typeof b.bbox.coordinate_system!=='string'))return false;
 }
 const parents=new Map(v.blocks.map(b=>[b.id,b.parent_object_id]));
 for(const b of v.blocks){
  const seen=new Set([b.id]);let parent=b.parent_object_id;
  while(parent){if(!ids.has(parent)||seen.has(parent))return false;seen.add(parent);parent=parents.get(parent)??null;}
 }
 return v.blocks.reduce((n,b)=>n+b.text.length,0)<=1000000;
}
