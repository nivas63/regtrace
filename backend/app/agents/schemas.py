"""Pydantic schemas for every LLM output. Raw model JSON is never used directly."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.types import ChangeCategory, ConflictType, Modality, ObligationType

Confidence = Field(ge=0.0, le=1.0)


# -- obligation extraction ---------------------------------------------------------


class ApplicabilitySpec(BaseModel):
    entity_types: list[str] = Field(default_factory=list, max_length=10)
    products: list[str] = Field(default_factory=list, max_length=10)
    customer_segments: list[str] = Field(default_factory=list, max_length=10)
    thresholds: list[str] = Field(default_factory=list, max_length=5)


class RequiredEvidence(BaseModel):
    description: str = Field(min_length=3, max_length=300)
    evidence_type: str = Field(default="DOCUMENT", max_length=40)


class ExtractedObligation(BaseModel):
    section_id: str
    requirement_text: str = Field(min_length=10, max_length=1000)
    source_quote: str = Field(min_length=10, max_length=1500)
    modality: Modality
    obligation_type: ObligationType
    subject: str = Field(min_length=2, max_length=300)
    action: str = Field(min_length=3, max_length=600)
    condition: str | None = Field(default=None, max_length=600)
    frequency_text: str | None = Field(default=None, max_length=200)
    deadline_text: str | None = Field(default=None, max_length=200)
    applicability: ApplicabilitySpec = Field(default_factory=ApplicabilitySpec)
    responsible_function: str | None = Field(default=None, max_length=200)
    required_evidence: list[RequiredEvidence] = Field(default_factory=list, max_length=4)
    ambiguity_note: str | None = Field(default=None, max_length=500)
    self_confidence: float = Confidence


class ObligationExtraction(BaseModel):
    obligations: list[ExtractedObligation] = Field(default_factory=list, max_length=60)


class VerificationItem(BaseModel):
    candidate_id: str
    entailed: bool
    reason: str = Field(max_length=400)


class ObligationVerification(BaseModel):
    results: list[VerificationItem]


# -- mapping ------------------------------------------------------------------------


class CandidateJudgement(BaseModel):
    control_key: str
    relation: Literal["FULL", "PARTIAL", "NONE"]
    coverage: float = Confidence
    confidence: float = Confidence
    control_quote: str | None = Field(default=None, max_length=800)
    reason: str = Field(min_length=5, max_length=600)


class ObligationMapping(BaseModel):
    obligation_id: str
    judgements: list[CandidateJudgement]


class MappingBatch(BaseModel):
    mappings: list[ObligationMapping]


# -- evidence ----------------------------------------------------------------------


class CriterionVerdict(BaseModel):
    criterion_id: str
    verdict: Literal["MET", "NOT_MET", "INSUFFICIENT"]
    evidence_id: str | None = None
    quote: str | None = Field(default=None, max_length=800)
    rationale: str = Field(max_length=400)
    confidence: float = Confidence


class EvidenceAssessmentResult(BaseModel):
    verdicts: list[CriterionVerdict]


# -- change intelligence -------------------------------------------------------------


class ChangeJudgement(BaseModel):
    pair_id: str
    category: ChangeCategory
    is_material: bool
    summary: str = Field(max_length=500)
    old_value: str | None = Field(default=None, max_length=200)
    new_value: str | None = Field(default=None, max_length=200)
    confidence: float = Confidence


class ChangeBatch(BaseModel):
    changes: list[ChangeJudgement]


class AmendmentInstruction(BaseModel):
    op: Literal["SUBSTITUTE_TEXT", "REPLACE_PARAGRAPH", "INSERT_PARAGRAPH", "DELETE_PARAGRAPH"]
    target_path: str = Field(max_length=40)
    old_text: str | None = Field(default=None, max_length=2000)
    new_text: str | None = Field(default=None, max_length=4000)
    new_path: str | None = Field(default=None, max_length=40)


class AmendmentParse(BaseModel):
    instructions: list[AmendmentInstruction] = Field(default_factory=list, max_length=40)
    effective_text: str | None = Field(default=None, max_length=300)
    unclear_parts: str | None = Field(default=None, max_length=800)


# -- remediation ---------------------------------------------------------------------


class RemediationStep(BaseModel):
    kind: Literal[
        "CONTROL_CHANGE",
        "PROCESS_CHANGE",
        "TECHNOLOGY_CHANGE",
        "POLICY_UPDATE",
        "EVIDENCE_COLLECTION",
        "TESTING",
        "APPROVAL",
    ]
    description: str = Field(min_length=5, max_length=500)


class RemediationPlan(BaseModel):
    finding_id: str
    root_cause: str = Field(max_length=600)
    actions: list[RemediationStep] = Field(min_length=1, max_length=8)
    suggested_owner: str = Field(max_length=200)
    target_days: int = Field(ge=1, le=365)
    dependencies: list[str] = Field(default_factory=list, max_length=8)
    expected_evidence: str = Field(max_length=500)
    verification_method: str = Field(max_length=500)
    touches_production: bool


class RemediationBatch(BaseModel):
    plans: list[RemediationPlan]


# -- cross-regulation ----------------------------------------------------------------


class ConflictJudgement(BaseModel):
    pair_id: str
    is_potential_conflict: bool
    conflict_type: ConflictType
    reason: str = Field(max_length=800)
    resolution_hint: str | None = Field(default=None, max_length=600)
    applicability_overlap: str | None = Field(default=None, max_length=400)
    confidence: float = Confidence


class ConflictBatch(BaseModel):
    pairs: list[ConflictJudgement]


class ClusterJudgement(BaseModel):
    group_id: str
    equivalent: bool
    topic: str = Field(max_length=200)
    summary: str = Field(max_length=600)
    shared_control_suggestion: str | None = Field(default=None, max_length=600)
    confidence: float = Confidence


class ClusterBatch(BaseModel):
    groups: list[ClusterJudgement]


# -- controls from policies ------------------------------------------------------------


class PolicyControl(BaseModel):
    section_id: str
    control_ref: str | None = Field(default=None, max_length=40)
    name: str = Field(max_length=300)
    description: str = Field(max_length=1500)
    frequency_text: str | None = Field(default=None, max_length=200)
    owner: str | None = Field(default=None, max_length=200)
    control_type: Literal["PREVENTIVE", "DETECTIVE", "CORRECTIVE"]
    automation: Literal["MANUAL", "SEMI_AUTOMATED", "AUTOMATED"]
    source_quote: str = Field(max_length=1500)


class PolicyControls(BaseModel):
    controls: list[PolicyControl] = Field(default_factory=list, max_length=60)


# -- supervisor / document intelligence ------------------------------------------------


class DocumentIntel(BaseModel):
    doc_class: Literal["REGULATION", "AMENDMENT", "POLICY", "EVIDENCE", "OTHER"]
    regulation_title: str | None = Field(default=None, max_length=500)
    related_regulation_id: str | None = None
    relationship: Literal["NEW_REGULATION", "NEW_VERSION", "AMENDMENT", "DUPLICATE", "UNRELATED"]
    reason: str = Field(max_length=500)
    confidence: float = Confidence


class Plan(BaseModel):
    steps: list[str] = Field(max_length=30)
    rationale: str = Field(max_length=1200)


class ApplicabilityItem(BaseModel):
    obligation_id: str
    decision: Literal["APPLICABLE", "NOT_APPLICABLE", "UNCERTAIN"]
    rationale: str = Field(max_length=400)
    confidence: float = Confidence


class ApplicabilityBatch(BaseModel):
    decisions: list[ApplicabilityItem]


class ScenarioParse(BaseModel):
    target_obligation_ids: list[str] = Field(default_factory=list, max_length=20)
    change_kind: Literal["FREQUENCY", "DEADLINE", "THRESHOLD", "NEW_EVIDENCE", "SCOPE"]
    new_frequency_text: str | None = Field(default=None, max_length=200)
    new_value_text: str | None = Field(default=None, max_length=200)
    interpretation: str = Field(max_length=500)
    confidence: float = Confidence


# -- RAG -------------------------------------------------------------------------------


class QueryUnderstanding(BaseModel):
    query_type: Literal["REGULATION_LOOKUP", "COMPLIANCE_STATUS", "CHANGE_QUERY", "OTHER"]
    rewritten_query: str = Field(max_length=500)
    sub_queries: list[str] = Field(default_factory=list, max_length=2)
    as_of_date: str | None = None


class Claim(BaseModel):
    text: str = Field(max_length=800)
    citations: list[str] = Field(default_factory=list, max_length=8)


class GroundedAnswer(BaseModel):
    answer: str = Field(max_length=3000)
    claims: list[Claim] = Field(default_factory=list, max_length=15)
    insufficient_evidence: bool = False
