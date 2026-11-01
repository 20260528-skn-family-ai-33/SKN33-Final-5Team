"""Structured model outputs used by document and control executors."""
from pydantic import BaseModel,ConfigDict

class Fact(BaseModel):
    model_config=ConfigDict(extra='forbid')
    field:str
    value:str|None
    source_id:str|None
    quote:str|None

class Citation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str
    quote:str

class GroundedAnswer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str
    citations:list[Citation]

class InputAssessment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    valid:bool
    clarification:str

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:bool
    reason:str

class SearchTerms(BaseModel):
    model_config=ConfigDict(extra='forbid')
    keywords:list[str]

class Facts(BaseModel):
    model_config=ConfigDict(extra='forbid')
    facts:list[Fact]

class Row(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cells:list[Fact]

class Rows(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rows:list[Row]
