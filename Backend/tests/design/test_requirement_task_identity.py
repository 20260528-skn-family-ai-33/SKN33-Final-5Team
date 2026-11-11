"""Replay duplicate candidates from the public 9B trace without invoking a model."""
from agent_builder.design.requirement_ledger import extract_plan


def candidate(description, spec, turn=0):
    return dict(description=description, spec=spec, source_message=turn)


def extract(messages, candidates, **extra):
    state = {'messages': messages, 'resources': [], **extra}
    return extract_plan({'tasks': candidates, 'unresolved': []}, state)['tasks']


def test_x03_preliminary_export_and_final_export_have_one_requested_result():
    messages = [dict(role='user', content='연결한 표를 region별 amount 합계로 집계하고 sum이 25 이상인 결과만 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')]
    export = dict(kind='export', format='json')
    aggregate = dict(kind='aggregate', fields=['region'], aggregation='sum', sum_field='amount')
    filtered = dict(kind='filter', filters=[dict(field='sum', operator='gte', value=25.0,
                    compare_field=None, value_type='number')], match='all')
    policy = dict(kind='policy', missing_policy='error', duplicate_policy='keep', duplicate_field=None)
    tasks = extract(messages, [
        candidate('region별 amount 합계 집계 및 필터링 후 JSON 저장', export),
        candidate('region별 amount 합계 계산', aggregate), candidate('합계 값이 25 이상인 행 필터링', filtered),
        candidate('빈 값을 오류로 처리', policy), candidate('결과를 JSON 파일로 저장', export)])
    assert [task['spec'] for task in tasks] == [aggregate, filtered, policy, export]
    assert tasks[1]['depends_on'] == [tasks[0]['id']]
    assert tasks[-1]['depends_on'] == [tasks[1]['id']]


def test_m04_answer_refines_same_schedule_and_downstream_binding_survives():
    messages = [dict(role='user', content='한국 시간으로 매일 표를 JSON으로 저장해줘.'),
                dict(role='assistant', content='몇 시 몇 분에 실행할까요? 오전·오후도 함께 알려주세요.'),
                dict(role='user', content='매일 오전 9시 정각으로 해줘.')]
    schedule = dict(kind='schedule', schedule=dict(frequency='daily', timezone='Asia/Seoul', hour=9,
                    minute=0, weekday=None, monthday=None, month_end=False))
    tasks = extract(messages, [
        candidate('매일 한국 시간 오전 9시에 표를 JSON 형식으로 저장하는 예약 작업 설정', schedule),
        candidate('예약 실행 시간을 매일 오전 9시로 구체화', schedule, 1),
        candidate('결과 내보내기', dict(kind='export', format='json'))],
        condition_answers=[dict(question_ids=['schedule-hour'], candidate_keys=['schedule.hour'],
                                answer=messages[2]['content'], turn=2)])
    assert [task['spec']['kind'] for task in tasks] == ['schedule', 'export']
    assert tasks[1]['depends_on'] == [tasks[0]['id']]


def test_a_later_schedule_for_a_distinct_job_is_not_an_answer():
    messages = [dict(role='user', content='매일 한국 시간 오전 9시에 매출을 확인해.'),
                dict(role='user', content='매일 같은 시각에 재고도 별도로 확인해.')]
    schedule = dict(kind='schedule', schedule=dict(frequency='daily', timezone='Asia/Seoul', hour=9,
                    minute=0, weekday=None, monthday=None, month_end=False))
    tasks = extract(messages, [candidate('매출 확인 예약', schedule), candidate('재고 확인 예약', schedule, 1)])
    assert len(tasks) == 2


def test_single_named_artifact_in_each_request_keeps_both_destinations():
    messages = [dict(role='user', content='매출 결과는 sales.json 파일 하나로 저장해.'),
                dict(role='user', content='같은 결과를 backup.json 파일 하나로 저장해.')]
    export = dict(kind='export', format='json')
    tasks = extract(messages, [candidate('매출 파일', export), candidate('백업 파일', export, 1)])
    assert len(tasks) == 2


def test_m07_formula_reply_completes_the_same_output_column():
    request = '연결한 표의 qty에 곱할 배율을 아직 정하지 못했어. scaled라는 열을 만들고 JSON으로 저장해줘.'
    reply = 'qty에 2를 곱해줘.'
    messages = [dict(role='user', content=request), dict(role='assistant', content='qty에 곱할 배율은?'),
                dict(role='user', content=reply)]
    compute = dict(kind='compute', formula='qty * 2', output_column='scaled')
    export = dict(kind='export', format='json')
    tasks = extract(messages, [candidate('scaled 열 생성', compute), candidate('답변 반영', compute, 1),
                               candidate('최종 표 저장', export)],
                    resources=[dict(id='table-a', kind='table', columns=['qty'])],
                    capability_answers=[dict(turn=2, question_ids=['factor'], kind='answer', answer=reply)])
    assert [t['spec'] for t in tasks] == [compute, export]
    assert tasks[1]['depends_on'] == [tasks[0]['id']]


def test_equal_formulas_on_separately_requested_inputs_are_both_kept():
    messages = [dict(role='user', content='두 입력 표에 각각 계산 열을 만들어. 수식은 qty * 2이고 결과 열 이름은 scaled.')]
    compute = dict(kind='compute', formula='qty * 2', output_column='scaled')
    read_a = dict(kind='read', file_ids=['store-a'])
    read_b = dict(kind='read', file_ids=['store-b'])
    tasks = extract(messages, [candidate('A 매장 읽기', read_a), candidate('매장 A 수량 두 배', compute),
                               candidate('B 매장 읽기', read_b), candidate('매장 B 수량 두 배', compute)],
                    resources=[dict(id=name, kind='table', columns=['qty']) for name in ('store-a', 'store-b')])
    assert [task['spec'] for task in tasks] == [read_a, compute, read_b, compute]
