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
    help="If no legal authorities are found, continue using internal legal notes only. Never uses website editorial as legal authority.",
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
]

LEGAL_AUTHORITY_PATTERN = r"(Article\s+\d|SEM Directives|LEI / AIG|OASA / VZAE)"


# ============================================================
# Prompts
# ============================================================

CLASSIFIER_INSTRUCTIONS = """
You are assisting a Swiss immigration law content workflow.
Classify the requested article before drafting.
Return strict JSON only.
Do not write the article.
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
- Distinguish between settled legal framework, discretion, cantonal practice, procedure and evidential issues.
- Identify exceptions, preservation mechanisms, qualifications and reader-category distinctions.
- If EU/EFTA and non-EU treatment differs, state that.
- If canton-specific handling matters, state that.
- Identify practical decision points for a prospective client.
- Identify evidence/documents that a lawyer would usually want to review.
- Avoid unsupported certainty.

Return strict JSON only.
""".strip()

VERIFIER_INSTRUCTIONS = """
You are reviewing an internal legal memo for overstatement, omission and weak support.
Return strict JSON only.
You must identify:
- unsupported claims
- overbroad claims
- missing exceptions or qualifications
- points that rely on cantonal practice rather than black-letter law
- places where the eventual article should distinguish reader categories
- whether the memo is safe for article drafting
""".strip()

DRAFT_INSTRUCTIONS = f"""
You are drafting a client-facing blog post for a Swiss immigration law firm.
Draft only from the verified legal memo and editorial instructions supplied.
Do not add new legal propositions not present in the verified memo.

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
- Do not use footnotes.
- Do not use bullet points unless they genuinely improve practical clarity.
- No markdown other than double asterisks for bold paragraph and bold sub-headings.

Legal authority requirements:
- Include a small number of short legal authority references throughout the body after the first two paragraphs.
- Legal authority references must be legally accurate and should include article numbers wherever relevant.
- Always use LEI / AIG, never AIG on its own.
- Always use OASA / VZAE, never VZAE on its own.

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
- Where the article refers to "some nationalities", "certain countries", "some cantons" or similar, either name the relevant category accurately from the legal memo or say that the point must be checked against current official guidance.
- Distinguish clearly between settled legal framework, canton-specific practice, evidential issues and genuinely discretionary areas.
- Use caution where needed, but avoid repeated hedging.

SEO:
- Integrate relevant SEO keywords and keyword variations naturally.
- Do not let SEO phrasing dominate the article.
- The post must still read as though written by a human lawyer.

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
- Reduce repetition by approximately 25 to 35 percent where possible.
- Make each section do distinct work.
- Make the article more practical, decision-oriented and useful to a prospective client.
- Preserve required formatting.
- Preserve legal authority style: LEI / AIG and OASA / VZAE.
- Keep legal references out of the first two paragraphs.
- Keep a bold first paragraph.
- Keep a second introductory paragraph explaining what the post covers and who it helps.
- Ensure sections headed exactly:
  **What This Means in Practice**
  **What To Do Next**
- Ensure the CTA is concrete, not generic.
- Remove internal-sounding phrases such as "the memo", "the verified materials", "the materials support".
- Avoid overclaiming in the title.
- Replace vague category wording such as "some applicants", "some nationalities", "certain countries" or "some cantons" with either a named category supported by the memo or a clear reference to current official guidance.

Return strict JSON only using the blog_draft schema.
""".strip()

