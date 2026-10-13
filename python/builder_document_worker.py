"""Docling native-text PDF conversion with original page mapping. No OCR/downloads."""
import os,json,sys
from pathlib import Path
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from docling.document_converter import DocumentConverter,NativePdfFormatOption
from docling.datamodel.base_models import InputFormat,ConversionStatus
source,output=map(Path,sys.argv[1:3])
try:
    result=DocumentConverter(format_options={InputFormat.PDF:NativePdfFormatOption()}).convert(source,max_num_pages=500,max_file_size=8*1024*1024)
    if result.status not in (ConversionStatus.SUCCESS,ConversionStatus.PARTIAL_SUCCESS):raise ValueError('변환 실패')
    doc=result.document;pages={page:[] for page in doc.pages};sections=[];tables=[]
    for item,_ in doc.iterate_items():
        text=getattr(item,'text','') or ''
        if not text.strip():continue
        numbers=sorted(set(p.page_no for p in getattr(item,'prov',[]) or []))
        for page in numbers:pages.setdefault(page,[]).append(text)
        sections.append({'text':text,'pages':numbers})
    for table in doc.tables:
        text=table.export_to_markdown(doc=doc)
        numbers=sorted(set(p.page_no for p in table.prov))
        for page in numbers:pages.setdefault(page,[]).append(text)
        tables.append({'text':text,'pages':numbers})
    ordered=['\n'.join(pages.get(page,[])) for page in range(1,max(pages,default=0)+1)]
    text='\n\n'.join(ordered)
    if not text.strip():raise ValueError('텍스트를 추출하지 못했습니다. 스캔 PDF는 OCR이 필요합니다.')
    if len(text)>1000000:raise ValueError('추출 본문이 1,000,000자를 넘었습니다. 자료를 나누세요.')
    warnings=['텍스트 레이어 기반 변환이며 OCR을 수행하지 않습니다.','Docling Native PDF 모드는 표 셀 구조를 복원하지 못할 수 있습니다. 오류와 해결 방법 관계는 원본 페이지와 대조하세요.']
    if result.status==ConversionStatus.PARTIAL_SUCCESS or result.errors:warnings.append('일부 변환 오류가 있습니다. 원문과 대조하세요.')
    empty=[i+1 for i,p in enumerate(ordered) if not p.strip()]
    if empty:warnings.append('텍스트가 없는 페이지: '+', '.join(map(str,empty[:30])))
    output.write_text(json.dumps({'text':text,'pages':ordered,'sections':sections,'tables':tables,'engine':'docling','parser_version':__import__('importlib.metadata',fromlist=['version']).version('docling'),'warnings':warnings,'page_count':len(ordered)},ensure_ascii=False))
except Exception as error:
    output.write_text(json.dumps({'error':'Docling 문서 처리에 실패했습니다. 텍스트 PDF·500페이지·8MB 제한을 확인하세요.','error_type':type(error).__name__},ensure_ascii=False));sys.exit(1)
