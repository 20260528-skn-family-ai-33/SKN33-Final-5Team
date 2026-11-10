"""Shared generation contract; supplies no task answers or fallback content."""

DESCRIPTION = (
    '최상위 instructions는 사용자가 요청한 업무와 명시한 처리 조건을 설명하는 지시사항입니다. '
    '빈 문자열이나 공백만 반환하지 않습니다. 질문·미지원 상태에도 현재 확인된 요청을 설명하고, '
    '미정 조건·실행 결과를 만들어내지 않습니다. 노드별 settings.instructions와 별개입니다.'
)

PROMPT = '\n' + DESCRIPTION + (
    '\ninstructions의 설명은 workflow의 실행 단계·설정을 대신하지 않습니다. '
    '실행 가능한 업무는 두 항목을 모두 작성하고, 확인이 필요한 조건은 questions에 남기세요. '
    '질문이 남아 있어도 확인된 workflow는 미완료 미리보기로 보존하며, 완료·승인된 설계라고 설명하지 마세요.\n'
)