SEO_INSTRUCTIONS = """
You are generating SEO metadata for a Swiss immigration law article.
Return strict JSON only.
Requirements:
- meta title max 60 characters
- meta description max 155 characters
- 6 keyword phrases
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
            "primary_audience",
            "article_type",
            "search_intent",
            "legal_complexity",
            "key_issues",
            "distinctions_required",
            "source_needs",
            "style_profile",
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
                        "exceptions": {"type": "array", "items": {"type": "string"}},
                        "procedure_points": {"type": "array", "items": {"type": "string"}},
                        "cantonal_practice_points": {"type": "array", "items": {"type": "string"}},
                        "reader_distinctions": {"type": "array", "items": {"type": "string"}},
                        "practical_implications": {"type": "array", "items": {"type": "string"}},
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
                        "issue",
                        "rule",
                        "exceptions",
                        "procedure_points",
                        "cantonal_practice_points",
                        "reader_distinctions",
                        "practical_implications",
                        "support",
                        "confidence",
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
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "overbroad_claims": {"type": "array", "items": {"type": "string"}},
            "missing_qualifications": {"type": "array", "items": {"type": "string"}},
            "cantonal_sensitivity": {"type": "array", "items": {"type": "string"}},
            "required_reader_distinctions": {"type": "array", "items": {"type": "string"}},
            "revision_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "publishable",
            "unsupported_claims",
            "overbroad_claims",
            "missing_qualifications",
            "cantonal_sensitivity",
            "required_reader_distinctions",
            "revision_actions",
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
        },
        "required": ["seo_meta_title", "seo_meta_description", "suggested_seo_keywords"],
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
    repetition_score: float


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
            chunks.append(
                KnowledgeChunk(
                    source_name=str(pdf_path.relative_to(SCRIPT_DIR)),
                    source_kind="legal_authority",
                    text=text,
                )
            )

    return chunks


def load_chunks_from_folder(folder: Path, source_kind: str) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    if not folder.is_dir():
        return chunks
    for pdf_path in sorted(folder.glob("*.pdf")):
        text = read_pdf_text(pdf_path)
        if text:
            chunks.append(KnowledgeChunk(source_name=pdf_path.name, source_kind=source_kind, text=text))
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
    payload = {
        "topic": topic_entry.get("topic", ""),
        "angle": topic_entry.get("angle", ""),
        "audience": topic_entry.get("audience", "general_global"),
        "topic_metadata": derive_topic_metadata(topic_entry),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_legal_input(topic_entry: dict[str, Any], classifier: dict[str, Any], legal_sources_text: str) -> str:
    payload = {
        "topic": topic_entry.get("topic", ""),
        "angle": topic_entry.get("angle", ""),
        "audience": topic_entry.get("audience", "general_global"),
        "audience_brief": audience_brief(topic_entry.get("audience", "general_global")),
        "topic_metadata": derive_topic_metadata(topic_entry),
        "classifier": classifier,
        "legal_sources": legal_sources_text,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_verifier_input(topic_entry: dict[str, Any], classifier: dict[str, Any], memo: dict[str, Any]) -> str:
    payload = {
        "topic": topic_entry.get("topic", ""),
        "angle": topic_entry.get("angle", ""),
        "audience": topic_entry.get("audience", "general_global"),
        "classifier": classifier,
        "memo": memo,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_draft_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    verifier: dict[str, Any],
    website_context: str,
) -> str:
    payload = {
        "topic": topic_entry.get("topic", ""),
        "angle": topic_entry.get("angle", ""),
        "audience": topic_entry.get("audience", "general_global"),
        "audience_brief": audience_brief(topic_entry.get("audience", "general_global")),
        "topic_metadata": derive_topic_metadata(topic_entry),
        "classifier": classifier,
        "verified_legal_memo": memo,
        "verifier": verifier,
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
            "website_context_use": "Use for continuity, overlap avoidance and internal linking only. Do not use as legal authority.",
            "website_context": website_context[:8000],
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_revision_input(
    *,
    topic_entry: dict[str, Any],
    draft: dict[str, Any],
    memo: dict[str, Any],
    verifier: dict[str, Any],
    validation: EditorialValidationResult,
) -> str:
    payload = {
        "topic": topic_entry.get("topic", ""),
        "angle": topic_entry.get("angle", ""),
        "audience": topic_entry.get("audience", "general_global"),
        "topic_metadata": derive_topic_metadata(topic_entry),
        "current_draft": draft,
        "verified_legal_memo": memo,
        "verifier": verifier,
        "editorial_validation_issues": validation.issues,
        "repetition_score": validation.repetition_score,
        "hard_word_limit": MAX_BLOG_WORDS,
        "required_fixes": [
            f"Shorten to no more than {MAX_BLOG_WORDS} words.",
            "Fix every validation issue listed.",
            "Delete repetition rather than adding more explanation.",
            "Preserve legal accuracy.",
            "Preserve required sections and formatting.",
            "Do not send a review note; produce a publishable final blog draft.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_seo_input(topic_entry: dict[str, Any], draft: dict[str, Any]) -> str:
    payload = {
        "topic": topic_entry.get("topic", ""),
        "angle": topic_entry.get("angle", ""),
        "audience": topic_entry.get("audience", "general_global"),
        "blog_title": draft["blog_title"],
        "blog_excerpt": draft["blog_content"][:3500],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


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


def flag_vague_category_references(blog_content: str) -> list[str]:
    issues: list[str] = []
    for pattern in VAGUE_CATEGORY_PATTERNS:
        match = re.search(pattern, blog_content, flags=re.IGNORECASE)
        if match:
            issues.append(
                f"Vague category reference found: '{match.group(0)}'. Name the category accurately or refer to current official guidance."
            )
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


def validate_editorial_quality(draft: dict[str, Any]) -> EditorialValidationResult:
    issues: list[str] = []
    title = draft.get("blog_title", "")
    blog_content = draft.get("blog_content", "")

    issues.extend(validate_title(title))
    issues.extend(validate_opening_paragraph_rules(blog_content))
    issues.extend(validate_practical_sections(blog_content))
    issues.extend(flag_vague_category_references(blog_content))
    issues.extend(validate_cta(blog_content))
    issues.extend(validate_generic_style(blog_content))

    repetition_score, repetition_issues = detect_repetition(blog_content)
    issues.extend(repetition_issues)
    if repetition_score >= 0.08:
        issues.append(f"Repetition score too high: {repetition_score:.2f}. Compress repeated propositions.")

    return EditorialValidationResult(passed=not issues, issues=issues, repetition_score=repetition_score)


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


def inline_bold_to_html(text: str) -> str:
    escaped = escape_html(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def blog_content_to_html(blog_title: str, blog_content: str) -> str:
    blocks = split_blocks(blog_content)
    html_parts: list[str] = [f"<h2>{escape_html(blog_title)}</h2>"]

    for block in blocks:
        stripped = block.strip()

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


def render_success_email(
    *,
    topic_entry: dict[str, Any],
    draft: dict[str, Any],
    seo: dict[str, Any],
    remaining_after_send: int,
) -> str:
    keywords = "; ".join(seo["suggested_seo_keywords"])
    blog_html = blog_content_to_html(draft["blog_title"], draft["blog_content"])

    return f"""<html>
  <body style="font-family: Arial, Helvetica, sans-serif; line-height: 1.6; color: #222;">
    <p><strong>TOPIC BACKLOG:</strong><br>{remaining_after_send} topics remaining</p>

    <p><strong>TOPIC:</strong><br>{escape_html(topic_entry.get('topic', ''))}</p>

    <p><strong>ANGLE:</strong><br>{escape_html(topic_entry.get('angle', ''))}</p>

    <p><strong>BLOG TITLE:</strong><br>{escape_html(draft['blog_title'])}</p>

    <p><strong>DYNAMIC PAGE LINK:</strong><br>&nbsp;</p>

    <p><strong>SEO META TITLE:</strong><br>{escape_html(seo['seo_meta_title'])}</p>

    <p><strong>SEO META DESCRIPTION:</strong><br>{escape_html(seo['seo_meta_description'])}</p>

    <p><strong>SUGGESTED SEO KEYWORDS:</strong><br>{escape_html(keywords)}</p>

    <hr>

    <p><strong>BLOG CONTENT:</strong></p>

    {blog_html}
  </body>
