import json


from langgraph.types import interrupt


from ..workflow_runtime import InputAssessment


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    question = settings['question']
    user_inputs = [m['content'] for m in state.get('messages', []) if m['role'] == 'user']
    if settings['instructions'] and user_inputs:
        assessment = llm.with_structured_output(InputAssessment, method='json_schema', strict=True).invoke([{'role': 'system', 'content': '직원이 이미 제공한 입력이 이 확인 질문에 답하고 있는지 검사합니다. 이미 충분하면 valid=true로 질문을 생략합니다. 사용자가 말하지 않은 실제 조치 결과나 해결 여부는 추측하지 말고 false입니다. 에이전트 안내는 사용자 답변이 아닙니다. 기준: ' + settings['instructions']}, {'role': 'user', 'content': json.dumps({'question': question, 'answer': '\n'.join(user_inputs)}, ensure_ascii=False)}])
        if assessment.valid:
            return {**inputs, 'answer': user_inputs[-1], '_already_answered': True}
    while True:
        answer = interrupt({'kind': 'workflow_question', 'node_id': node['id'], 'question': question, 'preview': inputs.get('records', [])[:10]})
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('확인 답변을 입력하세요.')
        if not settings['instructions']:
            break
        assessment = llm.with_structured_output(InputAssessment, method='json_schema', strict=True).invoke([{'role': 'system', 'content': '사용자 응답의 의미가 지정 확인 기준에 충분한지 판단합니다. 예/아니오라는 단어 자체를 강요하지 마세요. 계속 진행하거나 저장하라는 명확한 요청도 긍정 답변입니다. 확인 기준만 판정하세요. 이전 에이전트가 제안한 모든 조치를 수행했는지 강요하거나 추가 조건을 만들지 마세요. 이미 대화에서 제공한 모델·문제 정보는 반복 요구하지 마세요. 질문에 실제로 답하지 않거나 확인 기준을 판단할 수 없을 때만 valid=false와 필요한 질문을 반환합니다. true/false 해결 여부를 대신 추측하지 마세요. 기준: ' + settings['instructions']}, {'role': 'user', 'content': json.dumps({'question': question, 'answer': answer, 'prior_user_messages': user_inputs}, ensure_ascii=False)}])
        if assessment.valid:
            break
        question = assessment.clarification or settings['question']
    return {**inputs, 'answer': answer.strip()}
