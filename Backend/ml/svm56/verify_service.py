"""Offline diagnostics against the pinned baseline model, not an accuracy test.

From Backend/: python -m ml.svm56.verify_service --bundle /path/to/08_package
"""
import argparse
import json
import os
from pathlib import Path

from ml.svm56.svm56_service_hook import recommend_svm_for_state, MODEL_SHA256
from workflow_engine.catalog.registry import TOOLS


def verify(bundle, model_path=None, expected_model_sha256=MODEL_SHA256):
    os.environ['A4B_SVM_MODEL_PATH'] = str(model_path or bundle/'model/recommender_char_svm_5431.joblib')
    os.environ['A4B_SVM_EXPECTED_MODEL_SHA256'] = expected_model_sha256
    os.environ['A4B_SVM_CATALOG_PATH'] = str(bundle/'catalog/catalog_56.json')
    cases = [
        ('첨부한 CSV에서 금액이 10000 이상인 행만 골라 CSV로 저장해줘', 'table',
         {'read_table', 'filter_sort', 'write_result'}, None),
        ('첨부한 문서를 요약만 해줘. 파일로 저장하지 마.', 'document', set(), 'negation_requires_planner'),
        ('고객 응대 초안이 정책을 위반하는지 검수해줘', None, {'reply_review'}, None),
        ('응대 검수 결과를 보고서 표로 정리해줘', None, {'review_summary'}, None),
    ]
    results = []
    for query, kind, required, status in cases:
        state = {'messages': [{'role': 'user', 'content': query}],
                 'resources': [{'kind': kind, 'columns': ['금액']}] if kind else []}
        result = recommend_svm_for_state(state, TOOLS)
        ids = {r['tool_id'] for r in result['ml_candidates']}
        passed = required <= ids and (status is None or not ids and result['ml_status'] == status)
        passed = passed and (kind != 'table' or 'export_result' not in ids)
        results.append({'query': query, 'passed': passed, 'advice': result})
    result = recommend_svm_for_state({'messages': [
        {'role': 'user', 'content': cases[0][0]},
        {'role': 'user', 'content': '아니, CSV 저장은 취소하고 문서 내용만 요약해줘.'}]}, TOOLS)
    results.append({'query': 'multi-turn cancellation',
                    'passed': not result['ml_candidates'] and result['ml_status'] == 'conversation_requires_planner',
                    'advice': result})
    return {'passed': all(r['passed'] for r in results), 'cases': results,
            'scope': 'Pinned-model diagnostic probes; no Qwen or workflow execution'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--model', type=Path, help='Optional evaluated candidate model')
    parser.add_argument('--model-sha256', help='Required exact hash when --model is used')
    args = parser.parse_args()
    if bool(args.model) != bool(args.model_sha256):
        parser.error('--model and --model-sha256 must be provided together')
    result = verify(args.bundle.resolve(), args.model.resolve() if args.model else None,
                    args.model_sha256 or MODEL_SHA256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
