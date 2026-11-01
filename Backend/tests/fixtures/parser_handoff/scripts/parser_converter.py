"""Service PDF settings: structure/OCR by default, independent of optional VLM."""
import hashlib
import os
from pathlib import Path

PIPELINE_VERSION = 'docling-structure-hybrid-v2'


def parser_config():
    from lighton_supplement import enabled
    import torch
    mode = os.environ.get('A4B_PDF_PARSER_MODE', 'standard').lower()
    requested = os.environ.get('A4B_PDF_DEVICE', 'auto').lower()
    if mode not in {'standard', 'native'}:
        raise ValueError('A4B_PDF_PARSER_MODE는 standard 또는 native여야 합니다.')
    if requested not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('A4B_PDF_DEVICE는 auto, cpu 또는 cuda여야 합니다.')
    device = ('cuda' if torch.cuda.is_available() else 'cpu') if requested == 'auto' else requested
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA를 사용할 수 없습니다. A4B_PDF_DEVICE=cpu로 설정하세요.')
    if enabled() and (mode != 'standard' or device != 'cuda'):
        raise ValueError('LightOn 보완은 standard 모드와 CUDA가 필요합니다. CPU에서는 PDF_LIGHTON_ENABLED=0을 사용하세요.')
    return dict(mode=mode, device=device, ocr=mode == 'standard', table_structure=mode == 'standard',
                ocr_languages=['ko', 'en'] if mode == 'standard' else [],
                ocr_model_directory=os.environ.get('A4B_PDF_OCR_MODEL_DIR') or None,
                artifacts_directory=os.environ.get('A4B_PDF_ARTIFACTS_DIR') or None,
                model_downloads=False, lighton_enabled=enabled())


def parser_metadata(config):
    folder = Path(__file__).resolve().parent
    names = ['parser_converter.py', 'document_postprocessing.py', 'table_boundaries.py',
             'text_table_recovery.py', 'annual_headers.py', 'pdf_text_supplement.py', 'lighton_supplement.py']
    return dict(parser_pipeline_version=PIPELINE_VERSION, parser_config=config,
                parser_code_sha256={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names})


def make_converter(config=None):
    from docling.document_converter import DocumentConverter, NativePdfFormatOption, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    config = config or parser_config()
    if config['mode'] == 'native':
        return DocumentConverter(format_options={InputFormat.PDF:NativePdfFormatOption()})
    if config['ocr_model_directory']:
        # EasyOCR also creates user_network outside model_storage_directory.
        # Keep both under the explicitly configured, writable model directory.
        os.environ['EASYOCR_MODULE_PATH']=str(Path(config['ocr_model_directory']).resolve())
    import torch
    torch.set_num_threads(4)
    from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
    opts=PdfPipelineOptions(do_ocr=True,do_table_structure=True)
    opts.ocr_options=EasyOcrOptions(lang=['ko','en'],use_gpu=config['device']=='cuda',
                                   download_enabled=False,model_storage_directory=config['ocr_model_directory'])
    opts.accelerator_options=AcceleratorOptions(device=AcceleratorDevice(config['device']),num_threads=4)
    if config['artifacts_directory']:
        opts.artifacts_path=Path(config['artifacts_directory'])
    return DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=opts)})
