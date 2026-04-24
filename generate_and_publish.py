from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request

HAS_PYPDF2 = importlib.util.find_spec("PyPDF2") is not None

if HAS_PYPDF2:
    from PyPDF2 import PdfReader


# ============================================================
# CLI
# ============================================================

parser = argparse.ArgumentParser(
    description="Generate Swiss immigration blog drafts with staged legal validation."
)
parser.add_argument("--dry-run", action="store_true", help="Skip network calls and topic writes.")
parser.add_argument(
    "--topic-index",
    type=int,
    default=None,
    help="Use a specific topics.json index instead of the first unused topic.",
)
parser.add_argument(
    "--allow-editorial-fallback",
    action="store_true",
    help=(
        "If no legal authorities are found, continue using internal legal notes only. "
        "Never uses website editorial as legal authority."
    ),
)
parser.add_argument(
    "--auto-mark-used-on-review-recommended",
    action="store_true",
    help=(
        "If the final publication gate recommends human review, still mark the topic as used. "
        "Default is conservative: send the draft but leave the topic unused where human review is recommended."
    ),
)
args = parser.parse_args()


# ============================================================
# Environment and paths
# ============================================================

def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "info@richmondchambers.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "paul.richmond@richmondchambers.com")
REPLY_TO = os.environ.get("EMAIL_REPLY_TO", EMAIL_TO)

CTA_HEADING = "Contact Our Immigration Lawyers In Switzerland"
CTA_NAME = "Richmond Chambers Switzerland"
CTA_PHONE = "+41 21 588 07 70"

SCRIPT_DIR = Path(__file__).resolve().parent
TOPICS_PATH = SCRIPT_DIR / "topics.json"
KNOWLEDGE_DIR = SCRIPT_DIR / "knowledge"
LEGAL_AUTHORITIES_DIR = KNOWLEDGE_DIR / "legal_authorities"
INTERNAL_NOTES_DIR = KNOWLEDGE_DIR / "internal_legal_notes"
WEBSITE_EDITORIAL_DIR = KNOWLEDGE_DIR / "website_editorial"
OUTPUT_DIR = SCRIPT_DIR / "generated_blog_runs"
AUTHORITY_MAP_PATH = SCRIPT_DIR / "authority_pack_map.json"


# ============================================================
# Editorial constants
# ============================================================

MAX_BLOG_WORDS = 1500
TARGET_BLOG_WORDS = "1,050 to 1,300"
MAX_EDITORIAL_REVISIONS = 5

FORBIDDEN_PUBLIC_PHRASES = [
    "the memo",
    "the verified memo",
    "the materials support",
    "the verified materials",
    "for this specific issue, the verified materials say",
    "any article on this topic",
    "the drafting inputs",
    "the source packs",
    "the legal memo",
    "the supplied materials",
    "the source material",
]

GENERIC_FILLER_PHRASES = [
    "that distinction matters",
    "the practical point is",
    "it is important to note",
    "it is worth noting",
    "navigating",
    "complex landscape",
    "in today's globalised world",
    "whether you are",
    "this article explores",
    "this article delves",
]

TITLE_OVERCLAIM_PATTERNS = [
    r"\bthe real problem is\b",
    r"\bthe only reason\b",
    r"\balways\b",
    r"\bnever\b",
    r"\bguarantee[sd]?\b",
    r"\bwill be approved\b",
    r"\bwill succeed\b",
    r"\bmust fail\b",
    r"\bno chance\b",
]

VAGUE_CATEGORY_PATTERNS = [
    r"\bsome nationalities\b",
    r"\bcertain nationalities\b",
    r"\bsome countries\b",
    r"\bcertain countries\b",
    r"\bsome cantons\b",
    r"\bcertain cantons\b",
    r"\bsome applicants\b",
    r"\bcurrent guidance\b",
    r"\bofficial guidance\b",
    r"\bthe authorities\b",
    r"\busually\b",
    r"\bin most cases\b",
    r"\bEU/EFTA nationals\b",
    r"\bfamily members\b",
    r"\bstrong integration\b",
]

LEGAL_AUTHORITY_PATTERN = r"(Article\s+\d|Art\.\s*\d|SEM Directives|SEM guidance|SEM Weisungen|LEI / AIG|OASA / VZAE)"


# ============================================================
# Prompts
# ============================================================

CLASSIFIER_INSTRUCTIONS = """
You are assisting a Swiss immigration law content workflow.
Classify the requested article before drafting.
Return strict JSON only.
Do not write the article.
""".strip()

SOURCE_AUDIT_INSTRUCTIONS = """
You are auditing retrieved sources before a Swiss immigration law article is drafted.

Return strict JSON only.

Assess whether the retrieved material is relevant to the requested topic.

Important:
- Do not block drafting merely because the material is a curated authority pack rather than verbatim legislation.
- Do not block drafting merely because canton-specific material, forms, fees, nationality lists, or current SEM extracts would improve precision.
- Treat missing primary-law quotations or canton-specific detail as drafting cautions unless the retrieved legal material is irrelevant or absent.
- Website editorial may help positioning, but must not be treated as legal authority.
- If a point needs current official verification, identify it as a warning and instruct the draft to phrase it cautiously.
- Drafting is safe if there is relevant legal authority or internal legal material sufficient to support a cautious, general legal article.

Only set safe_to_draft to false where:
- no relevant legal authority or internal legal note has been retrieved;
- the retrieved material is plainly unrelated to the topic;
- the topic requires current factual data, such as fees or forms, and no source is available at all;
- drafting would require unsupported legal claims.

Even where safe_to_draft is false, provide practical drafting cautions rather than refusing to help.
""".strip()

LEGAL_MEMO_INSTRUCTIONS = """
You are preparing an internal legal analysis note for a Swiss immigration law article.

Source hierarchy:
1. legal_authority
2. internal_legal_note
3. website_editorial

Rules:
- Use legal_authority as the basis for legal propositions wherever available.
- Use internal_legal_note only as a supporting interpretive source.
- Do not treat website_editorial as legal authority.
- If a source is missing for a point, say so explicitly.
- Distinguish between settled legal framework, entitlement, eligibility, facilitated access, discretion, guidance, policy, procedure, evidential issues and cantonal practice.
- Do not describe guidance as law.
- Do not describe discretionary routes as rights.
- Do not describe cantonal practice as uniform Swiss law.
- Identify exceptions, preservation mechanisms, qualifications and reader-category distinctions.
- If EU/EFTA and non-EU treatment differs, state that.
- If nationality-specific routes, family categories, sponsor categories or canton-specific handling matter, state that precisely.
- Identify practical decision points for a prospective client.
- Identify evidence/documents that a lawyer would usually want to review.
- Identify common mistakes and refusal risks.
- State which propositions are safe for public article use and which need cautious wording.
- Source-audit gaps are drafting cautions, not reasons to refuse drafting.

Return strict JSON only.
""".strip()

VERIFIER_INSTRUCTIONS = """
You are reviewing an internal legal memo for legal and editorial risk before a client-facing article is drafted.

Return strict JSON only.

Check expressly for:
- confusion between "can apply" and "will be granted"
- confusion between eligibility and entitlement
- discretionary routes described as rights
- guidance described as law
- cantonal practice described as uniform Swiss law
- unsupported nationality categories
- unsupported permit timelines
- unsupported fee or process claims
- overbroad statements about EU/EFTA nationals
- overbroad statements about marriage, family members, employment, income, property ownership or tax contribution
- missing refusal grounds
- missing evidence requirements
- missing reader-category distinctions
- duplication or cannibalisation risk against existing website editorial

Important:
- Your role is to identify legal risks and required drafting caution.
- Do not use publication_blockers unless the article would require unsupported legal claims that cannot be avoided by cautious wording.
- Prefer mandatory_revisions and claims_requiring_exact_source_reference over blocking publication.
- The downstream draft should use your review to produce a legally careful, publication-ready article.
""".strip()

OVERLAP_INSTRUCTIONS = """
You are assessing whether a proposed Swiss immigration law blog post overlaps with existing firm content.

Return strict JSON only.

Use the website editorial material only for content differentiation, overlap avoidance and internal-link suggestions.
Do not treat website editorial as legal authority.

Identify:
- similar existing articles or route pages
- what the new article should do differently
- what the draft must avoid repeating
- internal link opportunities
- whether cannibalisation risk is low, medium or high

If cannibalisation risk is high, identify a distinct angle if one exists.
If no distinct angle exists, still recommend the narrowest workable angle rather than blocking the article.
""".strip()

