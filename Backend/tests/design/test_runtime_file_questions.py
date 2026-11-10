"""Deferred uploads still allow questions about the work to perform."""
import json

import pytest

from agent_builder.design.runtime_inputs import runtime_file_question_issues
from agent_builder.design.nodes.direct_conversation import make_direct_conversation
from contracts.models import AgentDraft, ExecutionPlan


def deferred_state():
    return dict(session_id='deferred-questions', resources=[], issues=[], repair_count=0,
                node_library=[], required_node_refs=[], saved_workflows=[], trace=[],
                messages=[dict(role='user', content='CSV 파일을 집계해 줘'),
                          dict(role='user', content='실행할 때 넣을게요')])


BUSINESS_QUESTIONS = [
    '어떤 파일 처리 규칙을 적용할까요?',
    '어떤 문서 처리 방식을 적용해야 하나요?',
    '어느 파일 선택 기준을 적용할까요?',
    '어떤 자료 사용 범위를 허용할까요?',
    '어느 문서 연결 방식을 사용할까요?',
    '어떤 파일을 처리하는 기준을 적용할까요?',
    '어느 문서를 사용해야 하는 기준을 알려주세요.',
    '어떤 파일을 연결할지 판단 기준을 알려주세요.',
    'CSV 파일에서 집계할 열을 선택해 주세요.',
    '계약서에서 검토할 기준을 알려주세요.',
]


@pytest.mark.parametrize('question', BUSINESS_QUESTIONS)
def test_deferred_upload_allows_business_criteria(question):
    assert runtime_file_question_issues({'questions': [question]}, deferred_state()) == []


@pytest.mark.parametrize('question', [
    '어떤 파일을 처리할까요?',
    '어떤 파일을 사용할까요?',
    '어느 문서를 선택하나요?',
    '어느 자료를 연결하시나요?',
    '어떤 파일을 사용해야 하나요?',
    '어떤 파일을 사용하면 될까요?',
    '어떤 파일을 사용하면 되나요?',
    '어떤 파일을 연결하면 되나요?',
    '어떤 파일을 사용하시겠습니까?',
    '어떤 문서를 처리하겠어요?',
    '어느 파일을 선택하시면 되겠습니까?',
    '어떤 파일을 연결해야 되나요?',
    '어떤 파일을 사용해야 할까요?',
    '어떤 파일을 처리하십니까?',
    '어느 파일을 선택하실 건가요?',
    '어떤 파일을 사용할 예정인가요?',
    '어떤 문서를 연결할 계획인가요?',
    '어떤 문서 처리할까요?',
    '어떤 파일을 사용할지 알려주세요.',
    'CSV 파일을 선택해 주세요.',
    '계약서 PDF를 첨부해 주세요.',
    '파일 경로를 알려주세요.',
    '파일의 ID를 제공해주세요.',
    '계약서 PDF 파일은 무엇인가요?',
    '어떤 파일 처리 규칙을 적용할까요? 파일도 업로드해 주세요.',
    '어떤 문서 처리 방식을 적용해야 하나요? 어느 파일을 사용할까요?',
])
def test_deferred_upload_still_rejects_files_and_locators(question):
    assert runtime_file_question_issues({'questions': [question]}, deferred_state())


def question_draft(question):
    return AgentDraft(
        name='집계 설계', purpose='CSV 집계', instructions='사용자 기준에 따라 집계한다.',
        tool_ids=[], document_ids=[], requirements=[], questions=[question],
        question_options=[[]], unsupported=[], explanation='집계 기준을 확인합니다.',
        execution_plan=ExecutionPlan(filters=[], sort=[], match='all', missing_policy='keep',
                                     duplicate_policy='keep', duplicate_field=None, output_format='csv'),
    ).model_dump()


@pytest.mark.parametrize('question', BUSINESS_QUESTIONS[:2])
def test_direct_planner_delivers_business_question_without_retry(monkeypatch, question):
    monkeypatch.setenv('A4B_PLANNER_PROMPT', 'v8')
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    answer = question_draft(question)

    class Model:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            return {'content': json.dumps(answer, ensure_ascii=False)}

    model = Model()
    result = make_direct_conversation('', 'test', model_factory=lambda: model)(deferred_state())
    assert result['draft']['questions'] == [question]
    assert model.calls == 1


def test_file_question_is_not_rejected_without_deferred_upload():
    state = deferred_state()
    state['messages'].pop()
    assert runtime_file_question_issues({'questions': ['어떤 파일을 사용할까요?']}, state) == []