</html>
"""


def render_review_email(
    *,
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    verifier: dict[str, Any],
    reason: str,
) -> str:
    return f"""REVIEW REQUIRED: BLOG DRAFT WITHHELD

TOPIC:
{topic_entry.get('topic', '')}

ANGLE:
{topic_entry.get('angle', '')}

AUDIENCE:
{topic_entry.get('audience', 'general_global')}

WITHHOLD REASON:
{reason}

CLASSIFIER:
{json.dumps(classifier, ensure_ascii=False, indent=2)}

LEGAL MEMO:
{json.dumps(memo, ensure_ascii=False, indent=2)}

VERIFIER:
{json.dumps(verifier, ensure_ascii=False, indent=2)}
"""


# ============================================================
# Main workflow
# ============================================================

def main() -> None:
    topics = load_topics(TOPICS_PATH)
    topic_index, topic_entry, remaining_count = pick_topic(topics, args.topic_index)

    if args.dry_run:
        print(json.dumps({"selected_topic": topic_entry, "remaining_unused": remaining_count}, ensure_ascii=False, indent=2))
        return

    openai_api_key = require_env("OPENAI_API_KEY")
    require_env("SENDGRID_API_KEY")

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

    memo = call_responses_api(
        openai_api_key,
        instructions=LEGAL_MEMO_INSTRUCTIONS,
        input_text=build_legal_input(topic_entry, classifier, legal_sources_text),
        schema=LEGAL_MEMO_SCHEMA,
        model=OPENAI_MODEL,
    )

    verifier = call_responses_api(
        openai_api_key,
        instructions=VERIFIER_INSTRUCTIONS,
        input_text=build_verifier_input(topic_entry, classifier, memo),
        schema=VERIFIER_SCHEMA,
        model=OPENAI_MODEL,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", topic_entry.get("topic", "untitled").lower()).strip("-")[:80]

    write_run_artifact(
        f"{timestamp}_{slug}_analysis.json",
        {
            "topic": topic_entry,
            "classifier": classifier,
            "selected_legal_authority_packs": [str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths],
            "memo": memo,
            "verifier": verifier,
        },
    )

    if not verifier["publishable"]:
        review_email = render_review_email(
            topic_entry=topic_entry,
            classifier=classifier,
            memo=memo,
            verifier=verifier,
            reason="Legal verifier blocked publication pending legal review.",
        )
        sent = send_email_via_sendgrid(
            subject=f"Review required: {topic_entry.get('topic', 'Swiss immigration blog topic')}",
            body=review_email,
        )
        if not sent:
            raise RuntimeError("Legal verifier blocked publication and review email delivery failed.")
        print("Review email sent because legal verifier blocked publication.")
        return

    draft = call_responses_api(
        openai_api_key,
        instructions=DRAFT_INSTRUCTIONS,
        input_text=build_draft_input(topic_entry, classifier, memo, verifier, website_context_text),
        schema=DRAFT_SCHEMA,
        model=OPENAI_MODEL,
    )
    draft = normalise_draft_output(draft)
    editorial_validation = validate_editorial_quality(draft)

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
                validation=editorial_validation,
            ),
            schema=DRAFT_SCHEMA,
            model=OPENAI_MODEL,
        )
        draft = normalise_draft_output(draft)
        editorial_validation = validate_editorial_quality(draft)

    if not editorial_validation.passed:
        raise RuntimeError(
            "Editorial validation still failed after automatic revision passes:\n"
            + "\n".join(f"- {issue}" for issue in editorial_validation.issues)
        )

    seo = call_responses_api(
        openai_api_key,
        instructions=SEO_INSTRUCTIONS,
        input_text=build_seo_input(topic_entry, draft),
        schema=SEO_SCHEMA,
        model=OPENAI_MODEL,
    )

    write_run_artifact(
        f"{timestamp}_{slug}_final.json",
        {
            "topic": topic_entry,
            "classifier": classifier,
            "selected_legal_authority_packs": [str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths],
            "memo": memo,
            "verifier": verifier,
            "editorial_validation": {
                "passed": editorial_validation.passed,
                "issues": editorial_validation.issues,
                "repetition_score": editorial_validation.repetition_score,
                "revision_count": revision_count,
                "word_count": word_count(draft["blog_content"]),
            },
            "draft": draft,
            "seo": seo,
        },
    )

    email_body = render_success_email(
        topic_entry=topic_entry,
        draft=draft,
        seo=seo,
        remaining_after_send=remaining_count - 1,
    )

    sent = send_email_via_sendgrid(
        subject=f"Blog draft: {draft['blog_title']}",
        body=email_body,
        is_html=True,
    )
    if not sent:
        raise RuntimeError("Draft generated but SendGrid delivery failed.")

    topics[topic_index]["status"] = "used"
    topics[topic_index]["used_title"] = draft["blog_title"]
    topics[topic_index]["used_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with TOPICS_PATH.open("w", encoding="utf-8") as f:
        json.dump(topics, f, indent=2, ensure_ascii=False)

    print(f"Draft email sent successfully for: {draft['blog_title']}")


if __name__ == "__main__":
    main()
