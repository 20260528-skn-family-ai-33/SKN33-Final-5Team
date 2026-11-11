"""Validate a set of customer answers against the current server-owned questions."""
import json
import math

from .question_controls import project_question_controls


def answer_text(value):
    if isinstance(value,list):return ', '.join(value)
    return str(value)


def question_answers(value,state):
    if value is None:return []
    if not isinstance(value,list) or not 1<=len(value)<=10:
        raise ValueError('답변할 항목을 선택해주세요.')
    pending={q['id']:q for q in project_question_controls(state)['pending_questions']}
    result=[];seen=set()
    for item in value:
        if not isinstance(item,dict) or set(item)-{'question_id','value','custom','skip'}:
            raise ValueError('질문 답변 형식을 확인해주세요.')
        identifier=item.get('question_id')
        if not isinstance(identifier,str) or identifier not in pending or identifier in seen:
            raise ValueError('현재 표시된 질문에 답변해주세요. 변경된 질문은 새로고침해 확인할 수 있습니다.')
        seen.add(identifier);question=pending[identifier];answer=item.get('value');custom=item.get('custom',False)
        if type(custom) is not bool:raise ValueError('직접 입력 여부를 확인해주세요.')
        if type(item.get('skip',False)) is not bool:raise ValueError('선택사항 건너뛰기 여부를 확인해주세요.')
        if item.get('skip'):
            if question.get('required') is not False or answer is not None:
                raise ValueError('필수 항목에는 답변해주세요.')
            result.append({'question_id':identifier,'value':None,'skip':True})
            continue
        response=question.get('response') or {};kind=response.get('type','text')
        if kind=='time':
            from .question_time import time_parts
            time_parts(answer)
            answer=answer.strip()
        elif kind=='number':
            if type(answer) not in (int,float) or not math.isfinite(answer):
                raise ValueError('숫자로 답변해주세요.')
            minimum=response.get('minimum');maximum=response.get('maximum');step=response.get('step')
            if minimum is not None and answer<minimum or maximum is not None and answer>maximum:
                raise ValueError('안내된 범위 안의 숫자로 답변해주세요.')
            if step is not None and not math.isclose((answer-(minimum or 0))/step,round((answer-(minimum or 0))/step),abs_tol=1e-8):
                raise ValueError('안내된 단위에 맞춰 숫자를 입력해주세요.')
        elif kind=='multi_choice':
            if (not isinstance(answer,list) or not answer or any(not isinstance(part,str) for part in answer)
                    or len(answer)>40 or any(not part.strip() or len(part)>2000 for part in answer)
                    or len(set(answer))!=len(answer) or not custom and any(part not in question.get('options',[]) for part in answer)):
                raise ValueError('표시된 선택지에서 답변을 선택해주세요.')
        else:
            if not isinstance(answer,str) or not answer.strip() or len(answer)>4000:
                raise ValueError('답변 내용을 입력해주세요.')
            answer=answer.strip()
            if kind=='single_choice' and not custom and answer not in question.get('options',[]):
                raise ValueError('표시된 선택지에서 답변을 선택하거나 직접 입력해주세요.')
        result.append({'question_id':identifier,'value':answer,'custom':custom})
    if len(json.dumps(result,ensure_ascii=False))>12000:raise ValueError('답변이 너무 깁니다. 내용을 줄여주세요.')
    return result
