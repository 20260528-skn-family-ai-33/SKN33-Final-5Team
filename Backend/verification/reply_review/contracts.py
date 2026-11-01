"""Junghyun's public review contracts. Source texts are data, never instructions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    mode: Literal['review','pipeline'] = 'review'
    customer_message: str = Field(min_length=1,max_length=8000)
    policy_text: str = Field(default='',max_length=12000)
    policy_name: str = Field(default='업무 안내 자료',min_length=1,max_length=100)
    reply_draft: str = Field(default='',max_length=12000)
    review_rules: str = Field(default='',max_length=3000)
    model: str | None = None
    source_id: str = Field(default='',max_length=100)
    @model_validator(mode='after')
    def require_draft(self):
        if self.mode == 'review' and not self.reply_draft.strip():
            raise ValueError('단독 검수에는 응대 초안이 필요합니다.')
        return self

class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['customer','policy']
    quote: str = Field(min_length=1,max_length=3000)

class AnalysisReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requests: list[str] = Field(max_length=20)
    known_facts: list[str] = Field(max_length=30)
    missing_info: list[str] = Field(max_length=20)
    evidence: list[Evidence] = Field(max_length=30)

class ResponseReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reply: str = Field(min_length=1,max_length=12000)
    requires_confirmation: bool
    evidence: list[Evidence] = Field(max_length=30)

class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Literal['unsupported_claim','policy_conflict','missing_request','missing_information','unverified_action']
    severity: Literal['high','medium','low']
    draft_quote: str = Field(max_length=3000)
    evidence: list[Evidence] = Field(max_length=10)
    reason: str = Field(min_length=1,max_length=2000)
    suggestion: str = Field(min_length=1,max_length=2000)

class ReviewReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    verdict: Literal['pass','revise','needs_info']
    summary: str = Field(min_length=1,max_length=2000)
    requests: list[str] = Field(max_length=20)
    findings: list[Finding] = Field(max_length=20)
    missing_info: list[str] = Field(max_length=20)
    revised_reply: str = Field(min_length=1,max_length=12000)
