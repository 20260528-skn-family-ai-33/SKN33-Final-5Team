"""메인 생성 모델과 분리한 검증 모듈. 실제 업무를 실행하지 않는다."""
from .contracts import VerificationInput, VerificationOutput
from .service import verify
__all__ = ['VerificationInput', 'VerificationOutput', 'verify']
