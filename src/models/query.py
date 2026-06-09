from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class QuerySource(str, Enum):
    SPEC_RAG      = "spec_rag"       # answer came from the spec knowledge base
    SUBMITTAL_RAG = "submittal_rag"  # answer came from the uploaded submittal docs
    REPORT_JSON   = "report_json"    # answer came from the review report findings


class QueryRequest(BaseModel):
    question: str
    submittal_id: str
    authority: str


class QueryAnswer(BaseModel):
    answer: str
    source: QuerySource
    source_references: list[str]  # e.g. ["Section 33 40 00, Clause 2.3", "submittal_02/5_Test_Report.pdf p.4"]
    confidence: str               # "high" | "medium" | "low"


class ConversationTurn(BaseModel):
    question: str
    answer: QueryAnswer
