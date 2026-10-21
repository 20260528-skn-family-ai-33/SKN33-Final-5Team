"""Same PDF pipeline in one-shot or serial persistent mode; no result cache."""
import contextlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

PROCESS_START=time.perf_counter()
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
PROTOCOL='A4B_PDF_RESULT '


class ParserService:
    def __init__(self):
        self.converter=None
        self.config=None
        self.model_load_count=0
        self.conversion_count=0

    def synchronize_device(self):
        # CUDA launches are asynchronous; phase timings must include completed
        # device work. CPU execution and recognition settings are unchanged.
        if self.config and self.config['device']=='cuda':
            import torch
            torch.cuda.synchronize()

    def initialize(self):
        timings=dict(import_seconds=0.0,converter_seconds=0.0,model_load_seconds=0.0)
        if self.converter is not None:
            return timings
        started=time.perf_counter()
        from parser_converter import make_converter,parser_config
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.settings import settings
        settings.debug.profile_pipeline_timings=os.environ.get('A4B_PDF_PROFILE')=='1'
        self.config=parser_config()
        timings['import_seconds']=time.perf_counter()-started
        started=time.perf_counter()
        converter=make_converter(self.config)
        timings['converter_seconds']=time.perf_counter()-started
        started=time.perf_counter()
        converter.initialize_pipeline(InputFormat.PDF)
        self.synchronize_device()
        timings['model_load_seconds']=time.perf_counter()-started
        # Keep only fully initialized pipelines. Failed setup is retryable.
        self.converter=converter
        self.model_load_count+=1
        return timings

    def convert(self,source,output):
        request_start=time.perf_counter()
        reused=self.converter is not None
        timings=self.initialize()
        from docling.datamodel.base_models import ConversionStatus
        from parser_converter import parser_metadata
        from document_postprocessing import build_document_payload
        from pdf_text_supplement import supplement_docling_pdf,supplement_warnings
        self.synchronize_device()
        started=time.perf_counter()
        result=self.converter.convert(source,max_num_pages=500,max_file_size=8*1024*1024)
        self.synchronize_device()
        self.conversion_count+=1
        timings['conversion_seconds']=time.perf_counter()-started
        if result.status not in (ConversionStatus.SUCCESS,ConversionStatus.PARTIAL_SUCCESS):
            raise ValueError('변환 실패')
        started=time.perf_counter()
        base=build_document_payload(result.document)
        timings['structure_postprocess_seconds']=time.perf_counter()-started
        started=time.perf_counter()
        payload=supplement_docling_pdf(source,result.document,base)
        timings['supplement_seconds']=time.perf_counter()-started
        text=payload['text'];ordered=payload['pages']
        if not text.strip():
            raise ValueError('텍스트를 추출하지 못했습니다. 스캔 PDF는 OCR이 필요합니다.')
        if len(text)>1000000:
            raise ValueError('추출 본문이 1,000,000자를 넘었습니다. 자료를 나누세요.')
        warnings=[] if self.config['mode']=='standard' else ['Native PDF 모드입니다. OCR·표 구조 인식이 꺼져 있어 스캔 글자와 표 셀 관계를 누락할 수 있습니다.']
        warnings.extend(supplement_warnings(payload))
        if result.status==ConversionStatus.PARTIAL_SUCCESS or result.errors:
            warnings.append('일부 변환 오류가 있습니다. 원문과 대조하세요.')
        empty=[i+1 for i,p in enumerate(ordered) if not p.strip()]
        if empty:warnings.append('텍스트가 없는 페이지: '+', '.join(map(str,empty[:30])))
        data={**payload,**parser_metadata(self.config),
            'parser_status':'succeeded' if result.status==ConversionStatus.SUCCESS and not result.errors else 'partial',
            'engine':'docling','parser_version':__import__('importlib.metadata',fromlist=['version']).version('docling'),
            'warnings':warnings,
            'parser_runtime':dict(pid=os.getpid(),models_reused=reused,model_load_count=self.model_load_count,
                                  conversion_count=self.conversion_count,result_cache_used=False)}
        if result.timings:
            data['parser_stage_timings']={name:{'count':item.count,'seconds':float(sum(item.times))}
                                          for name,item in result.timings.items()}
        started=time.perf_counter()
        json.dumps(data,ensure_ascii=False)
        timings['serialization_seconds']=time.perf_counter()-started
        timings['request_seconds_before_write']=time.perf_counter()-request_start
        data['parser_timings']=timings
        output.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        return data


def handle(service,source,output):
    try:
        # Third-party model progress must never become a protocol response.
        with contextlib.redirect_stdout(sys.stderr):
            service.convert(Path(source),Path(output))
        return True
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        message=str(error) if isinstance(error,ValueError) and str(error).startswith(('A4B_','CUDA','LightOn')) else 'Docling 문서 처리에 실패했습니다. OCR·표 인식 모델 설치 및 500페이지·8MB 제한을 확인하세요.'
        Path(output).write_text(json.dumps({'error':message,'error_type':type(error).__name__},ensure_ascii=False),encoding='utf-8')
        # Do not reuse a possibly damaged pipeline after a failed job.
        service.converter=None
        return False


def main():
    service=ParserService()
    if sys.argv[1:]==['--serve']:
        for line in sys.stdin:
            try:
                request=json.loads(line)
                if request.get('action')=='shutdown':return 0
                ok=handle(service,request['source'],request['output'])
                print(PROTOCOL+json.dumps({'id':request['id'],'ok':ok}),flush=True)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                return 1
        return 0  # Parent exit closes stdin; no orphan service.
    if len(sys.argv)!=3:raise SystemExit('Usage: builder_document_worker.py SOURCE OUTPUT | --serve')
    return 0 if handle(service,sys.argv[1],sys.argv[2]) else 1


if __name__=='__main__':sys.exit(main())