DRAFT_INSTRUCTIONS = f"""
You are drafting a client-facing blog post for a Swiss immigration law firm.
Draft only from the verified legal memo, source audit, overlap analysis and editorial instructions supplied.
Do not add new legal propositions not present in the verified memo or supported legal sources.

Core editorial target:
- Write like a careful Swiss immigration practitioner advising a discerning prospective client.
- Be concise but still thorough.
- Be specific rather than generic.
- Be practical rather than merely descriptive.
- Be authoritative without overclaiming.
- Be conversion-aware without sounding like marketing copy.

Length and structure:
- Target approximately {TARGET_BLOG_WORDS} words.
- Hard maximum: {MAX_BLOG_WORDS} words.
- Do not exceed {MAX_BLOG_WORDS} words under any circumstances.
- Avoid repetition. Each section must do distinct work.
- Do not restate the same legal proposition in multiple sections.
- If a point has already been made, develop it with consequence, evidence or next step rather than repeating it.

Required article structure where appropriate:
- strong SEO-conscious title, without overclaiming
- bold opening paragraph
- second introductory paragraph
- a short **Quick Answer**, **At a Glance** or **In Brief** section near the top
- clear legal framework section
- who qualifies or is affected
- who may not qualify or where the misunderstanding lies
- evidence/document requirements
- common mistakes or refusal risks
- practical examples or short scenarios where useful
- concise checklist or ordered action framework
- **What This Means in Practice**
- **What To Do Next**
- restrained CTA
- short legal disclaimer before the CTA or near the end

Writing requirements:
- UK English.
- Calm, authoritative, analytical and natural.
- Professional but clear and easy to understand.
- Explain legal issues in practical language that a non-lawyer can follow.
- Write as a human legal professional, not as an internal system.
- Never refer to "the memo", "the verified memo", "the materials", "the verified materials", "any article on this topic", source packs, drafting inputs or internal validation.
- Avoid empty emphasis, filler and generic transition language.
- Avoid formulaic phrases such as "that distinction matters", "the practical point is", and "it is important to note" unless genuinely necessary.
- Prefer concrete framing over broad marketing abstractions.

Formatting requirements:
- Use keyword-optimised sub-headings throughout.
- Every sub-heading must be surrounded by a blank line above and below.
- Format each sub-heading in bold using double asterisks only, for example: **How Student Residence Is Counted for a Swiss C Permit**
- The first paragraph of the article must be fully bold using double asterisks.
- Immediately after the opening paragraph, include a second introductory paragraph explaining what the post will cover, who it is useful for, and what the reader will learn.
- Do not include legal authorities in the first paragraph.
- Do not include legal authorities in the second introductory paragraph.
- Use compact bullet or numbered lists only where they improve clarity.
- You may use simple markdown tables where a comparison is genuinely clearer as a table.
- No emojis.

Legal authority requirements:
- Include a small number of short legal authority references throughout the body after the first two paragraphs.
- Legal authority references must be legally accurate and should include article numbers wherever relevant.
- Always use LEI / AIG, never AIG on its own.
- Always use OASA / VZAE, never VZAE on its own.
- Refer to named official sources where supported, such as SEM guidance, SEM Weisungen AIG, or specific articles supplied by the memo.

Handling source-audit cautions:
- Source-audit gaps are not reasons to refuse drafting. Use them to avoid overstatement.
- If primary wording, canton-specific practice, nationality lists, forms, fees or current process details are not available, do not invent them.
- Instead, state the general legal position supported by the authority packs and say that current official guidance or canton-specific handling should be checked where necessary.
- Do not tell the reader that the article cannot answer the question; give the most useful cautious answer supported by the available legal material.

Practicality requirements:
- Include a concise section headed exactly: **What This Means in Practice**
- Include a concise section headed exactly: **What To Do Next**
- The "What To Do Next" section must give an ordered framework in prose or a short numbered list, covering as relevant:
  1. identify the exact decision, risk or problem;
  2. check the route, permit category or legal basis;
  3. identify the real weakness: legal, evidential, timing-related, procedural or discretionary;
  4. gather the key documents/evidence;
  5. decide whether appeal, reapplication, waiting, preserving status or another strategy is more realistic.
- Where useful, include a short practical checklist or examples of document types a lawyer would want to review.
- Where useful, include one or two brief anonymised scenarios or patterns.
- Do not invent facts, lists, nationality categories or canton-specific practice.

Specificity and uncertainty:
- Where the article refers to "some nationalities", "certain countries", "some cantons" or similar, either name the relevant category accurately from the legal memo or say that the point must be checked against current official guidance from the named official body.
- Distinguish clearly between settled legal framework, canton-specific practice, evidential issues and genuinely discretionary areas.
- Use caution where needed, but avoid repeated hedging.

Disclaimer:
- Include a short disclaimer before the CTA or immediately before the end.
- It must say in substance that the article summarises Swiss immigration law/guidance at the date of writing, individual facts/evidence/cantonal handling may affect the outcome, and it is not legal advice.

SEO and differentiation:
- Integrate relevant SEO keywords and keyword variations naturally.
- Follow the distinct angle from the overlap analysis.
- Avoid repeating existing firm content listed in the overlap analysis.
- Do not let SEO phrasing dominate the article.

CTA:
- The final CTA heading must be exactly: {CTA_HEADING}
- The CTA must be concrete and restrained.
- It should explain what {CTA_NAME} would review or do, such as reviewing the permit history, decision, route, legal basis, evidence, timing and options.
- Invite readers to contact {CTA_NAME} by telephone on {CTA_PHONE} or by completing an enquiry form to arrange an initial consultation meeting.
- Avoid generic sales language.

Under DYNAMIC PAGE LINK, return an empty string only.

Output strict JSON only.
""".strip()

EDITORIAL_REVISER_INSTRUCTIONS = f"""
You are revising a client-facing Swiss immigration law blog post before publication.

Your task:
- Preserve legal accuracy.
- Do not add new legal propositions not supported by the verified legal memo.
- Fix every editorial validation issue supplied.
- Hard maximum: {MAX_BLOG_WORDS} words. If the draft exceeds this, shorten it before doing anything else.
- Prefer deleting repetition over adding explanation.
- Make each section do distinct work.
- Make the article more practical, decision-oriented and useful to a prospective client.
- Preserve required formatting.
- Preserve legal authority style: LEI / AIG and OASA / VZAE.
- Keep legal references out of the first two paragraphs.
- Keep a bold first paragraph.
- Keep a second introductory paragraph explaining what the post covers and who it helps.
- Ensure a near-top section headed **Quick Answer**, **At a Glance** or **In Brief**.
- Ensure sections headed exactly:
  **What This Means in Practice**
  **What To Do Next**
- Ensure a short disclaimer is present before the CTA or near the end.
- Ensure the CTA is concrete, not generic.
- Remove internal-sounding phrases such as "the memo", "the verified materials", "the materials support".
- Avoid overclaiming in the title.
- Replace vague category wording with either a named category supported by the memo or a clear reference to named current official guidance.
- Follow the distinct angle and internal-link strategy from the overlap analysis.
- Treat source-audit issues as cautions to be solved through careful wording, not as reasons to refuse drafting.

Return strict JSON only using the blog_draft schema.
""".strip()

SEO_INSTRUCTIONS = """
You are generating SEO metadata for a Swiss immigration law article.
Return strict JSON only.

Requirements:
- meta title max 60 characters
- meta description max 155 characters
- suggested slug
- primary keyword
- secondary keywords
- internal link anchor suggestions
- external authority link suggestions
- FAQ questions where useful
- natural, non-promotional, legally accurate
- avoid melodramatic or absolute wording
- avoid keyword stuffing
""".strip()


# ============================================================
# Schemas
# ============================================================

CLASSIFIER_SCHEMA = {
    "name": "blog_classifier",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "primary_audience": {"type": "string"},
            "article_type": {"type": "string"},
            "search_intent": {"type": "string"},
            "legal_complexity": {"type": "string", "enum": ["low", "medium", "high"]},
            "key_issues": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 10},
            "distinctions_required": {"type": "array", "items": {"type": "string"}},
            "source_needs": {"type": "array", "items": {"type": "string"}},
            "style_profile": {"type": "string"},
        },
        "required": [
            "primary_audience", "article_type", "search_intent", "legal_complexity",
            "key_issues", "distinctions_required", "source_needs", "style_profile",
        ],
    },
}

