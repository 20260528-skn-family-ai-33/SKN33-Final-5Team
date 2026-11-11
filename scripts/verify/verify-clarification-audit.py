"""Offline, synthetic orchestration corpus. No model/API/network access."""
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))
from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.design.requirement_ledger import make_requirement_check
from agent_builder.design.request_meaning import make_meaning_node
from agent_builder.design.temporal_context import ensure_temporal_context
from agent_builder.design.gmail_summary import gmail_summary_reply
from tests.conditions.test_condition_readiness import state, Fake
from tests.design.test_request_meaning import Extractor, meaning
from tests.design.test_clarification_audit import unresolved_packet


def evaluate():
    cases = [
        ('recent5', 'Gmail에서 최근 5개 메일을 요약해줘', 'gmail', 'READY', [], []),
        ('yesterday', 'Gmail에서 어제 온 메일을 요약해줘', 'gmail', 'READY', [], []),
        ('daily_report', '매일 오전 9시에 이메일 보고서를 보내줘', 'requirements', 'CLARIFY', ['보고서의 내용이나 원본 자료', '이메일 수신 주소'], []),
        ('customer_analysis', '고객 데이터를 분석해서 보고서를 만들어줘', 'requirements', 'CLARIFY', ['분석할 고객 데이터', '보고서에서 확인할 분석 기준'], []),
        ('last_week_sales', '지난주 매출을 분석해서 이메일로 보내줘', 'requirements', 'CLARIFY', ['매출 데이터의 위치', '이메일 수신 주소'], []),
        ('refer_address', '방금 말한 이메일 주소로 보내줘', 'capability', 'READY', [], ['보고서를 audit@example.invalid로 보내줘']),
        ('already_provided', 'Gmail에서 최근 5개 메일을 요약해줘', 'gmail', 'READY', [], ['최근 메일은 5개로 정했어']),
        ('answer_then_proceed', 'JSON으로 만들어줘', 'requirements', 'READY', [], ['고객 데이터를 분석해서 보고서를 만들어줘', '결과 파일 형식은 무엇인가요?']),
        ('change_condition', '5개 대신 최근 10개로 바꿔줘', 'gmail', 'READY', [], ['Gmail에서 최근 5개 메일을 요약해줘']),
        ('unsupported', '결과를 Slack으로 보내줘', 'capability', 'UNSUPPORTED', [], []),
    ]
    rows = []
    for id, text, stage, expected, missing, history in cases:
        current = state(text)
        current['messages'] = [{'role': 'assistant' if h.endswith('?') else 'user', 'content': h} for h in history] + current['messages']
        current['user_timezone'] = 'Asia/Seoul'
        current.update(ensure_temporal_context(current, now=datetime.fromisoformat('2026-11-10T01:00:00+00:00')))
        prior = deepcopy(current['messages'][:-1])
        started = time.perf_counter()
        if stage == 'gmail':
            packet = meaning(count=None, evidence=None, period='yesterday') if id == 'yesterday' else meaning(count=10, evidence='10개') if id == 'change_condition' else meaning()
            if history:
                packet['tasks'][0]['selection']['limit_source_message'] = 1
                if id == 'already_provided': packet['tasks'][0]['source_message'] = 1
            transport = Extractor(packet)
            current.update(make_meaning_node('fixture', transport=transport)(current))
            update = gmail_summary_reply(current) or {}
            draft = update.get('draft') or {}
            actual = 'READY' if draft.get('workflow') and not draft.get('questions') else 'ERROR'
            raw = {'extraction': packet, 'review': {'faithful': True, 'issues': []}}
        elif stage == 'requirements':
            raw = unresolved_packet(text, missing)
            update = make_requirement_check('fixture', Fake([raw]), 'enforce')(current)
            actual = 'CLARIFY' if update.get('status') == 'needs_clarification' else 'READY' if update['requirement_route'] == 'plan' else 'ERROR'
            draft = update.get('draft') or {}
        else:
            raw = {'decision': 'missing_capability' if expected == 'UNSUPPORTED' else 'existing_tools',
                   'tool_ids': ['write_result'] if expected == 'UNSUPPORTED' else ['email.send'],
                   'missing_conditions': [], 'missing_actions': ['Slack 전송'] if expected == 'UNSUPPORTED' else [],
                   'reason': '합성 카탈로그 판정', 'user_quotes': [text]}
            update = make_capability_check('fixture', Fake([raw, raw]), 'enforce')(current)
            actual = {'existing_tools': 'READY', 'missing_capability': 'UNSUPPORTED', 'clarify': 'CLARIFY'}.get(update['capability_assessment']['decision'], 'ERROR')
            draft = update.get('draft') or {}
        elapsed = (time.perf_counter() - started) * 1000
        messages = update.get('messages', [])
        rows.append({'id': id, 'request': text, 'prior_conversation': prior, 'stage_under_test': stage,
                     'extracted_conditions': current.get('request_meaning') if stage == 'gmail' else update.get('requirement_ledger') or raw,
                     'selected_tools': draft.get('tool_ids') or (update.get('capability_assessment') or {}).get('tool_ids', []),
                     'expected': expected, 'actual': actual, 'actual_reason_to_ask': missing,
                     'qwen_raw_response': None, 'mock_response': raw,
                     'frontend_response_projection': messages[-1]['content'] if messages else None,
                     'frontend_observed_in_browser': False, 'questions': draft.get('questions', []),
                     'passed': actual == expected,
                     'failure_cause': None if actual == expected else 'Business unresolved values routed to validation failure' if missing else 'Inspect stage contract',
                     'elapsed_ms': round(elapsed, 3), 'workflow_generated': bool(draft.get('workflow')),
                     'external_execution': 'NOT_RUN'})
    labels = ['READY', 'CLARIFY', 'UNSUPPORTED']
    f1 = {}
    for label in labels:
        tp = sum(r['expected'] == label and r['actual'] == label for r in rows)
        fp = sum(r['expected'] != label and r['actual'] == label for r in rows)
        fn = sum(r['expected'] == label and r['actual'] != label for r in rows)
        f1[label] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    needed = [r for r in rows if r['expected'] == 'CLARIFY']
    return {'scope': 'Synthetic stage-routing fixtures, NOT Qwen semantic accuracy or end-to-end production evaluation',
            'model_calls': 0, 'cases': rows, 'metrics': {
                'fixture_routing_accuracy': sum(r['passed'] for r in rows) / len(rows), 'fixture_class_f1': f1,
                'required_question_rate': sum(bool(r['questions']) for r in needed) / len(needed),
                'unnecessary_question_rate': sum(bool(r['questions']) for r in rows if r['expected'] != 'CLARIFY') / (len(rows) - len(needed)),
                'repeated_question_rate_single_probe': int(bool(next(r for r in rows if r['id'] == 'already_provided')['questions'])),
                'answer_semantic_accuracy': None, 'clarification_completion_rate': None,
                'workflow_execution_success_rate': None,
                'mean_fixture_latency_ms': sum(r['elapsed_ms'] for r in rows) / len(rows)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['metrics'], ensure_ascii=True))
