"""Local Docling adapter. No URL inputs, remote services or model downloads."""
import json,sys,os
from pathlib import Path
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
from docling.document_converter import DocumentConverter,NativePdfFormatOption
from docling.datamodel.base_models import InputFormat,ConversionStatus
path=Path(sys.argv[1]);output=Path(sys.argv[2])
try:
 result=DocumentConverter(format_options={InputFormat.PDF:NativePdfFormatOption()}).convert(path,max_num_pages=100,max_file_size=10_000_000)
 if result.status not in (ConversionStatus.SUCCESS,ConversionStatus.PARTIAL_SUCCESS):raise ValueError('변환 실패')
 doc=result.document
 tables=[]
 for table in doc.tables:
  frame=table.export_to_dataframe(doc=doc).fillna('')
  pages=sorted(set(p.page_no for p in table.prov))
  location=(('페이지 '+', '.join(map(str,pages))+' · ') if pages else '')+f'표 {len(tables)+1}'
  tables.append({'location':location,'columns':[str(x) for x in frame.columns[:100]],'rows':[[str(v) for v in row[:100]] for row in frame.values.tolist()[:1000]]})
 sections=[];section_chars=0
 for index,(item,_) in enumerate(doc.iterate_items()):
  body=getattr(item,'text','') or ''
  if not body.strip():continue
  page_numbers=sorted(set(p.page_no for p in (getattr(item,'prov',[]) or [])))
  body=body[:min(20000,200000-section_chars)]
  section_chars+=len(body)
  sections.append({'text':body,'location':('페이지 '+', '.join(map(str,page_numbers))+' · ' if page_numbers else '')+f'추출 항목 {index+1}'})
  if section_chars>=200000:break
 text=doc.export_to_markdown()
 has_body_text=any(str(getattr(item,'text','')).strip() for item,_ in doc.iterate_items()) or any(str(cell.text).strip() for table in doc.tables for cell in table.data.table_cells)
 table_truncated=any(len(t.data.table_cells)>10000 or t.data.num_rows>1001 or t.data.num_cols>100 for t in doc.tables)
 warnings=['큰 표는 최대 1,000행·100열까지만 미리보기로 저장합니다.'] if table_truncated else []
 if result.status==ConversionStatus.PARTIAL_SUCCESS or result.errors:warnings.append('일부 변환 오류가 있습니다. 원문과 비교하세요.')
 if path.suffix.lower()=='.pdf':warnings.append('PDF는 텍스트 레이어를 읽습니다. 스캔된 영역의 OCR은 수행하지 않으므로 누락 여부를 확인하세요.')
 if not text.strip() or not has_body_text:raise ValueError('추출할 텍스트가 없습니다.'+(' 스캔 PDF는 OCR 처리가 필요합니다.' if path.suffix.lower()=='.pdf' else ' 파일의 본문·문자 인코딩을 확인하세요.'))
 output.write_text(json.dumps({'sections':sections[:1000],'text':text[:200000],'tables':tables[:30],'warnings':warnings+(['본문은 200,000자로 제한했습니다.'] if len(text)>200000 else []),'engine':'docling','version':__import__('importlib.metadata',fromlist=['version']).version('docling')},ensure_ascii=False),encoding='utf-8')
except Exception as exc:
 output.write_text(json.dumps({'error':'문서 변환 실패: '+str(exc).replace(str(path),path.name).replace(str(path.parent),'[변환 임시 폴더]')[:350]},ensure_ascii=False),encoding='utf-8');sys.exit(1)
