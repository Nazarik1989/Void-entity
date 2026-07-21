"""Immutable publication brief and relevance rules for VOID."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


EDITORIAL_CONTRACT_VERSION = "editorial-relevance.v1"
PERSONA_POLICY_VERSION = "void-persona.v2"
VISUAL_CODE_VERSION = "void-visual.v2"
MAX_REGENERATIONS = 2

POLICY_PRIORITY = (
    "security_and_access_control",
    "schedule_and_publication_type",
    "persona_identity",
    "current_editorial_contract",
    "persona_visual_bible",
    "music_allowlist_and_shared_rotation",
    "creative_variation",
)
SOURCE_TYPES = frozenset({
    "scheduled_rubric", "current_event_with_source", "canonical_story",
    "approved_backstage_seed", "explicit_admin_request", "continuation_with_reference",
})
DEFAULT_FORBIDDEN = (
    "unexplained elderly person as memory or wisdom", "sad person at a window",
    "child as a generic future symbol", "hands holding a glowing sphere",
    "random programmer at monitors", "humanoid robot as generic AI",
    "stock smiling team", "random luxury character", "Naz blue ultraviolet identity",
    "cyberpunk data network", "occult runes", "skulls or ravens as generic gothic decoration",
)
REASON_CODES = frozenset({
    "accepted", "invalid_brief", "unknown_source_type", "missing_source_reference",
    "unknown_rubric", "unknown_visual_code_version", "conflicting_visual_rules",
    "missing_people_justification", "text_missing_entry_context", "text_unknown_conversation",
    "text_invented_current_event", "text_topic_drift", "text_persona_mismatch",
    "image_subject_mismatch", "image_thesis_mismatch", "image_unexplained_people",
    "image_unexplained_elements", "image_visual_bible_mismatch", "image_why_here",
    "validator_unavailable", "generation_failed", "regeneration_exhausted", "fallback_forbidden",
    "schema_json_parse_failed", "schema_missing_fields", "schema_invalid_field_types",
    "schema_unknown_reason_code", "schema_conflicting_fields",
    "near_duplicate_semantics", "repeated_digital_attention_thesis",
    "brief_thesis_near_duplicate", "brief_novelty_exhausted",
})
TEXT_GATE_REASON_CODES = frozenset({
    "accepted", "text_missing_entry_context", "text_unknown_conversation",
    "text_invented_current_event", "text_topic_drift", "text_persona_mismatch",
})
IMAGE_GATE_REASON_CODES = frozenset({
    "accepted", "image_subject_mismatch", "image_thesis_mismatch",
    "image_unexplained_people", "image_unexplained_elements",
    "image_visual_bible_mismatch", "image_why_here",
})
NON_RETRYABLE_GATE_REASON_CODES = frozenset({
    "invalid_brief", "unknown_source_type", "missing_source_reference", "unknown_rubric",
    "unknown_visual_code_version", "conflicting_visual_rules", "missing_people_justification",
    "validator_unavailable", "schema_json_parse_failed", "schema_missing_fields",
    "schema_invalid_field_types", "schema_unknown_reason_code", "schema_conflicting_fields",
    "near_duplicate_semantics", "repeated_digital_attention_thesis",
    "brief_thesis_near_duplicate", "brief_novelty_exhausted",
})
TEXT_GATE_BOOLEAN_FIELDS = (
    "accepted", "entry_context_clear", "self_contained", "invented_current_event",
    "topic_matches", "persona_matches",
)
TEXT_GATE_REASON_BY_FAILED_CHECK = {
    "entry_context_clear": "text_missing_entry_context",
    "self_contained": "text_unknown_conversation",
    "invented_current_event": "text_invented_current_event",
    "topic_matches": "text_topic_drift",
    "persona_matches": "text_persona_mismatch",
}
IMAGE_GATE_BOOLEAN_FIELDS = (
    "accepted", "subject_matches", "thesis_supported", "unexplained_people",
    "unexplained_elements", "visual_bible_matches", "why_here",
)
IMAGE_GATE_REASON_BY_FAILED_CHECK = {
    "subject_matches": "image_subject_mismatch",
    "thesis_supported": "image_thesis_mismatch",
    "unexplained_people": "image_unexplained_people",
    "unexplained_elements": "image_unexplained_elements",
    "visual_bible_matches": "image_visual_bible_mismatch",
    "why_here": "image_why_here",
}


class BriefValidationError(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class GateResponseError(ValueError):
    def __init__(self, reason_code: str, field_names: Iterable[str] = ()):
        names = tuple(dict.fromkeys(str(name) for name in field_names if str(name)))
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field_names = names


@dataclass(frozen=True, slots=True)
class ContentBrief:
    editorial_contract_version: str
    post_id: str
    persona: str
    persona_policy_version: str
    destination: str
    scheduled_slot: str
    source_type: str
    source_reference: str
    rubric: str
    thesis: str
    context_reason: str
    visual_subject: str
    visual_relation: str
    people_allowed: bool
    allowed_people_description: str
    required_elements: tuple[str, ...]
    forbidden_elements: tuple[str, ...]
    visual_code_version: str
    music_required: bool
    exclusion_fingerprints: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["required_elements"] = list(self.required_elements)
        value["forbidden_elements"] = list(self.forbidden_elements)
        return value

    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def job_metadata(self) -> dict[str, Any]:
        return {
            "editorial_contract_version": self.editorial_contract_version,
            "persona_policy_version": self.persona_policy_version,
            "visual_code_version": self.visual_code_version,
            "post_id": self.post_id,
            "persona": self.persona,
            "destination": self.destination,
            "brief_hash": self.digest(),
            "source_type": self.source_type,
            "rubric": self.rubric,
            "music_required": self.music_required,
            "reason_code": "accepted",
        }


@dataclass(frozen=True, slots=True)
class ImageGateDecision:
    accepted: bool
    reason_code: str
    literal_description: str
    subject_matches: bool
    thesis_supported: bool
    unexplained_people: bool
    unexplained_elements: bool
    visual_bible_matches: bool
    why_here: bool


def make_post_id(destination: str, source_reference: str) -> str:
    digest = hashlib.sha256(f"void|{destination}|{source_reference}".encode()).hexdigest()[:24]
    return f"void-{digest}"


def validate_brief(brief: ContentBrief, *, allowed_rubrics: Iterable[str]) -> ContentBrief:
    if brief.editorial_contract_version != EDITORIAL_CONTRACT_VERSION:
        raise BriefValidationError("invalid_brief", "unknown editorial contract version")
    if brief.persona != "void" or brief.persona_policy_version != PERSONA_POLICY_VERSION:
        raise BriefValidationError("invalid_brief", "unknown VOID persona policy")
    if brief.destination not in {"telegram", "vk"}:
        raise BriefValidationError("invalid_brief", "unknown destination")
    if brief.source_type not in SOURCE_TYPES:
        raise BriefValidationError("unknown_source_type", "source type is not allowed")
    if not brief.source_reference.strip():
        raise BriefValidationError("missing_source_reference", "source reference is required")
    if brief.rubric not in set(allowed_rubrics):
        raise BriefValidationError("unknown_rubric", "rubric is not registered")
    if brief.visual_code_version != VISUAL_CODE_VERSION:
        raise BriefValidationError("unknown_visual_code_version", "visual code version is not registered")
    if not brief.scheduled_slot.strip() or not all((brief.thesis.strip(), brief.context_reason.strip(), brief.visual_subject.strip(), brief.visual_relation.strip())):
        raise BriefValidationError("invalid_brief", "brief context fields are required")
    if not re.fullmatch(r"void-[0-9a-f]{24}", brief.post_id):
        raise BriefValidationError("invalid_brief", "post id is not canonical")
    required = {" ".join(item.casefold().split()) for item in brief.required_elements}
    forbidden = {" ".join(item.casefold().split()) for item in brief.forbidden_elements}
    if required & forbidden:
        raise BriefValidationError("conflicting_visual_rules", "required and forbidden elements conflict")
    if brief.people_allowed:
        lowered = brief.allowed_people_description.casefold()
        if not all(marker in lowered for marker in ("who:", "action:", "why:")):
            raise BriefValidationError("missing_people_justification", "allowed people require who, action and why")
    elif brief.allowed_people_description.strip():
        raise BriefValidationError("conflicting_visual_rules", "people description conflicts with people_allowed")
    return brief


def build_brief(*, destination: str, scheduled_slot: str, source_type: str,
                source_reference: str, rubric: str, thesis: str, context_reason: str,
                visual_subject: str, visual_relation: str, allowed_rubrics: Iterable[str],
                people_allowed: bool = False, allowed_people_description: str = "",
                required_elements: Iterable[str] = (), forbidden_elements: Iterable[str] = (),
                music_required: bool = False,
                exclusion_fingerprints: Iterable[str] = ()) -> ContentBrief:
    brief = ContentBrief(
        EDITORIAL_CONTRACT_VERSION, make_post_id(destination, source_reference), "void",
        PERSONA_POLICY_VERSION, destination, scheduled_slot, source_type, source_reference,
        rubric, thesis.strip(), context_reason.strip(), visual_subject.strip(), visual_relation.strip(),
        bool(people_allowed), allowed_people_description.strip(),
        tuple(str(x).strip() for x in required_elements if str(x).strip()),
        tuple(dict.fromkeys((*DEFAULT_FORBIDDEN, *(str(x).strip() for x in forbidden_elements if str(x).strip())))),
        VISUAL_CODE_VERSION, bool(music_required),
        tuple(dict.fromkeys(str(x).strip() for x in exclusion_fingerprints if str(x).strip())),
    )
    return validate_brief(brief, allowed_rubrics=allowed_rubrics)


def brief_from_json(raw: str, *, allowed_rubrics: Iterable[str]) -> ContentBrief:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise BriefValidationError("invalid_brief", "brief must be an object")
    value["required_elements"] = tuple(value.get("required_elements") or ())
    value["forbidden_elements"] = tuple(value.get("forbidden_elements") or ())
    value["exclusion_fingerprints"] = tuple(value.get("exclusion_fingerprints") or ())
    try:
        brief = ContentBrief(**value)
    except TypeError as exc:
        raise BriefValidationError("invalid_brief", "brief fields are invalid") from exc
    return validate_brief(brief, allowed_rubrics=allowed_rubrics)


def render_text_instructions(brief: ContentBrief, persona_rules: str) -> str:
    return f"""POLICY PRIORITY: security/access > schedule/type > persona > current editorial contract > visual bible > music allowlist/shared rotation > creative variation.