SOURCE_AUDIT_SCHEMA = {
    "name": "source_audit",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "official_legal_sources_found": {"type": "array", "items": {"type": "string"}},
            "internal_notes_found": {"type": "array", "items": {"type": "string"}},
            "website_editorial_found": {"type": "array", "items": {"type": "string"}},
            "source_gaps": {"type": "array", "items": {"type": "string"}},
            "stale_or_uncertain_sources": {"type": "array", "items": {"type": "string"}},
            "must_verify_before_publication": {"type": "array", "items": {"type": "string"}},
            "recommended_source_references": {"type": "array", "items": {"type": "string"}},
            "safe_to_draft": {"type": "boolean"},
        },
        "required": [
            "official_legal_sources_found", "internal_notes_found", "website_editorial_found",
            "source_gaps", "stale_or_uncertain_sources", "must_verify_before_publication",
            "recommended_source_references", "safe_to_draft",
        ],
    },
}

LEGAL_MEMO_SCHEMA = {
    "name": "legal_memo",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "article_positioning": {"type": "string"},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "issue": {"type": "string"},
                        "rule": {"type": "string"},
                        "legal_basis": {"type": "string"},
                        "authority_type": {
                            "type": "string",
                            "enum": [
                                "statute", "ordinance", "official_guidance", "case_law",
                                "cantonal_practice", "internal_note", "editorial_context", "mixed", "unclear",
                            ],
                        },
                        "entitlement_or_discretion": {
                            "type": "string",
                            "enum": [
                                "entitlement", "eligibility_only", "facilitated_access",
                                "discretionary", "unclear",
                            ],
                        },
                        "reader_category": {"type": "string"},
                        "exceptions": {"type": "array", "items": {"type": "string"}},
                        "procedure_points": {"type": "array", "items": {"type": "string"}},
                        "cantonal_practice_points": {"type": "array", "items": {"type": "string"}},
                        "reader_distinctions": {"type": "array", "items": {"type": "string"}},
                        "evidence_needed": {"type": "array", "items": {"type": "string"}},
                        "common_mistakes": {"type": "array", "items": {"type": "string"}},
                        "client_decision_points": {"type": "array", "items": {"type": "string"}},
                        "practical_implications": {"type": "array", "items": {"type": "string"}},
                        "source_reference_to_use_in_article": {"type": "string"},
                        "safe_public_formulation": {"type": "string"},
                        "cautious_public_formulation": {"type": "string"},
                        "support": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "source_type": {"type": "string"},
                                    "source_name": {"type": "string"},
                                    "excerpt": {"type": "string"},
                                },
                                "required": ["source_type", "source_name", "excerpt"],
                            },
                        },
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": [
                        "issue", "rule", "legal_basis", "authority_type",
                        "entitlement_or_discretion", "reader_category", "exceptions",
                        "procedure_points", "cantonal_practice_points", "reader_distinctions",
                        "evidence_needed", "common_mistakes", "client_decision_points",
                        "practical_implications", "source_reference_to_use_in_article",
                        "safe_public_formulation", "cautious_public_formulation",
                        "support", "confidence",
                    ],
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["article_positioning", "issues", "open_questions"],
    },
}

VERIFIER_SCHEMA = {
    "name": "memo_review",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "publishable": {"type": "boolean"},
            "legal_risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "publication_blockers": {"type": "array", "items": {"type": "string"}},
            "mandatory_revisions": {"type": "array", "items": {"type": "string"}},
            "claims_requiring_exact_source_reference": {"type": "array", "items": {"type": "string"}},
            "duplication_risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "recommended_positioning": {"type": "string"},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "overbroad_claims": {"type": "array", "items": {"type": "string"}},
            "missing_qualifications": {"type": "array", "items": {"type": "string"}},
            "cantonal_sensitivity": {"type": "array", "items": {"type": "string"}},
            "required_reader_distinctions": {"type": "array", "items": {"type": "string"}},
            "revision_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "publishable", "legal_risk_level", "publication_blockers", "mandatory_revisions",
            "claims_requiring_exact_source_reference", "duplication_risk", "recommended_positioning",
            "unsupported_claims", "overbroad_claims", "missing_qualifications",
            "cantonal_sensitivity", "required_reader_distinctions", "revision_actions",
        ],
    },
}

OVERLAP_SCHEMA = {
    "name": "overlap_analysis",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "similar_existing_articles": {"type": "array", "items": {"type": "string"}},
            "overlap_summary": {"type": "string"},
            "new_article_distinct_angle": {"type": "string"},
            "internal_link_recommendations": {"type": "array", "items": {"type": "string"}},
            "avoid_repeating": {"type": "array", "items": {"type": "string"}},
            "recommended_title_angle": {"type": "string"},
            "cannibalisation_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": [
            "similar_existing_articles", "overlap_summary", "new_article_distinct_angle",
            "internal_link_recommendations", "avoid_repeating", "recommended_title_angle",
            "cannibalisation_risk",
        ],
    },
}

DRAFT_SCHEMA = {
    "name": "blog_draft",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "blog_title": {"type": "string"},
            "dynamic_page_link": {"type": "string"},
            "blog_content": {"type": "string"},
        },
        "required": ["blog_title", "dynamic_page_link", "blog_content"],
    },
}

SEO_SCHEMA = {
    "name": "blog_seo",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "seo_meta_title": {"type": "string"},
            "seo_meta_description": {"type": "string"},
            "suggested_seo_keywords": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 6},
            "suggested_slug": {"type": "string"},
            "primary_keyword": {"type": "string"},
            "secondary_keywords": {"type": "array", "items": {"type": "string"}},
            "internal_link_anchor_suggestions": {"type": "array", "items": {"type": "string"}},
            "external_authority_link_suggestions": {"type": "array", "items": {"type": "string"}},
            "faq_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "seo_meta_title", "seo_meta_description", "suggested_seo_keywords",
            "suggested_slug", "primary_keyword", "secondary_keywords",
            "internal_link_anchor_suggestions", "external_authority_link_suggestions",
            "faq_questions",
        ],
    },
}


# ============================================================
# Models
# ============================================================

@dataclass
class KnowledgeChunk:
    source_name: str
    source_kind: str
    text: str


@dataclass
class EditorialValidationResult:
    passed: bool
    issues: list[str]
    warnings: list[str]
    repetition_score: float


@dataclass
class SEOValidationResult:
    passed: bool
    issues: list[str]
    warnings: list[str]


@dataclass
class PublicationGateResult:
    passed: bool
    blockers: list[str]
    warnings: list[str]
    human_review_recommended: bool
    final_word_count: int
    revision_count: int


# ============================================================
# HTTP and OpenAI helpers
# ============================================================

def post_json(url: str, payload: dict, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req) as response:
            raw = response.read()
            body = raw.decode("utf-8", errors="replace").strip()
            if not body:
                return response.status, {}
            return response.status, json.loads(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc.reason}") from exc


def call_responses_api(
    api_key: str,
    *,
    instructions: str,
    input_text: str,
    schema: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": model,
        "input": input_text,
        "instructions": instructions,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema["name"],
                "schema": schema["schema"],
                "strict": True,
            }
        },
    }

    _, response = post_json("https://api.openai.com/v1/responses", payload=payload, headers=headers)

    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return json.loads(output_text)

    for item in response.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return json.loads(text)

    raise RuntimeError(f"Unexpected Responses API payload: {response}")


# ============================================================
# Knowledge loading
# ============================================================

