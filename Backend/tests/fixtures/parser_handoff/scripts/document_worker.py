"""Local Docling adapter. No URL inputs, remote services or model downloads."""
import json,sys,os
from pathlib import Path
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
from docling.document_converter import DocumentConverter,NativePdfFormatOption
from docling.datamodel.base_models import InputFormat,ConversionStatus
from parser_converter import make_converter,parser_config,parser_metadata
from lighton_supplement import enabled
from table_boundaries import logical_tables
from text_table_recovery import repaired_items
from docling_core.types.doc import TableItem
from pdf_text_supplement import supplement_docling_pdf,supplement_warnings
path=Path(sys.argv[1]);output=Path(sys.argv[2])
try:
 config=parser_config()
 result=make_converter(config).convert(path,max_num_pages=100,max_file_size=10_000_000)
 if result.status not in (ConversionStatus.SUCCESS,ConversionStatus.PARTIAL_SUCCESS):raise ValueError('변환 실패')
 doc=result.document
 tables=[]
 for table in (part for original in repaired_items(doc) if isinstance(original, TableItem) for part, _ in logical_tables(original)):
  frame=table.export_to_dataframe(doc=doc).fillna('')
  pages=sorted(set(p.page_no for p in table.prov))
  location=(('페이지 '+', '.join(map(str,pages))+' · ') if pages else '')+f'표 {len(tables)+1}'
  tables.append({'location':location,'columns':[str(x) for x in frame.columns[:100]],'rows':[[str(v) for v in row[:100]] for row in frame.values.tolist()[:1000]]})
 sections=[];section_chars=0
 for index,(item,_) in enumerate(doc.iterate_items(traverse_pictures=True)):
  body=getattr(item,'text','') or ''
  if not body.strip():continue
  page_numbers=sorted(set(p.page_no for p in (getattr(item,'prov',[]) or [])))
  body=body[:min(20000,200000-section_chars)]
  section_chars+=len(body)
  sections.append({'text':body,'location':('페이지 '+', '.join(map(str,page_numbers))+' · ' if page_numbers else '')+f'추출 항목 {index+1}'})
  if section_chars>=200000:break
 text=doc.export_to_markdown(traverse_pictures=True)
 has_body_text=any(str(getattr(item,'text','')).strip() for item,_ in doc.iterate_items(traverse_pictures=True)) or any(str(cell.text).strip() for table in doc.tables for cell in table.data.table_cells)
 table_truncated=any(len(t.data.table_cells)>10000 or t.data.num_rows>1001 or t.data.num_cols>100 for t in doc.tables)
 warnings=['큰 표는 최대 1,000행·100열까지만 미리보기로 저장합니다.'] if table_truncated else []
 supplement_metadata={}
 if path.suffix.lower()=='.pdf':
  enriched=supplement_docling_pdf(path,doc)
  additions=enriched['supplements']
  if additions:
   text+='\n\n'+'\n'.join(f"[페이지 {s['pages'][0]} · 보완 텍스트] {s['text']}" for s in additions)
   sections.extend({'text':s['text'],'location':f"페이지 {s['pages'][0]} · PyMuPDF4LLM 보완"} for s in additions)
   has_body_text=True
  if config['mode']=='standard':
   text=enriched['text']
   sections=[{'text':s['text'],'location':'페이지 '+', '.join(map(str,s['pages']))} for s in enriched['sections']]
   has_body_text=bool(text.strip())
  warnings.extend(supplement_warnings(enriched))
  supplement_metadata={'supplements':additions,'supplement_report':enriched['supplement_report'],'lighton_report':enriched.get('lighton_report')}
 if result.status==ConversionStatus.PARTIAL_SUCCESS or result.errors:warnings.append('일부 변환 오류가 있습니다. 원문과 비교하세요.')
 if path.suffix.lower()=='.pdf' and config['mode']=='native':warnings.append('Native PDF 모드로 OCR·표 구조 인식이 꺼져 있습니다.')
 if not text.strip() or not has_body_text:raise ValueError('추출할 텍스트가 없습니다.'+(' 스캔 PDF는 OCR 처리가 필요합니다.' if path.suffix.lower()=='.pdf' else ' 파일의 본문·문자 인코딩을 확인하세요.'))
 output.write_text(json.dumps({**supplement_metadata,**parser_metadata(config),'sections':sections[:1000],'text':text[:200000],'tables':tables[:30],'warnings':warnings+(['본문은 200,000자로 제한했습니다.'] if len(text)>200000 else []),'engine':'docling','version':__import__('importlib.metadata',fromlist=['version']).version('docling')},ensure_ascii=False),encoding='utf-8')
except Exception as exc:
 output.write_text(json.dumps({'error':'문서 변환 실패: '+str(exc).replace(str(path),path.name).replace(str(path.parent),'[변환 임시 폴더]')[:350]},ensure_ascii=False),encoding='utf-8');sys.exit(1)