PERSONA POLICY ({brief.persona_policy_version}):
{persona_rules.strip()}
IMMUTABLE CONTENT BRIEF ({brief.editorial_contract_version}):
post_id: {brief.post_id}
destination: {brief.destination}
scheduled_slot: {brief.scheduled_slot}
source_type: {brief.source_type}
source_reference: {brief.source_reference}
rubric: {brief.rubric}
thesis: {brief.thesis}
context_reason: {brief.context_reason}
visual_subject: {brief.visual_subject}
visual_relation: {brief.visual_relation}
The first paragraph must provide entry context. The post must stand alone, must not invent a current event, and must not change the source, rubric, thesis, persona, or destination. Return only the requested structured candidate.""".strip()


def render_visual_instructions(brief: ContentBrief, visual_rules: str) -> str:
    people = (f"People only as specified: {brief.allowed_people_description}" if brief.people_allowed
              else "No people, faces, silhouettes, or humanoid figures.")
    required = "\n".join(f"- {x}" for x in brief.required_elements) or "- none"
    forbidden = "\n".join(f"- {x}" for x in brief.forbidden_elements)
    return f"""VISUAL POLICY ({brief.visual_code_version}). Implement the immutable brief without replacing its subject or thesis.
visual_subject: {brief.visual_subject}
visual_relation: {brief.visual_relation}
thesis: {brief.thesis}
{people}
Required elements:
{required}
Forbidden elements:
{forbidden}
CANONICAL VOID VISUAL BIBLE:
{visual_rules.strip()}
The image must answer why it belongs to this exact post. No fallback subject or unrelated decoration.""".strip()


def text_gate_brief_payload(brief: ContentBrief) -> dict[str, Any]:
    return {
        "editorial_contract_version": brief.editorial_contract_version,
        "post_id": brief.post_id,
        "persona": brief.persona,
        "persona_policy_version": brief.persona_policy_version,
        "destination": brief.destination,
        "scheduled_slot": brief.scheduled_slot,
        "source_type": brief.source_type,
        "source_reference": brief.source_reference,
        "rubric": brief.rubric,
        "thesis": brief.thesis,
        "context_reason": brief.context_reason,
    }


def text_gate_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "accepted": {"type": "boolean"},
            "reason_code": {"type": "string", "enum": sorted(TEXT_GATE_REASON_CODES)},
            "entry_context_clear": {"type": "boolean"},
            "self_contained": {"type": "boolean"},
            "invented_current_event": {"type": "boolean"},
            "topic_matches": {"type": "boolean"},
            "persona_matches": {"type": "boolean"},
        },
        "required": ["reason_code", *TEXT_GATE_BOOLEAN_FIELDS],
        "additionalProperties": False,
    }


def build_text_gate_prompt(brief: ContentBrief, candidate: str) -> str:
    return json.dumps({
        "task": "accept_or_reject_text_only",
        "brief": text_gate_brief_payload(brief),
        "candidate": candidate,
        "checks": [
            "first paragraph gives entry context", "self-contained",
            "no invented current event", "topic matches", "VOID persona matches",
        ],
        "allowed_reason_codes": sorted(TEXT_GATE_REASON_CODES),
        "reason_code_by_failed_check": TEXT_GATE_REASON_BY_FAILED_CHECK,
        "schema": text_gate_response_schema(),
    }, ensure_ascii=False, separators=(",", ":"))


def parse_text_gate_response(raw: str) -> tuple[bool, str]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except (TypeError, ValueError) as exc:
        raise GateResponseError("schema_json_parse_failed") from exc
    if not isinstance(value, dict):
        raise GateResponseError("schema_invalid_field_types", ("response",))
    required_fields = ("reason_code", *TEXT_GATE_BOOLEAN_FIELDS)
    missing = tuple(name for name in required_fields if name not in value)
    if missing:
        raise GateResponseError("schema_missing_fields", missing)
    reason = str(value.get("reason_code") or "")
    if reason not in TEXT_GATE_REASON_CODES:
        raise GateResponseError("schema_unknown_reason_code", ("reason_code",))
    fields = {name: value.get(name) for name in TEXT_GATE_BOOLEAN_FIELDS}
    invalid_types = tuple(name for name, item in fields.items() if not isinstance(item, bool))
    if invalid_types:
        raise GateResponseError("schema_invalid_field_types", invalid_types)
    checks_accept = (
        fields["entry_context_clear"] and fields["self_contained"]
        and not fields["invented_current_event"] and fields["topic_matches"]
        and fields["persona_matches"]
    )
    accepted = fields["accepted"]
    failed_reasons = {
        TEXT_GATE_REASON_BY_FAILED_CHECK[name]
        for name in TEXT_GATE_REASON_BY_FAILED_CHECK
        if (fields[name] is False and name != "invented_current_event")
        or (name == "invented_current_event" and fields[name] is True)
    }
    if accepted != checks_accept:
        raise GateResponseError("schema_conflicting_fields", TEXT_GATE_BOOLEAN_FIELDS)
    if accepted and reason != "accepted":
        raise GateResponseError("schema_conflicting_fields", ("accepted", "reason_code"))
    if not accepted and reason not in failed_reasons:
        raise GateResponseError("schema_conflicting_fields", ("reason_code",))
    return accepted, reason


def is_retryable_gate_reason(reason_code: str) -> bool:
    return reason_code not in NON_RETRYABLE_GATE_REASON_CODES


def image_gate_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "accepted": {"type": "boolean"},
            "reason_code": {"type": "string", "enum": sorted(IMAGE_GATE_REASON_CODES)},
            "literal_description": {"type": "string"},
            "subject_matches": {"type": "boolean"},
            "thesis_supported": {"type": "boolean"},
            "unexplained_people": {"type": "boolean"},
            "unexplained_elements": {"type": "boolean"},
            "visual_bible_matches": {"type": "boolean"},
            "why_here": {
                "type": "boolean",
                "description": "true only when relevance to this exact post is unclear",
            },
        },
        "required": ["reason_code", "literal_description", *IMAGE_GATE_BOOLEAN_FIELDS],
        "additionalProperties": False,
    }


def build_image_gate_prompt(brief: ContentBrief) -> str:
    return json.dumps({
        "task": "accept_or_reject_image_only",
        "brief": brief.as_dict(),
        "checks": [
            "describe only what is literally depicted", "visual_subject matches",
            "thesis is supported", "no unexplained people", "no unexplained elements",
            "visual bible matches", "why_here is false when relevance is clear",
        ],
        "allowed_reason_codes": sorted(IMAGE_GATE_REASON_CODES),
        "reason_code_rule": (
            "Use accepted only when every check passes. Otherwise choose the code "
            "mapped to one failed check."
        ),
        "reason_code_by_failed_check": IMAGE_GATE_REASON_BY_FAILED_CHECK,
        "schema": image_gate_response_schema(),
    }, ensure_ascii=False, separators=(",", ":"))


def parse_image_gate_response(raw: str) -> ImageGateDecision:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
    try:
        value: Mapping[str, Any] = json.loads(cleaned)
    except (TypeError, ValueError) as exc:
        raise GateResponseError("schema_json_parse_failed") from exc
    if not isinstance(value, dict):
        raise GateResponseError("schema_invalid_field_types", ("response",))
    required_fields = ("reason_code", "literal_description", *IMAGE_GATE_BOOLEAN_FIELDS)
    missing = tuple(name for name in required_fields if name not in value)
    if missing:
        raise GateResponseError("schema_missing_fields", missing)
    reason = str(value.get("reason_code") or "")
    if reason not in IMAGE_GATE_REASON_CODES:
        raise GateResponseError("schema_unknown_reason_code", ("reason_code",))
    fields = {name: value.get(name) for name in IMAGE_GATE_BOOLEAN_FIELDS}
    invalid_types = tuple(name for name, item in fields.items() if not isinstance(item, bool))
    if not isinstance(value.get("literal_description"), str):
        invalid_types += ("literal_description",)
    if invalid_types:
        raise GateResponseError("schema_invalid_field_types", invalid_types)
    accepted = bool(fields["accepted"])
    checks = fields["subject_matches"] and fields["thesis_supported"] and not fields["unexplained_people"] and not fields["unexplained_elements"] and fields["visual_bible_matches"] and not fields["why_here"]
    failed_reasons = {
        IMAGE_GATE_REASON_BY_FAILED_CHECK[name]
        for name in IMAGE_GATE_REASON_BY_FAILED_CHECK
        if (fields[name] is False and name not in {"unexplained_people", "unexplained_elements", "why_here"})
        or (name in {"unexplained_people", "unexplained_elements", "why_here"} and fields[name] is True)
    }
    if accepted != checks:
        raise GateResponseError("schema_conflicting_fields", IMAGE_GATE_BOOLEAN_FIELDS)
    if accepted and reason != "accepted":
        raise GateResponseError("schema_conflicting_fields", ("accepted", "reason_code"))
    if not accepted and reason not in failed_reasons:
        raise GateResponseError("schema_conflicting_fields", ("reason_code",))
    return ImageGateDecision(reason_code=reason, literal_description=str(value.get("literal_description") or "")[:500], **fields)