def load_authority_pack_map(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Authority pack map not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_authority_pack_paths(topic_entry: dict[str, Any], authority_map: dict[str, Any]) -> list[Path]:
    pillar = (topic_entry.get("pillar") or "").strip()
    subtopic = (topic_entry.get("subtopic") or "").strip()
    article_type = (topic_entry.get("article_type") or "").strip()
    legal_complexity = (topic_entry.get("legal_complexity") or "").strip()

    selected: list[str] = []
    selected.extend(authority_map.get("pillar_defaults", {}).get(pillar, []))
    selected.extend(authority_map.get("subtopic_overrides", {}).get(f"{pillar}:{subtopic}", []))
    selected.extend(authority_map.get("article_type_extras", {}).get(article_type, []))
    selected.extend(authority_map.get("legal_complexity_extras", {}).get(legal_complexity, []))

    deduped: list[str] = []
    seen = set()
    for item in selected:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return [LEGAL_AUTHORITIES_DIR / rel_path for rel_path in deduped]


def read_pdf_text(path: Path) -> str:
    if not HAS_PYPDF2:
        return ""

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            pages.append(f"[page {page_num}] {text}")
    return "\n".join(pages)


def load_selected_legal_authority_chunks(pdf_paths: list[Path]) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []

    for pdf_path in pdf_paths:
        if not pdf_path.exists():
            raise RuntimeError(f"Mapped legal authority pack not found: {pdf_path}")

        text = read_pdf_text(pdf_path)
        if text:
            chunks.append(KnowledgeChunk(str(pdf_path.relative_to(SCRIPT_DIR)), "legal_authority", text))

    return chunks


def load_chunks_from_folder(folder: Path, source_kind: str) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    if not folder.is_dir():
        return chunks
    for pdf_path in sorted(folder.glob("*.pdf")):
        text = read_pdf_text(pdf_path)
        if text:
            chunks.append(KnowledgeChunk(pdf_path.name, source_kind, text))
    return chunks


def tokenize_queries(queries: Iterable[str]) -> list[str]:
    tokens: list[str] = []
    for query in queries:
        for token in re.findall(r"[A-Za-z0-9/+-]+", query):
            token = token.lower().strip()
            if len(token) >= 3:
                tokens.append(token)
    return tokens


def simple_retrieve(
    chunks: list[KnowledgeChunk],
    queries: Iterable[str],
    *,
    limit: int = 6,
    allowed_source_kinds: set[str] | None = None,
) -> list[KnowledgeChunk]:
    query_terms = tokenize_queries(queries)
    scored: list[tuple[int, KnowledgeChunk]] = []

    for chunk in chunks:
        if allowed_source_kinds and chunk.source_kind not in allowed_source_kinds:
            continue
        haystack = chunk.text.lower()
        score = sum(haystack.count(term) for term in query_terms)
        if score == 0:
            continue
        if chunk.source_kind == "legal_authority":
            score += 15
        elif chunk.source_kind == "internal_legal_note":
            score += 7
        scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


def format_sources_for_prompt(chunks: list[KnowledgeChunk], max_chars_per_source: int = 6000) -> str:
    parts: list[str] = []
    for chunk in chunks:
        parts.append(
            f"SOURCE_KIND: {chunk.source_kind}\n"
            f"SOURCE_NAME: {chunk.source_name}\n"
            f"CONTENT:\n{chunk.text[:max_chars_per_source]}"
        )
    return "\n\n---\n\n".join(parts)


# ============================================================
# Topic helpers
# ============================================================

def load_topics(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def pick_topic(topics: list[dict[str, Any]], topic_index: int | None) -> tuple[int, dict[str, Any], int]:
    unused_indexes = [i for i, topic in enumerate(topics) if topic.get("status") == "unused"]
    if not unused_indexes:
        raise RuntimeError("No unused topics remain.")

    if topic_index is not None:
        if topic_index < 0 or topic_index >= len(topics):
            raise RuntimeError(f"topic-index {topic_index} out of range")
        selected = topics[topic_index]
        if selected.get("status") != "unused":
            raise RuntimeError(f"topic-index {topic_index} is not unused")
        return topic_index, selected, len(unused_indexes)

    idx = unused_indexes[0]
    return idx, topics[idx], len(unused_indexes)


def audience_brief(audience: str) -> str:
    mapping = {
        "global_individuals": "Write primarily for individuals and families outside Switzerland considering a Swiss move, permit or residence strategy.",
        "global_businesses": "Write primarily for businesses outside Switzerland hiring, assigning, seconding or posting staff to Switzerland.",
        "inside_switzerland_individuals": "Write primarily for individuals already in Switzerland dealing with residence continuity, renewals, upgrades, family life or citizenship.",
        "inside_switzerland_businesses": "Write primarily for organisations already operating in Switzerland managing permits, sponsorship, compliance or workforce planning.",
        "refused_applicants": "Write primarily for applicants or sponsors responding to a Swiss refusal or planning a stronger re-filing or appeal strategy.",
        "general_global": "Write for a broad international audience while still identifying the reader position most affected by the topic.",
    }
    return mapping.get(audience or "general_global", mapping["general_global"])


def derive_topic_metadata(topic_entry: dict[str, Any]) -> dict[str, str]:
    topic = topic_entry.get("topic", "")
    angle = topic_entry.get("angle", "")
    audience = topic_entry.get("audience", "general_global")

    default_article_type = "risk_analysis"
    if any(term in (topic + " " + angle).lower() for term in ["why", "differs", "compare", "between"]):
        default_article_type = "comparison_piece"
    elif any(term in (topic + " " + angle).lower() for term in ["refusal", "appeal", "reapplying", "reapply"]):
        default_article_type = "procedural_strategy"
    elif any(term in (topic + " " + angle).lower() for term in ["what authorities notice", "what needs to be addressed", "go wrong"]):
        default_article_type = "myth_correction"

    return {
        "audience": audience,
        "article_type_hint": topic_entry.get("article_type", default_article_type),
        "pillar": topic_entry.get("pillar", "unspecified"),
        "subtopic": topic_entry.get("subtopic", "unspecified"),
        "legal_complexity_hint": topic_entry.get("legal_complexity", "high"),
        "overlap_group": topic_entry.get("overlap_group", "unspecified"),
    }


# ============================================================
# Prompt builders
# ============================================================

def build_classifier_input(topic_entry: dict[str, Any]) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "topic_metadata": derive_topic_metadata(topic_entry),
        },
        ensure_ascii=False,
        indent=2,
    )


def build_source_audit_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    legal_chunks: list[KnowledgeChunk],
    website_context_chunks: list[KnowledgeChunk],
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "classifier": classifier,
            "legal_sources": format_sources_for_prompt(legal_chunks, max_chars_per_source=2500),
            "website_editorial_sources": format_sources_for_prompt(website_context_chunks, max_chars_per_source=1500),
        },
        ensure_ascii=False,
        indent=2,
    )


def build_overlap_input(topic_entry: dict[str, Any], classifier: dict[str, Any], website_context_text: str) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "classifier": classifier,
            "website_editorial_context": website_context_text,
        },
        ensure_ascii=False,
        indent=2,
    )


def build_legal_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    source_audit: dict[str, Any],
    legal_sources_text: str,
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "audience_brief": audience_brief(topic_entry.get("audience", "general_global")),
            "topic_metadata": derive_topic_metadata(topic_entry),
            "classifier": classifier,
            "source_audit": source_audit,
            "legal_sources": legal_sources_text,
        },
        ensure_ascii=False,
        indent=2,
    )


def build_verifier_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    source_audit: dict[str, Any],
    overlap: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "classifier": classifier,
            "memo": memo,
            "source_audit": source_audit,
            "overlap_analysis": overlap,
        },
        ensure_ascii=False,
        indent=2,
    )


