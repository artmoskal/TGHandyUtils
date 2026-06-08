"""Typed state models for the Anki generation graph."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from models.anki import AnkiCard
from ai_workflow_engine.models import EvaluationDecision, WorkflowTraceEvent, WorkflowUsageSummary


class ContentImage(BaseModel):
    index: int
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    image_data: Optional[bytes] = None

    @property
    def has_data(self) -> bool:
        return bool(self.image_data)


class ContentSource(BaseModel):
    content: str = ""
    user_id: int
    owner_name: str = "User"
    location: Optional[str] = None
    images: List[ContentImage] = Field(default_factory=list)


class AnkiDirectiveConstraints(BaseModel):
    include_image: bool = True
    image_placement: Literal["front", "back"] = "back"
    image_policy: Literal["auto", "none", "reuse_user_image", "reference", "generate"] = "auto"
    multi_split: bool = False
    strategy: Literal["split", "merge", "count"] = "split"
    count: Optional[int] = None
    card_type: Optional[Literal["basic", "cloze", "visual"]] = None
    guide_mode: bool = False
    guide: Optional[str] = None
    help: bool = False
    language_voice: bool = False
    source_language: Optional[str] = None
    target_language: Optional[str] = None


class ImageAssetPlan(BaseModel):
    image_role: Literal[
        "ignore_media",
        "source_content_only",
        "reuse_user_image",
        "use_as_reference",
        "generate_new_visual",
    ]
    candidate_front_images: List[int] = Field(default_factory=list)
    candidate_back_images: List[int] = Field(default_factory=list)
    reference_images: List[int] = Field(default_factory=list)
    rationale: str = ""

    @property
    def uses_uploaded_media(self) -> bool:
        return bool(self.candidate_front_images or self.candidate_back_images)


class CardBuildPlan(BaseModel):
    card_kind: Literal["basic", "cloze", "visual_basic"]
    image_policy: Literal["none", "reuse_user_image", "reference", "generate"] = "none"
    count: Optional[int] = None
    source_facts: List[str] = Field(default_factory=list)
    study_goal: str = ""
    visual_rationale: Optional[str] = None
    fallback_kind: Literal["basic", "cloze"] = "basic"
    user_constraints_applied: List[str] = Field(default_factory=list)


class TextCardScenario(BaseModel):
    source_content: str
    guide: Optional[str] = None
    rendering_guide: Optional[str] = None
    facts_to_test: List[str] = Field(default_factory=list)
    answer_constraints: str = ""
    strategy: Literal["split", "merge", "count"] = "split"
    count: Optional[int] = None


class ClozeCardScenario(BaseModel):
    source_content: str
    guide: Optional[str] = None
    rendering_guide: Optional[str] = None
    cloze_targets: List[str] = Field(default_factory=list)
    rewritten_sentence: str = ""
    max_deletions: int = 3
    count: Optional[int] = None


class VisualCardScenario(BaseModel):
    source_content: str
    question_text: str
    front_intent: str = ""
    back_intent: str = ""
    facts_to_test: List[str] = Field(default_factory=list)
    visual_prompt: str
    reference_image_policy: Literal["none", "style_reference", "source_reference"] = "none"
    layout: Literal[
        "image_front_text_back",
        "text_front_image_back",
        "image_both_sides",
        "image_front_image_back",
    ] = "text_front_image_back"
    image_count: Literal[1, 2] = 1
    answer_text: str
    rendering_guide: Optional[str] = None
    fallback_kind: Literal["basic", "cloze"] = "basic"
    voice_text: Optional[str] = None


class GeneratedMedia(BaseModel):
    path: str
    basename: str
    source: Literal["user_upload", "generated"]
    role: Literal["front", "back", "both", "reference", "audio"]
    metadata: Dict[str, str] = Field(default_factory=dict)


class RenderedCardSet(BaseModel):
    cards: List[AnkiCard]
    image_asset_plan: ImageAssetPlan
    generated_media: List[GeneratedMedia] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    usage_summary: Optional[WorkflowUsageSummary] = None


class RenderedCardEvaluation(BaseModel):
    accepted: bool
    issues: List[str] = Field(default_factory=list)
    severity: Literal["none", "low", "medium", "high"] = "none"
    repair_strategy: Literal[
        "accept",
        "repair_render",
        "retry_card_plan",
        "retry_scenario",
        "fallback",
    ] = "accept"
    guidance: str = ""
    visual_accepted: bool = True


class AnkiPipelineState(BaseModel):
    source: ContentSource
    directives: Optional[AnkiDirectiveConstraints] = None
    cleaned_content: str = ""
    image_asset_plan: Optional[ImageAssetPlan] = None
    build_plan: Optional[CardBuildPlan] = None
    text_scenario: Optional[TextCardScenario] = None
    cloze_scenario: Optional[ClozeCardScenario] = None
    visual_scenario: Optional[VisualCardScenario] = None
    rendered: Optional[RenderedCardSet] = None
    quality_evaluation: Optional[RenderedCardEvaluation] = None
    evaluation_decision: Optional[EvaluationDecision] = None
    generated_media: List[GeneratedMedia] = Field(default_factory=list)
    retry_counts: Dict[str, int] = Field(default_factory=dict)
    trace: List[WorkflowTraceEvent] = Field(default_factory=list)
    usage_summary: Optional[WorkflowUsageSummary] = None
