// Offline producer fixtures. No server, worker, PDF conversion, DB or model call.
import {parseBuilderAsset} from '../server/agent-builder';
import {normalizeBuilderDocument} from '../server/builder-documents';

const csv=await parseBuilderAsset({fileName:'비용.csv',data:Buffer.from('항목,비용\nA,100000\nB,200000\n').toString('base64')});
const txt=await parseBuilderAsset({fileName:'규정.txt',data:Buffer.from('비용 기준은 100000원 이상입니다.').toString('base64')});
const parsed=normalizeBuilderDocument({text:'일부 본문',pages:['일부 본문'],sections:[],tables:[],engine:'docling',
  parser_version:'fixture-only',warnings:['일부 변환 오류'],page_count:1,parser_status:'partial',
  supplement_report:{deferred_pages:[2]}});
console.log(JSON.stringify([
  {name:'native-csv',producer:'parseBuilderAsset',asset:{...csv.asset,id:'native-csv',version:1,processing_status:'ready'}},
  {name:'native-txt',producer:'parseBuilderAsset',asset:{...txt.asset,id:'native-txt',version:1,processing_status:'ready'}},
  {name:'normalizer-partial-pdf',producer:'normalizeBuilderDocument; synthetic worker packet, no PDF parsing',
    asset:{...parsed,id:'normalizer-pdf',name:'부분.pdf',kind:'document',format:'pdf',columns:[],profile:{},version:1,processing_status:'ready'}},
]));
