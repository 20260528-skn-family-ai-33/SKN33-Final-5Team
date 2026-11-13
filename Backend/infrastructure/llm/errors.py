"""User-facing error text: never expose provider bodies, prompts or secrets."""

def public_error(error,api_key='',*service_tokens):
    # Provider bodies can contain prompts, billing details or secrets. Do not expose them.
    from pydantic import ValidationError
    import os
    from openai import APITimeoutError,APIConnectionError,AuthenticationError,PermissionDeniedError,RateLimitError,NotFoundError,APIStatusError
    if type(error).__name__=='SlackDeliveryUnknown':
        return 'Slack 전송 결과를 확인할 수 없습니다. 채널에서 메시지를 확인한 뒤 필요하면 새 실행을 시작해주세요.'
    if isinstance(error,ValidationError):
        fields=', '.join('.'.join(map(str,e['loc'])) for e in error.errors(include_input=False)[:5])
        return '모델이 반환한 설정 형식이 올바르지 않습니다. 다시 시도해주세요. (검사 항목: '+fields+')'
    if isinstance(error,ValueError):
        message=str(error)
        secrets=[api_key,*service_tokens,*[os.getenv(name,'') for name in ('A4B_LLM_API_KEY','A4B_MAIN_API_KEY','A4B_VERIFIER_API_KEY')]]
        for secret in secrets:
            if isinstance(secret,str) and secret:message=message.replace(secret,'[보호된 키]')
        return message[:1000]
    name=type(error).__name__
    if isinstance(error,(APITimeoutError,TimeoutError)):
        return '모델 응답 시간이 초과됐습니다. 대화는 보존되어 있으니 서버 상태와 응답 제한 시간을 확인한 뒤 다시 시도해주세요.'
    if os.getenv('A4B_MAIN_PROVIDER') in ('ollama','vllm'):
        if isinstance(error,(AuthenticationError,PermissionDeniedError)):return '모델 서버의 접속 키와 접근 권한을 확인해주세요. RunPod 계정 관리 키와는 별개입니다.'
        if isinstance(error,(APIConnectionError,NotFoundError)):return '모델 서버에 연결하지 못했습니다. RunPod가 실행 중인지, .env 주소와 서버 모델 이름이 맞는지 확인해주세요. 대화는 보존됩니다.'
        if isinstance(error,APIStatusError) and error.status_code in (502,503,504):return '모델 서버가 아직 준비되지 않았습니다. RunPod 실행 상태와 Ollama API 포트를 확인해주세요. 대화는 보존됩니다.'
        if isinstance(error,RateLimitError):return '모델 서버가 요청을 처리하지 못했습니다. 동시 요청 수와 서버 상태를 확인한 뒤 다시 시도해주세요.'
    if isinstance(error,(AuthenticationError,PermissionDeniedError)):return '연결 계정의 API 키 또는 모델 접근 권한을 확인해주세요.'
    if isinstance(error,RateLimitError):return '모델 API 사용 한도에 도달했습니다. 계정의 사용 한도와 잔액을 확인해주세요.'
    from langchain.agents.structured_output import StructuredOutputError
    if isinstance(error,StructuredOutputError):return '모델이 올바른 설정 형식으로 응답하지 못했습니다. 대화는 보존됩니다. 모델의 구조화 응답 지원 여부를 확인해주세요.'
    return '에이전트 요청을 완료하지 못했습니다. 연결 계정·네트워크·모델을 확인하고 다시 시도해주세요.'