def build_draft_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    verifier: dict[str, Any],
    source_audit: dict[str, Any],
    overlap: dict[str, Any],
    website_context: str,
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "audience_brief": audience_brief(topic_entry.get("audience", "general_global")),
            "topic_metadata": derive_topic_metadata(topic_entry),
            "classifier": classifier,
            "source_audit": source_audit,
            "verified_legal_memo": memo,
            "verifier": verifier,
            "overlap_analysis": overlap,
            "editorial_constraints": {
                "cta_heading": CTA_HEADING,
                "cta_name": CTA_NAME,
                "cta_phone": CTA_PHONE,
                "dynamic_page_link_must_be_blank": True,
                "hard_word_limit": MAX_BLOG_WORDS,
                "target_word_range": TARGET_BLOG_WORDS,
                "citation_style": "Use short in-text legal references only, using LEI / AIG and OASA / VZAE.",
                "subheading_style": "Use bold keyword-optimised sub-headings with a blank line above and below each one.",
                "opening_style": "The first paragraph must be fully bold, followed immediately by a second introductory paragraph without legal citations.",
                "practical_sections_required": ["What This Means in Practice", "What To Do Next"],
                "quick_answer_required": True,
                "disclaimer_required": True,
                "distinct_angle": overlap.get("new_article_distinct_angle", ""),
                "avoid_repeating": overlap.get("avoid_repeating", []),
                "recommended_internal_links": overlap.get("internal_link_recommendations", []),
                "source_audit_cautions": source_audit.get("source_gaps", []) + source_audit.get("must_verify_before_publication", []),
                "website_context_use": "Use for continuity, overlap avoidance and internal linking only. Do not use as legal authority.",
                "website_context": website_context[:8000],
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def build_revision_input(
    *,
    topic_entry: dict[str, Any],
    draft: dict[str, Any],
    memo: dict[str, Any],
    verifier: dict[str, Any],
    source_audit: dict[str, Any],
    overlap: dict[str, Any],
    validation: EditorialValidationResult,
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "topic_metadata": derive_topic_metadata(topic_entry),
            "current_draft": draft,
            "verified_legal_memo": memo,
            "verifier": verifier,
            "source_audit": source_audit,
            "overlap_analysis": overlap,
            "editorial_validation_issues": validation.issues,
            "editorial_validation_warnings": validation.warnings,
            "repetition_score": validation.repetition_score,
            "hard_word_limit": MAX_BLOG_WORDS,
            "required_fixes": [
                f"Shorten to no more than {MAX_BLOG_WORDS} words.",
                "Fix every validation issue listed.",
                "Delete repetition rather than adding more explanation.",
                "Preserve legal accuracy.",
                "Preserve required sections and formatting.",
                "Preserve distinct angle from overlap analysis.",
                "Treat source-audit cautions as points requiring careful wording, not refusal.",
                "Produce a publication-ready final blog draft.",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def build_seo_input(
    topic_entry: dict[str, Any],
    draft: dict[str, Any],
    overlap: dict[str, Any],
    source_audit: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "blog_title": draft["blog_title"],
            "blog_excerpt": draft["blog_content"][:3500],
            "overlap_internal_link_recommendations": overlap.get("internal_link_recommendations", []),
            "recommended_source_references": source_audit.get("recommended_source_references", []),
        },
        ensure_ascii=False,
        indent=2,
    )


# ============================================================
# Persistence and email helpers
# ============================================================

def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def write_run_artifact(filename: str, payload: dict[str, Any]) -> Path:
    ensure_output_dir()
    path = OUTPUT_DIR / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def send_email_via_sendgrid(subject: str, body: str, *, is_html: bool = False) -> bool:
    if not SENDGRID_API_KEY:
        raise RuntimeError("Missing SENDGRID_API_KEY")

    payload = {
        "personalizations": [{"to": [{"email": EMAIL_TO}], "subject": subject}],
        "from": {"email": EMAIL_FROM},
        "reply_to": {"email": REPLY_TO},
        "content": [{"type": "text/html" if is_html else "text/plain", "value": body}],
    }
    headers = {"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"}

    try:
        status, _ = post_json("https://api.sendgrid.com/v3/mail/send", payload=payload, headers=headers)
        return status == 202
    except RuntimeError:
        return False


def list_to_html(items: list[str]) -> str:
    if not items:
        return "<em>None</em>"
    return "<ul>" + "".join(f"<li>{escape_html(item)}</li>" for item in items) + "</ul>"


# ============================================================
# Editorial validation and normalisation
# ============================================================

def replace_legal_abbreviation_style(text: str) -> str:
    text = re.sub(r"(?<!LEI / )\bAIG\b", "LEI / AIG", text)
    text = re.sub(r"(?<!OASA / )\bVZAE\b", "OASA / VZAE", text)
    return text


def remove_forbidden_public_phrases(text: str) -> str:
    cleaned = text
    for phrase in FORBIDDEN_PUBLIC_PHRASES:
        cleaned = re.sub(re.escape(phrase), "this issue", cleaned, flags=re.IGNORECASE)
    return cleaned


def split_blocks(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", text.strip()) if chunk.strip()]


def strip_bold_markers(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def visible_text(text: str) -> str:
    return re.sub(r"\s+", " ", strip_bold_markers(text)).strip()


def word_count(text: str) -> int:
    return len(visible_text(text).split())


def is_bold_heading(block: str) -> bool:
    if not (block.startswith("**") and block.endswith("**")):
        return False
    inner = block[2:-2].strip()
    return len(inner.split()) <= 14 and not inner.endswith(".")


def validate_title(title: str) -> list[str]:
    issues: list[str] = []
    lower = title.lower()
    for pattern in TITLE_OVERCLAIM_PATTERNS:
        if re.search(pattern, lower):
            issues.append(f"Title may overclaim or overstate certainty: pattern '{pattern}'.")
    if len(title) > 120:
        issues.append("Title is too long and should be tightened.")
    return issues


def validate_opening_paragraph_rules(blog_content: str) -> list[str]:
    issues: list[str] = []
    prose_blocks = [block for block in split_blocks(blog_content) if not is_bold_heading(block)]

    if len(prose_blocks) < 2:
        return ["Draft must contain at least two opening prose paragraphs."]

    first = visible_text(prose_blocks[0])
    second = visible_text(prose_blocks[1])

    if not prose_blocks[0].startswith("**"):
        issues.append("First paragraph is not bold.")
    if re.search(LEGAL_AUTHORITY_PATTERN, first):
        issues.append("First paragraph contains legal authorities.")
    if re.search(LEGAL_AUTHORITY_PATTERN, second):
        issues.append("Second introductory paragraph contains legal authorities.")
    return issues


def validate_quick_answer(blog_content: str) -> list[str]:
    blocks = split_blocks(blog_content)
    lower_blocks = [visible_text(block).lower() for block in blocks]
    total = max(1, len(lower_blocks))
    headings = ["quick answer", "at a glance", "in brief"]

    for index, block in enumerate(lower_blocks):
        if any(heading in block for heading in headings):
            if index / total > 0.35:
                return ["Quick Answer / At a Glance / In Brief appears too late in the article."]
            return []

    return ["Missing required near-top section: Quick Answer, At a Glance or In Brief."]


def validate_disclaimer(blog_content: str) -> list[str]:
    lower = visible_text(blog_content).lower()
    has_not_legal_advice = "not legal advice" in lower or "does not constitute legal advice" in lower
    has_individual_facts = any(term in lower for term in ["individual facts", "case-specific", "personal circumstances", "evidence"])
    has_law_guidance = any(term in lower for term in ["law", "guidance", "date of writing", "current position", "federal immigration"])

    issues = []
    if not has_not_legal_advice:
        issues.append("Disclaimer must state that the article is not legal advice or equivalent.")
    if not has_individual_facts:
        issues.append("Disclaimer must refer to individual facts, evidence or case-specific assessment.")
    if not has_law_guidance:
        issues.append("Disclaimer must refer to law/guidance/current position.")
    return issues


def validate_source_anchoring(blog_content: str, source_audit: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    official_sources = source_audit.get("official_legal_sources_found", [])
    text = visible_text(blog_content)
    lower = text.lower()

    if official_sources and not re.search(LEGAL_AUTHORITY_PATTERN, text):
        issues.append("Article lacks an official legal source or guidance reference in the body.")

    paragraphs = [visible_text(block) for block in split_blocks(blog_content) if not is_bold_heading(block)]
    referenced_paras = [p for p in paragraphs if re.search(LEGAL_AUTHORITY_PATTERN, p)]

    if official_sources and len(referenced_paras) < 2:
        issues.append("Legal references appear too sparse or clustered; add source anchoring in more than one body section.")

    if re.search(r"\bAIG\b", text) and "LEI / AIG" not in text:
        issues.append("Legal references use AIG without LEI / AIG formulation.")
    if re.search(r"\bVZAE\b", text) and "OASA / VZAE" not in text:
        issues.append("Legal references use VZAE without OASA / VZAE formulation.")

    if lower.count("current guidance") > 1 and "sem" not in lower:
        issues.append("Article repeatedly refers to current guidance without identifying the source body.")

    return issues


def sentence_tokens(text: str) -> list[str]:
    text = visible_text(text).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if len(w) > 3]
    stop = {
        "swiss", "permit", "article", "this", "that", "with", "from", "have", "will",
        "your", "they", "their", "which", "when", "where", "what", "also", "into",
        "because", "should", "would", "could", "about", "under", "there",
    }
    return [w for w in words if w not in stop]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_repetition(blog_content: str) -> tuple[float, list[str]]:
    paragraphs = [
        visible_text(block)
        for block in split_blocks(blog_content)
        if not is_bold_heading(block) and len(visible_text(block).split()) >= 25
    ]
    if len(paragraphs) < 4:
        return 0.0, []

    token_sets = [set(sentence_tokens(p)) for p in paragraphs]
    repeated_pairs = 0
    comparisons = 0
    examples: list[str] = []

    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            comparisons += 1
            score = jaccard(token_sets[i], token_sets[j])
            if score >= 0.38:
                repeated_pairs += 1
                if len(examples) < 3:
                    examples.append(f"Paragraphs {i + 1} and {j + 1} appear to repeat similar ideas.")

    repetition_score = repeated_pairs / comparisons if comparisons else 0.0
    return repetition_score, examples


def validate_practical_sections(blog_content: str) -> list[str]:
    issues: list[str] = []
    lower = blog_content.lower()
    if "what this means in practice" not in lower:
        issues.append("Missing required section: What This Means in Practice.")
    if "what to do next" not in lower:
        issues.append("Missing required section: What To Do Next.")

    practical_markers = [
        "documents", "evidence", "decision", "route", "legal basis",
        "appeal", "reapplication", "wait", "timing", "checklist", "review",
    ]
    if sum(1 for marker in practical_markers if marker in lower) < 4:
        issues.append("Practical guidance appears too thin; add documents/evidence/decision framework.")
    return issues


def nearby_specificity(text: str, start: int, window: int = 120) -> str:
    return text[max(0, start - window): start + window].lower()


def flag_vague_category_references(blog_content: str) -> list[str]:
    issues: list[str] = []
    text = visible_text(blog_content)
    lower = text.lower()

    specificity_markers = [
        "sem", "art.", "article", "lei / aig", "oasa / vzae", "spouse", "child",
        "under 18", "canton", "official", "federal", "guidance", "weisungen",
        "must be checked", "current official",
    ]

    for pattern in VAGUE_CATEGORY_PATTERNS:
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            context = nearby_specificity(lower, match.start())
            if not any(marker in context for marker in specificity_markers):
                issues.append(
                    f"Vague reference may need specificity: '{match.group(0)}'. "
                    "Name the category, source or limitation where possible."
                )
                break
    return issues


def validate_cta(blog_content: str) -> list[str]:
    issues: list[str] = []
    lower = blog_content.lower()

    if CTA_HEADING.lower() not in lower:
        issues.append("Missing required CTA heading.")

    cta_start = lower.rfind(CTA_HEADING.lower())
    cta_text = lower[cta_start:] if cta_start != -1 else ""

    concrete_terms = ["review", "decision", "evidence", "route", "legal basis", "appeal", "reapplication", "timing", "options"]
    if cta_text and sum(1 for term in concrete_terms if term in cta_text) < 2:
        issues.append("CTA is too generic; it should explain what the firm would review or advise on.")
    return issues


def validate_generic_style(blog_content: str) -> list[str]:
    issues: list[str] = []
    lower = blog_content.lower()

    for phrase in FORBIDDEN_PUBLIC_PHRASES:
        if phrase.lower() in lower:
            issues.append(f"Internal workflow phrase remains in article: '{phrase}'.")

    filler_hits = [phrase for phrase in GENERIC_FILLER_PHRASES if phrase in lower]
    if len(filler_hits) >= 3:
        issues.append(f"Too many generic/filler phrases: {', '.join(filler_hits[:5])}.")

    wc = word_count(blog_content)
    if wc > MAX_BLOG_WORDS:
        issues.append(f"Article exceeds hard maximum word count ({wc}/{MAX_BLOG_WORDS}).")
    if wc < 750:
        issues.append(f"Article may be too thin ({wc} words); ensure practical substance is adequate.")
    return issues


def validate_editorial_quality(draft: dict[str, Any], source_audit: dict[str, Any]) -> EditorialValidationResult:
    issues: list[str] = []
    warnings: list[str] = []
    title = draft.get("blog_title", "")
    blog_content = draft.get("blog_content", "")

    issues.extend(validate_title(title))
    issues.extend(validate_opening_paragraph_rules(blog_content))
    issues.extend(validate_quick_answer(blog_content))
    issues.extend(validate_practical_sections(blog_content))
    issues.extend(validate_disclaimer(blog_content))
    issues.extend(validate_source_anchoring(blog_content, source_audit))
    warnings.extend(flag_vague_category_references(blog_content))
    issues.extend(validate_cta(blog_content))
    issues.extend(validate_generic_style(blog_content))

    repetition_score, repetition_issues = detect_repetition(blog_content)
    issues.extend(repetition_issues)
    if repetition_score >= 0.08:
        issues.append(f"Repetition score too high: {repetition_score:.2f}. Compress repeated propositions.")

    return EditorialValidationResult(passed=not issues, issues=issues, warnings=warnings, repetition_score=repetition_score)


def validate_seo(seo: dict[str, Any], draft: dict[str, Any]) -> SEOValidationResult:
    issues: list[str] = []
    warnings: list[str] = []

    meta_title = seo.get("seo_meta_title", "")
    meta_description = seo.get("seo_meta_description", "")
    slug = seo.get("suggested_slug", "")
    primary_keyword = seo.get("primary_keyword", "")
    title = draft.get("blog_title", "")
    body_start = visible_text(draft.get("blog_content", ""))[:600].lower()

    if len(meta_title) > 60:
        issues.append("SEO meta title exceeds 60 characters.")
    if len(meta_description) > 155:
        issues.append("SEO meta description exceeds 155 characters.")
    if len(slug) > 80:
        warnings.append("Suggested slug is long; consider shortening.")
    if validate_title(meta_title):
        issues.append("SEO meta title may overclaim.")
    if primary_keyword and primary_keyword.lower() not in (title + " " + body_start).lower():
        warnings.append("Primary keyword does not appear naturally in title or opening body.")
    if re.search(r"\bguaranteed\b|\bultimate\b|\bsecret\b|\bmust-read\b", meta_title.lower()):
        issues.append("SEO meta title contains melodramatic wording.")

    return SEOValidationResult(passed=not issues, issues=issues, warnings=warnings)


def normalise_draft_output(draft: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(draft)
    cleaned["dynamic_page_link"] = ""

    blog_content = cleaned.get("blog_content", "").strip()
    blog_content = replace_legal_abbreviation_style(blog_content)
    blog_content = remove_forbidden_public_phrases(blog_content)
    blog_content = re.sub(r"\n{3,}", "\n\n", blog_content).strip()

    cleaned["blog_content"] = blog_content
    cleaned["blog_title"] = replace_legal_abbreviation_style(cleaned.get("blog_title", "").strip())
    return cleaned


# ============================================================
# HTML rendering
# ============================================================

def inline_bold_to_html(text: str) -> str:
    escaped = escape_html(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def render_markdown_table(block: str) -> str | None:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) < 3 or not all(line.startswith("|") and line.endswith("|") for line in lines[:2]):
        return None
    if not re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", lines[1]):
        return None

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    headers = cells(lines[0])
    rows = [cells(line) for line in lines[2:]]

    html = ["<table border='1' cellpadding='6' cellspacing='0'>"]
    html.append("<thead><tr>" + "".join(f"<th>{inline_bold_to_html(h)}</th>" for h in headers) + "</tr></thead>")
    html.append("<tbody>")
    for row in rows:
        html.append("<tr>" + "".join(f"<td>{inline_bold_to_html(cell)}</td>" for cell in row) + "</tr>")
    html.append("</tbody></table>")
    return "\n".join(html)


def render_list_block(block: str) -> str | None:
    lines = [line.rstrip() for line in block.splitlines() if line.strip()]
    if not lines:
        return None

    unordered = all(re.match(r"^\s*[-*]\s+", line) for line in lines)
    ordered = all(re.match(r"^\s*\d+\.\s+", line) for line in lines)

    if not unordered and not ordered:
        return None

    tag = "ul" if unordered else "ol"
    html = [f"<{tag}>"]
    for line in lines:
        item = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).strip()
        html.append(f"<li>{inline_bold_to_html(item)}</li>")
    html.append(f"</{tag}>")
    return "\n".join(html)


def blog_content_to_html(blog_title: str, blog_content: str) -> str:
    blocks = split_blocks(blog_content)
    html_parts: list[str] = [f"<h2>{escape_html(blog_title)}</h2>"]

    for block in blocks:
        stripped = block.strip()

        table_html = render_markdown_table(stripped)
        if table_html:
            html_parts.append(table_html)
            continue

        list_html = render_list_block(stripped)
        if list_html:
            html_parts.append(list_html)
            continue

        if is_bold_heading(stripped):
            inner = stripped[2:-2].strip()
            html_parts.append(f"<h3><strong>{escape_html(inner)}</strong></h3>")
            continue

        if stripped.startswith("**") and stripped.endswith("**"):
            inner = stripped[2:-2].strip()
            html_parts.append(f"<p><strong>{escape_html(inner)}</strong></p>")
            continue

        html_parts.append(f"<p>{inline_bold_to_html(stripped)}</p>")

    return "\n".join(html_parts)


# ============================================================
# Final publication gate
# ============================================================

def build_publication_gate(
    *,
    source_audit: dict[str, Any],
    verifier: dict[str, Any],
    overlap: dict[str, Any],
    editorial_validation: EditorialValidationResult,
    seo_validation: SEOValidationResult,
    draft: dict[str, Any],
    revision_count: int,
) -> PublicationGateResult:
    blockers: list[str] = []
    warnings: list[str] = []

    # Source audit is advisory unless there is truly no legal basis to work from.
    if not source_audit.get("safe_to_draft", False):
        if not source_audit.get("official_legal_sources_found") and not source_audit.get("internal_notes_found"):
            warnings.append("Source audit found limited legal source anchoring; draft uses cautious wording.")
        else:
            warnings.append("Source audit raised cautions; draft uses careful wording.")

    # Verifier findings guide caution and revision. They do not block drafting unless no article could be drafted safely.
    if verifier.get("legal_risk_level") == "high":
        warnings.append("Verifier assessed legal risk as high; human review recommended.")
    if verifier.get("publication_blockers"):
        warnings.extend([f"Verifier caution: {item}" for item in verifier.get("publication_blockers", [])])

    if overlap.get("cannibalisation_risk") == "high":
        warnings.append("High cannibalisation risk; article should be reviewed for distinct positioning.")

    if not editorial_validation.passed:
        blockers.extend([f"Editorial issue: {item}" for item in editorial_validation.issues])

    if not seo_validation.passed:
        warnings.extend([f"SEO issue: {item}" for item in seo_validation.issues])

    warnings.extend(source_audit.get("must_verify_before_publication", []))
    warnings.extend(editorial_validation.warnings)
    warnings.extend(seo_validation.warnings)

    if overlap.get("cannibalisation_risk") == "medium":
        warnings.append("Medium cannibalisation risk.")
    if verifier.get("legal_risk_level") == "medium":
        warnings.append("Verifier assessed legal risk as medium.")
    if verifier.get("claims_requiring_exact_source_reference"):
        warnings.extend(
            [f"Exact source reference recommended: {claim}" for claim in verifier.get("claims_requiring_exact_source_reference", [])]
        )

    human_review_recommended = bool(warnings or verifier.get("legal_risk_level") in {"medium", "high"})

    return PublicationGateResult(
        passed=not blockers,
        blockers=blockers,
        warnings=warnings,
        human_review_recommended=human_review_recommended,
        final_word_count=word_count(draft.get("blog_content", "")),
        revision_count=revision_count,
    )


# ============================================================
# Email rendering
# ============================================================

def render_success_email(
    *,
    topic_entry: dict[str, Any],
    draft: dict[str, Any],
    seo: dict[str, Any],
    remaining_after_send: int,
    source_audit: dict[str, Any],
    verifier: dict[str, Any],
    overlap: dict[str, Any],
    gate: PublicationGateResult,
    selected_pack_paths: list[Path],
) -> str:
    keywords = "; ".join(seo["suggested_seo_keywords"])
    blog_html = blog_content_to_html(draft["blog_title"], draft["blog_content"])
    review_label = "<p><strong>HUMAN REVIEW RECOMMENDED:</strong> Yes</p>" if gate.human_review_recommended else ""

    return f"""<html>
  <body style="font-family: Arial, Helvetica, sans-serif; line-height: 1.6; color: #222;">
    <p><strong>TOPIC BACKLOG:</strong><br>{remaining_after_send} topics remaining</p>
    {review_label}

    <p><strong>TOPIC:</strong><br>{escape_html(topic_entry.get('topic', ''))}</p>
    <p><strong>ANGLE:</strong><br>{escape_html(topic_entry.get('angle', ''))}</p>
    <p><strong>BLOG TITLE:</strong><br>{escape_html(draft['blog_title'])}</p>
    <p><strong>DYNAMIC PAGE LINK:</strong><br>&nbsp;</p>

    <p><strong>SEO META TITLE:</strong><br>{escape_html(seo['seo_meta_title'])}</p>
    <p><strong>SEO META DESCRIPTION:</strong><br>{escape_html(seo['seo_meta_description'])}</p>
    <p><strong>SUGGESTED SLUG:</strong><br>{escape_html(seo.get('suggested_slug', ''))}</p>
    <p><strong>PRIMARY KEYWORD:</strong><br>{escape_html(seo.get('primary_keyword', ''))}</p>
    <p><strong>SUGGESTED SEO KEYWORDS:</strong><br>{escape_html(keywords)}</p>

    <hr>

    <h3>Quality Control Summary</h3>
    <p><strong>Final publication gate:</strong> {'Passed' if gate.passed else 'Failed'}</p>
    <p><strong>Word count:</strong> {gate.final_word_count}</p>
    <p><strong>Revision count:</strong> {gate.revision_count}</p>

    <p><strong>Source audit summary:</strong></p>
    {list_to_html(source_audit.get('recommended_source_references', []))}

    <p><strong>Legal verifier summary:</strong><br>
    Risk level: {escape_html(verifier.get('legal_risk_level', ''))}<br>
    Recommended positioning: {escape_html(verifier.get('recommended_positioning', ''))}</p>

    <p><strong>Overlap analysis:</strong><br>
    Cannibalisation risk: {escape_html(overlap.get('cannibalisation_risk', ''))}<br>
    Distinct angle: {escape_html(overlap.get('new_article_distinct_angle', ''))}</p>

    <p><strong>Recommended internal links:</strong></p>
    {list_to_html(overlap.get('internal_link_recommendations', []) + seo.get('internal_link_anchor_suggestions', []))}

    <p><strong>External authority link suggestions:</strong></p>
    {list_to_html(seo.get('external_authority_link_suggestions', []))}

    <p><strong>Warnings requiring human verification:</strong></p>
    {list_to_html(gate.warnings)}

    <p><strong>Source list:</strong></p>
    {list_to_html([str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths])}

    <hr>

    <p><strong>BLOG CONTENT:</strong></p>
    {blog_html}
  </body>
</html>
"""


# ============================================================
# Main workflow
# ============================================================

def main() -> None:
    topics = load_topics(TOPICS_PATH)
    topic_index, topic_entry, remaining_count = pick_topic(topics, args.topic_index)

    if args.dry_run:
        authority_map = load_authority_pack_map(AUTHORITY_MAP_PATH)
        selected_pack_paths = resolve_authority_pack_paths(topic_entry, authority_map)
        print(json.dumps({
            "selected_topic": topic_entry,
            "remaining_unused": remaining_count,
            "mapped_authority_packs": [str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths],
            "expected_retrieval_queries": [
                topic_entry.get("topic", ""),
                topic_entry.get("angle", ""),
                topic_entry.get("subtopic", ""),
                topic_entry.get("pillar", ""),
            ],
        }, ensure_ascii=False, indent=2))
        return

    openai_api_key = require_env("OPENAI_API_KEY")
    require_env("SENDGRID_API_KEY")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", topic_entry.get("topic", "untitled").lower()).strip("-")[:80]
    run_base = f"{timestamp}_{slug}"

    authority_map = load_authority_pack_map(AUTHORITY_MAP_PATH)

    classifier = call_responses_api(
        openai_api_key,
        instructions=CLASSIFIER_INSTRUCTIONS,
        input_text=build_classifier_input(topic_entry),
        schema=CLASSIFIER_SCHEMA,
        model=OPENAI_MODEL,
    )

    retrieval_queries = list(classifier.get("key_issues", [])) + [
        topic_entry.get("topic", ""),
        topic_entry.get("angle", ""),
    ]

    selected_pack_paths = resolve_authority_pack_paths(topic_entry, authority_map)

    if not selected_pack_paths:
        raise RuntimeError(
            f"No legal authority packs mapped for pillar={topic_entry.get('pillar')} "
            f"subtopic={topic_entry.get('subtopic')}"
        )

    selected_legal_chunks = load_selected_legal_authority_chunks(selected_pack_paths)
    internal_note_chunks = load_chunks_from_folder(INTERNAL_NOTES_DIR, "internal_legal_note")
    website_editorial_chunks = load_chunks_from_folder(WEBSITE_EDITORIAL_DIR, "website_editorial")

    legal_chunks = simple_retrieve(
        selected_legal_chunks + internal_note_chunks,
        retrieval_queries,
        limit=10,
        allowed_source_kinds={"legal_authority", "internal_legal_note"},
    )

    if not legal_chunks:
        raise RuntimeError("No usable legal authority or internal legal note was retrieved for this topic.")

    website_context_chunks = simple_retrieve(
        website_editorial_chunks,
        retrieval_queries,
        limit=3,
        allowed_source_kinds={"website_editorial"},
    )

    legal_sources_text = format_sources_for_prompt(legal_chunks)
    website_context_text = format_sources_for_prompt(website_context_chunks)

    source_audit = call_responses_api(
        openai_api_key,
        instructions=SOURCE_AUDIT_INSTRUCTIONS,
        input_text=build_source_audit_input(topic_entry, classifier, legal_chunks, website_context_chunks),
        schema=SOURCE_AUDIT_SCHEMA,
        model=OPENAI_MODEL,
    )

    overlap = call_responses_api(
        openai_api_key,
        instructions=OVERLAP_INSTRUCTIONS,
        input_text=build_overlap_input(topic_entry, classifier, website_context_text),
        schema=OVERLAP_SCHEMA,
        model=OPENAI_MODEL,
    )

    memo = call_responses_api(
        openai_api_key,
        instructions=LEGAL_MEMO_INSTRUCTIONS,
        input_text=build_legal_input(topic_entry, classifier, source_audit, legal_sources_text),
        schema=LEGAL_MEMO_SCHEMA,
        model=OPENAI_MODEL,
    )

    verifier = call_responses_api(
        openai_api_key,
        instructions=VERIFIER_INSTRUCTIONS,
        input_text=build_verifier_input(topic_entry, classifier, memo, source_audit, overlap),
        schema=VERIFIER_SCHEMA,
        model=OPENAI_MODEL,
    )

    write_run_artifact(
        f"{run_base}_analysis.json",
        {
            "topic": topic_entry,
            "classifier": classifier,
            "selected_legal_authority_packs": [str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths],
            "source_audit": source_audit,
            "overlap_analysis": overlap,
            "memo": memo,
            "verifier": verifier,
        },
    )

    draft = call_responses_api(
        openai_api_key,
        instructions=DRAFT_INSTRUCTIONS,
        input_text=build_draft_input(topic_entry, classifier, memo, verifier, source_audit, overlap, website_context_text),
        schema=DRAFT_SCHEMA,
        model=OPENAI_MODEL,
    )
    draft = normalise_draft_output(draft)
    editorial_validation = validate_editorial_quality(draft, source_audit)

    revision_count = 0
    while not editorial_validation.passed and revision_count < MAX_EDITORIAL_REVISIONS:
        revision_count += 1
        draft = call_responses_api(
            openai_api_key,
            instructions=EDITORIAL_REVISER_INSTRUCTIONS,
            input_text=build_revision_input(
                topic_entry=topic_entry,
                draft=draft,
                memo=memo,
                verifier=verifier,
                source_audit=source_audit,
                overlap=overlap,
                validation=editorial_validation,
            ),
            schema=DRAFT_SCHEMA,
            model=OPENAI_MODEL,
        )
        draft = normalise_draft_output(draft)
        editorial_validation = validate_editorial_quality(draft, source_audit)

    seo = call_responses_api(
        openai_api_key,
        instructions=SEO_INSTRUCTIONS,
        input_text=build_seo_input(topic_entry, draft, overlap, source_audit),
        schema=SEO_SCHEMA,
        model=OPENAI_MODEL,
    )

    seo_validation = validate_seo(seo, draft)

    gate = build_publication_gate(
        source_audit=source_audit,
        verifier=verifier,
        overlap=overlap,
        editorial_validation=editorial_validation,
        seo_validation=seo_validation,
        draft=draft,
        revision_count=revision_count,
    )

    if not gate.passed:
        # Final repair attempt so the app delivers a publication-ready draft rather than a review-required email.
        repair_validation = EditorialValidationResult(
            passed=False,
            issues=gate.blockers,
            warnings=gate.warnings,
            repetition_score=editorial_validation.repetition_score,
        )
        draft = call_responses_api(
            openai_api_key,
            instructions=EDITORIAL_REVISER_INSTRUCTIONS,
            input_text=build_revision_input(
                topic_entry=topic_entry,
                draft=draft,
                memo=memo,
                verifier=verifier,
                source_audit=source_audit,
                overlap=overlap,
                validation=repair_validation,
            ),
            schema=DRAFT_SCHEMA,
            model=OPENAI_MODEL,
        )
        draft = normalise_draft_output(draft)
        editorial_validation = validate_editorial_quality(draft, source_audit)
        seo = call_responses_api(
            openai_api_key,
            instructions=SEO_INSTRUCTIONS,
            input_text=build_seo_input(topic_entry, draft, overlap, source_audit),
            schema=SEO_SCHEMA,
            model=OPENAI_MODEL,
        )
        seo_validation = validate_seo(seo, draft)
        revision_count += 1
        gate = build_publication_gate(
            source_audit=source_audit,
            verifier=verifier,
            overlap=overlap,
            editorial_validation=editorial_validation,
            seo_validation=seo_validation,
            draft=draft,
            revision_count=revision_count,
        )

    final_payload = {
        "topic": topic_entry,
        "classifier": classifier,
        "selected_legal_authority_packs": [str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths],
        "source_audit": source_audit,
        "overlap_analysis": overlap,
        "memo": memo,
        "verifier": verifier,
        "draft": draft,
        "seo": seo,
        "editorial_validation": {
            "passed": editorial_validation.passed,
            "issues": editorial_validation.issues,
            "warnings": editorial_validation.warnings,
            "repetition_score": editorial_validation.repetition_score,
        },
        "seo_validation": {
            "passed": seo_validation.passed,
            "issues": seo_validation.issues,
            "warnings": seo_validation.warnings,
        },
        "publication_gate": {
            "passed": gate.passed,
            "blockers": gate.blockers,
            "warnings": gate.warnings,
            "human_review_recommended": gate.human_review_recommended,
            "final_word_count": gate.final_word_count,
            "revision_count": gate.revision_count,
        },
    }

    write_run_artifact(f"{run_base}_final.json", final_payload)

    email_body = render_success_email(
        topic_entry=topic_entry,
        draft=draft,
        seo=seo,
        remaining_after_send=remaining_count - 1,
        source_audit=source_audit,
        verifier=verifier,
        overlap=overlap,
        gate=gate,
        selected_pack_paths=selected_pack_paths,
    )

    subject_prefix = "Blog draft"
    if gate.human_review_recommended:
        subject_prefix = "Blog draft - human review recommended"

    sent = send_email_via_sendgrid(
        subject=f"{subject_prefix}: {draft['blog_title']}",
        body=email_body,
        is_html=True,
    )
    if not sent:
        raise RuntimeError("Draft generated but SendGrid delivery failed.")

    should_mark_used = not gate.human_review_recommended or args.auto_mark_used_on_review_recommended
    if should_mark_used:
        topics[topic_index]["status"] = "used"
        topics[topic_index]["used_title"] = draft["blog_title"]
        topics[topic_index]["used_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with TOPICS_PATH.open("w", encoding="utf-8") as f:
            json.dump(topics, f, indent=2, ensure_ascii=False)

        print(f"Draft email sent successfully and topic marked used: {draft['blog_title']}")
    else:
        print(f"Draft email sent with human review recommended; topic remains unused: {draft['blog_title']}")


if __name__ == "__main__":
    main()
