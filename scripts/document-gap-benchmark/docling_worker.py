"""One Docling PDF conversion in a fresh process; no gold is loaded."""
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root/'Backend'/'scripts'))
p = argparse.ArgumentParser()
p.add_argument('--input', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--arm', choices=['docling-base','docling-formula','docling-table-fast','docling-chart'], required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
started=time.perf_counter()
try:
    from parser_converter import make_converter, parser_config
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import TableFormerMode
    import importlib.metadata
    config=parser_config(); converter=make_converter(config)
    opts=converter.format_to_options[InputFormat.PDF].pipeline_options
    if a.arm=='docling-formula': opts.do_formula_enrichment=True
    if a.arm=='docling-table-fast': opts.table_structure_options.mode=TableFormerMode.FAST
    if a.arm=='docling-chart':
        if not hasattr(opts,'do_chart_extraction'): raise ValueError('Chart extraction unavailable in this Docling version')
        opts.do_chart_extraction=True
    options=opts.model_dump(mode='json')
    # Constructor/import time is separated from convert(), but model initialization may be lazy
    # and is therefore included in convert_seconds. This is not a warm request.
    setup=time.perf_counter()-started
    t=time.perf_counter(); converted=converter.convert(a.input, max_num_pages=500, max_file_size=8*1024*1024)
    seconds=time.perf_counter()-t; doc=converted.document
    (a.output/'raw.json').write_text(json.dumps(doc.export_to_dict(),ensure_ascii=False,default=str),encoding='utf-8')
    (a.output/'prediction.md').write_text(doc.export_to_markdown(),encoding='utf-8')
    value={'status':str(converted.status),'errors':[str(e) for e in converted.errors], 'version':importlib.metadata.version('docling'),
        'arm':a.arm,'parser_config':config,'options':options,'setup_seconds':setup,'convert_seconds':seconds,
        'process_elapsed_seconds':time.perf_counter()-started,'measurement':'fresh process; model initialization may occur in convert'}
    (a.output/'metadata.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
except Exception as e:
    (a.output/'error.json').write_text(json.dumps({'error_type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc()},ensure_ascii=False,indent=2),encoding='utf-8')
    raise
