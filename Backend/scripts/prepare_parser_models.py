"""Explicit public-weight preparation. No user documents are read or uploaded."""
import argparse
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[2]/'data/pdf-models')
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    os.environ['EASYOCR_MODULE_PATH']=str(root/'easyocr-runtime')
    from docling.utils.model_downloader import download_models
    from docling.models.stages.ocr.easyocr_model import EasyOcrModel
    destination=download_models(output_dir=root/'docling',with_layout=True,with_tableformer=True,
        with_code_formula=False,with_picture_classifier=False,with_rapidocr=False,
        with_easyocr=True,easyocr_languages=['ko','en'],progress=True)
    print('\n저장소 루트 .env에 다음 경로를 설정하세요:')
    print('A4B_PDF_ARTIFACTS_DIR='+str(destination).replace('\\','/'))
    print('A4B_PDF_OCR_MODEL_DIR='+str(destination/EasyOcrModel._model_repo_folder).replace('\\','/'))


if __name__=='__main__':main()
