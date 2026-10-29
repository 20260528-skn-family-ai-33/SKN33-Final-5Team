"""CLI boundary for the web server: local SOURCE OUTPUT, optional --work-dir."""
import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from document_analysis.contracts import AnalysisError
from document_analysis.pipeline import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if os.environ.get('A4B_DOCUMENT_BENCHMARK_ARM') == 'docling-paddle-fixed-v1':
                from document_analysis.benchmark_baseline import run as baseline_run
                result = baseline_run(args.source, args.work_dir or args.output.parent)
            else:
                result = run(args.source, args.work_dir or args.output.parent,
                             max_supplements=int(os.environ.get('A4B_DOCUMENT_MAX_SUPPLEMENTS', '16')))
        args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return 0
    except Exception as error:
        code = error.code if isinstance(error, AnalysisError) else 'inspection_error'
        message = str(error) if isinstance(error, AnalysisError) else '문서 구조 조사에 실패했습니다. 형식·의존성·처리 로그를 확인하세요.'
        args.output.write_text(json.dumps(dict(error=message, error_code=code, parser_status='failed'), ensure_ascii=False), encoding='utf-8')
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
