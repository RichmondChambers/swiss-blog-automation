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
    description="Generate Swiss immigration blog drafts ready for publication."
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
        "If no legal authorities or internal legal notes are found, continue using website editorial "
        "for editorial context only. Website editorial is never treated as legal authority."
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

ARTICLE_STRUCTURE_VARIANTS = [
    {
        "name": "legal_framework_first",
        "description": (
            "Start with the legal framework, then explain practical consequences, "
            "timing issues, evidence considerations and next steps."
        ),
        "preferred_flow": [
            "opening",
            "legal framework",
            "who is affected",
            "common misunderstanding",
            "practical consequences",
            "evidence considerations",
            "what this means in practice",
            "what to do next",
            "disclaimer",
            "CTA",
        ],
    },
    {
        "name": "problem_solution",
        "description": (
            "Start with the practical problem or misconception, then explain why it arises, "
            "how the law approaches it and what the reader should do next."
        ),
        "preferred_flow": [
            "opening",
            "practical problem",
            "why the issue arises",
            "legal framework",
            "risk areas",
            "examples",
            "what this means in practice",
            "what to do next",
            "disclaimer",
            "CTA",
        ],
    },
    {
        "name": "client_scenarios",
        "description": (
            "Use one or two short anonymised scenarios or patterns to explain the issue, "
            "then draw out the legal and practical principles."
        ),
        "preferred_flow": [
            "opening",
            "scenario or pattern",
            "legal framework",
            "why outcomes differ",
            "evidence considerations",
            "timing or strategy",
            "what this means in practice",
            "what to do next",
            "disclaimer",
            "CTA",
        ],
    },
    {
        "name": "myth_correction",
        "description": (
            "Start by correcting a common misconception, then explain the accurate legal position "
            "and how clients should approach the issue."
        ),
        "preferred_flow": [
            "opening",
            "common misconception",
            "correct legal position",
            "exceptions or qualifications",
            "practical risks",
            "evidence considerations",
            "what this means in practice",
            "what to do next",
            "disclaimer",
            "CTA",
        ],
    },
    {
        "name": "decision_framework",
        "description": (
            "Structure the article around the sequence of decisions a client needs to make, "
            "rather than around a fixed legal explainer format."
        ),
        "preferred_flow": [
            "opening",
            "first decision point",
            "second decision point",
            "legal framework",
            "evidence and timing",
            "common mistakes",
            "what this means in practice",
            "what to do next",
            "disclaimer",
            "CTA",
        ],
    },
]


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
- Where documents or evidence are mentioned, make clear they are examples only and not an exhaustive checklist.

Return strict JSON only.
""".strip()

DRAFT_INSTRUCTIONS = f"""
You are drafting a client-facing blog post for a Swiss immigration law firm.
Draft only from the legal memo, legal sources and editorial instructions supplied.
Do not add new legal propositions that are not supported by the memo or supplied legal sources.

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
- Do not include a section headed **Quick Answer**, **At a Glance** or **In Brief**.
- Do not use a standardised article template for every topic. Follow the article structure variant supplied in the input.

Structure variety:
- Use the supplied article_structure_variant to vary the shape, order and emphasis of the article.
- The article should not always follow the same sequence of headings.
- You may begin with a practical misconception, a legal framework, a decision point, a short anonymised scenario or a timing problem, depending on the supplied variant.
- Keep the required sections **What This Means in Practice** and **What To Do Next**, but vary the surrounding structure and headings.
- Avoid making every article look or feel like a checklist-led explainer.

Required article elements:
- strong SEO-conscious title, without overclaiming
- bold opening paragraph
- second introductory paragraph
- clear legal framework where relevant
- practical explanation of who is affected
- explanation of common misunderstandings or risk areas where relevant
- evidence or document discussion where relevant
- practical examples or short anonymised scenarios where useful
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
- Prefer prose over bullet points.
- Use bullet points or numbered lists only where they materially improve clarity and no prose alternative would work as well.
- Avoid multiple bullet-point sections in the same article.
- The **What To Do Next** section should usually be written in prose, not as a numbered list, unless the topic genuinely requires a sequence.

Terminology:
- Do not use the phrase "ordinary C".
- Prefer "an ordinary C-permit", "the ordinary C-permit route", or "the ordinary route to a C permit", depending on context.
- Use "C permit" or "C-permit" consistently and naturally.
- Always use LEI / AIG, never AIG on its own.
- Always use OASA / VZAE, never VZAE on its own.

Formatting requirements:
- Use keyword-optimised sub-headings throughout.
- Every sub-heading must be surrounded by a blank line above and below.
- Format each sub-heading in bold using double asterisks only, for example: **How Student Residence Is Counted for a Swiss C Permit**
- The first paragraph of the article must be fully bold using double asterisks.
- Immediately after the opening paragraph, include a second introductory paragraph explaining what the post will cover, who it is useful for, and what the reader will learn.
- Do not include legal authorities in the first paragraph.
- Do not include legal authorities in the second introductory paragraph.
- You may use simple markdown tables where a comparison is genuinely clearer as a table.
- No emojis.

Legal authority requirements:
- Include a small number of short legal authority references throughout the body after the first two paragraphs.
- Legal authority references must be legally accurate and should include article numbers wherever relevant.
- Refer to named official sources where supported, such as SEM guidance, SEM Weisungen AIG, or specific articles supplied by the memo.
- Do not invent nationality lists, canton-specific practice, forms, fees or procedural requirements.

Evidence and document caveats:
- Whenever suggesting documents or evidence, state that the documents mentioned are examples only.
- Make clear that the documents required in any individual case depend on the applicant's facts, route, canton, timing and procedural posture.
- Make clear that Richmond Chambers Switzerland provides clients with a tailored checklist of required and recommended documents.
- Make clear that documents must satisfy the strict content, format, translation, certification, date and submission requirements set by the competent migration authorities.
- Do not present document examples as an exhaustive checklist.
- Do not imply that producing the listed examples will necessarily be sufficient.

Practicality requirements:
- Include a concise section headed exactly: **What This Means in Practice**
- Include a concise section headed exactly: **What To Do Next**
- The "What To Do Next" section should help the reader identify the exact decision, risk or problem; check the route, permit category or legal basis; identify whether the weakness is legal, evidential, timing-related, procedural or discretionary; gather relevant evidence; and decide whether applying, waiting, reapplying, appealing or preserving status is more realistic.
- Prefer explaining this in prose rather than as a list.
- Where useful, include one or two brief anonymised scenarios or patterns.
- Do not invent facts, lists, nationality categories or canton-specific practice.

Specificity and uncertainty:
- Where the article refers to "some nationalities", "certain countries", "some cantons" or similar, either name the relevant category accurately from the legal memo or say that the point must be checked against current official guidance from the named official body.
- Distinguish clearly between settled legal framework, canton-specific practice, evidential issues and genuinely discretionary areas.
- Use caution where needed, but avoid repeated hedging.

Disclaimer:
- Include a short disclaimer before the CTA or immediately before the end.
- It must say in substance that the article summarises Swiss immigration law/guidance at the date of writing, individual facts/evidence/cantonal handling may affect the outcome, and it is not legal advice.

SEO:
- Integrate relevant SEO keywords and keyword variations naturally.
- Avoid duplicating existing firm content where website editorial context indicates an overlapping article.
- Do not let SEO phrasing dominate the article.

CTA:
- The final CTA heading must be exactly: {CTA_HEADING}
- The CTA must be concrete and restrained.
- It should explain what {CTA_NAME} would review or do, such as reviewing the permit history, decision, route, legal basis, evidence, timing and options.
- If the CTA mentions documents or evidence, include the tailored-checklist and strict-authority-requirements caveats.
- Invite readers to contact {CTA_NAME} by telephone on {CTA_PHONE} or by completing an enquiry form to arrange an initial consultation meeting.
- Avoid generic sales language.

Under DYNAMIC PAGE LINK, return an empty string only.

Output strict JSON only.
""".strip()

SEO_INSTRUCTIONS = """
You are generating SEO metadata for a Swiss immigration law article.
Return strict JSON only.

Requirements:
- meta title max 60 characters
- meta description max 155 characters
- suggested slug
- primary keyword
- exactly 6 suggested SEO keyword phrases
- natural, non-promotional, legally accurate
- avoid melodramatic or absolute wording
- avoid keyword stuffing
- do not suggest clickbait phrasing
- do not use identical keyword patterns across articles where more natural alternatives are available
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
            "key_issues": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 10,
            },
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
                        "legal_basis": {"type": "string"},
                        "authority_type": {
                            "type": "string",
                            "enum": [
                                "statute",
                                "ordinance",
                                "official_guidance",
                                "case_law",
                                "cantonal_practice",
                                "internal_note",
                                "editorial_context",
                                "mixed",
                                "unclear",
                            ],
                        },
                        "entitlement_or_discretion": {
                            "type": "string",
                            "enum": [
                                "entitlement",
                                "eligibility_only",
                                "facilitated_access",
                                "discretionary",
                                "unclear",
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
                        "issue",
                        "rule",
                        "legal_basis",
                        "authority_type",
                        "entitlement_or_discretion",
                        "reader_category",
                        "exceptions",
                        "procedure_points",
                        "cantonal_practice_points",
                        "reader_distinctions",
                        "evidence_needed",
                        "common_mistakes",
                        "client_decision_points",
                        "practical_implications",
                        "source_reference_to_use_in_article",
                        "safe_public_formulation",
                        "cautious_public_formulation",
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
            "suggested_slug": {"type": "string"},
            "primary_keyword": {"type": "string"},
            "suggested_seo_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 6,
                "maxItems": 6,
            },
        },
        "required": [
            "seo_meta_title",
            "seo_meta_description",
            "suggested_slug",
            "primary_keyword",
            "suggested_seo_keywords",
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


def select_article_structure_variant(topic_entry: dict[str, Any]) -> dict[str, Any]:
    basis = (
        topic_entry.get("topic", "")
        + "|"
        + topic_entry.get("angle", "")
        + "|"
        + topic_entry.get("subtopic", "")
        + "|"
        + topic_entry.get("pillar", "")
    )
    index = sum(ord(char) for char in basis) % len(ARTICLE_STRUCTURE_VARIANTS)
    return ARTICLE_STRUCTURE_VARIANTS[index]


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


def build_legal_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    legal_sources_text: str,
    website_context_text: str,
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "audience_brief": audience_brief(topic_entry.get("audience", "general_global")),
            "topic_metadata": derive_topic_metadata(topic_entry),
            "classifier": classifier,
            "legal_sources": legal_sources_text,
            "website_editorial_context": (
                "Use only for content positioning, overlap awareness and tone. "
                "Do not treat website editorial as legal authority.\n\n"
                f"{website_context_text[:8000]}"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def build_draft_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    website_context: str,
) -> str:
    structure_variant = select_article_structure_variant(topic_entry)

    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "audience_brief": audience_brief(topic_entry.get("audience", "general_global")),
            "topic_metadata": derive_topic_metadata(topic_entry),
            "article_structure_variant": structure_variant,
            "classifier": classifier,
            "legal_memo": memo,
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
                "do_not_include_quick_answer": True,
                "prefer_prose_over_bullets": True,
                "avoid_multiple_bullet_sections": True,
                "practical_sections_required": ["What This Means in Practice", "What To Do Next"],
                "disclaimer_required": True,
                "document_evidence_caveat_required": (
                    "Whenever documents or evidence are suggested, say that they are examples only; "
                    "that Richmond Chambers Switzerland provides clients with a tailored checklist of "
                    "required and recommended documents; and that all documents must satisfy the strict "
                    "content, format, translation, certification, date and submission requirements set "
                    "by the competent migration authorities."
                ),
                "terminology_preferences": {
                    "avoid": ["ordinary C"],
                    "prefer": [
                        "an ordinary C-permit",
                        "the ordinary C-permit route",
                        "the ordinary route to a C permit",
                    ],
                },
                "structure_variety": {
                    "selected_variant": structure_variant["name"],
                    "description": structure_variant["description"],
                    "preferred_flow": structure_variant["preferred_flow"],
                },
                "website_context_use": "Use for continuity and overlap avoidance only. Do not use as legal authority.",
                "website_context": website_context[:8000],
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def build_seo_input(topic_entry: dict[str, Any], draft: dict[str, Any]) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "blog_title": draft["blog_title"],
            "blog_excerpt": draft["blog_content"][:3500],
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
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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
# Normalisation
# ============================================================

def replace_legal_abbreviation_style(text: str) -> str:
    text = re.sub(r"(?<!LEI / )\bAIG\b", "LEI / AIG", text)
    text = re.sub(r"(?<!OASA / )\bVZAE\b", "OASA / VZAE", text)
    return text


def replace_informal_c_permit_terms(text: str) -> str:
    text = re.sub(r"\bordinary C\b", "an ordinary C-permit", text, flags=re.IGNORECASE)
    text = re.sub(r"\bOrdinary C\b", "An ordinary C-permit", text)
    return text


def remove_forbidden_public_phrases(text: str) -> str:
    cleaned = text
    for phrase in FORBIDDEN_PUBLIC_PHRASES:
        cleaned = re.sub(re.escape(phrase), "this issue", cleaned, flags=re.IGNORECASE)
    return cleaned


def split_blocks(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", text.strip()) if chunk.strip()]


def is_bold_heading(block: str) -> bool:
    if not (block.startswith("**") and block.endswith("**")):
        return False
    inner = block[2:-2].strip()
    return len(inner.split()) <= 14 and not inner.endswith(".")


def remove_near_top_summary_section(blog_content: str) -> str:
    blocks = split_blocks(blog_content)
    if not blocks:
        return blog_content

    summary_headings = {
        "quick answer",
        "at a glance",
        "in brief",
    }

    cleaned_blocks: list[str] = []
    skip_next = False

    for block in blocks:
        if skip_next:
            skip_next = False
            continue

        heading_text = re.sub(r"^\*\*|\*\*$", "", block.strip()).strip().lower()
        heading_text = heading_text.rstrip(":")

        if heading_text in summary_headings:
            skip_next = True
            continue

        cleaned_blocks.append(block)

    return "\n\n".join(cleaned_blocks)


def normalise_draft_output(draft: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(draft)
    cleaned["dynamic_page_link"] = ""

    blog_content = cleaned.get("blog_content", "").strip()
    blog_content = replace_legal_abbreviation_style(blog_content)
    blog_content = remove_forbidden_public_phrases(blog_content)
    blog_content = remove_near_top_summary_section(blog_content)
    blog_content = replace_informal_c_permit_terms(blog_content)
    blog_content = re.sub(r"\n{3,}", "\n\n", blog_content).strip()

    cleaned["blog_content"] = blog_content
    cleaned["blog_title"] = replace_informal_c_permit_terms(
        replace_legal_abbreviation_style(cleaned.get("blog_title", "").strip())
    )
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
# Email rendering
# ============================================================

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

    <p><strong>SUGGESTED SLUG:</strong><br>{escape_html(seo.get('suggested_slug', ''))}</p>

    <p><strong>PRIMARY KEYWORD:</strong><br>{escape_html(seo.get('primary_keyword', ''))}</p>

    <p><strong>SUGGESTED SEO KEYWORDS:</strong><br>{escape_html(keywords)}</p>

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

    authority_map = load_authority_pack_map(AUTHORITY_MAP_PATH)
    selected_pack_paths = resolve_authority_pack_paths(topic_entry, authority_map)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "selected_topic": topic_entry,
                    "remaining_unused": remaining_count,
                    "mapped_authority_packs": [
                        str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths
                    ],
                    "selected_article_structure_variant": select_article_structure_variant(topic_entry),
                    "expected_retrieval_queries": [
                        topic_entry.get("topic", ""),
                        topic_entry.get("angle", ""),
                        topic_entry.get("subtopic", ""),
                        topic_entry.get("pillar", ""),
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    openai_api_key = require_env("OPENAI_API_KEY")
    require_env("SENDGRID_API_KEY")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", topic_entry.get("topic", "untitled").lower()).strip("-")[:80]
    run_base = f"{timestamp}_{slug}"

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

    website_context_chunks = simple_retrieve(
        website_editorial_chunks,
        retrieval_queries,
        limit=3,
        allowed_source_kinds={"website_editorial"},
    )

    if not legal_chunks:
        if not args.allow_editorial_fallback:
            raise RuntimeError("No usable legal authority or internal legal note was retrieved for this topic.")

        legal_sources_text = (
            "No legal authority or internal legal note was retrieved. "
            "Website editorial context may be used only for tone and positioning, not as legal authority. "
            "Draft cautiously and avoid unsupported legal detail."
        )
    else:
        legal_sources_text = format_sources_for_prompt(legal_chunks)

    website_context_text = format_sources_for_prompt(website_context_chunks)

    memo = call_responses_api(
        openai_api_key,
        instructions=LEGAL_MEMO_INSTRUCTIONS,
        input_text=build_legal_input(topic_entry, classifier, legal_sources_text, website_context_text),
        schema=LEGAL_MEMO_SCHEMA,
        model=OPENAI_MODEL,
    )

    write_run_artifact(
        f"{run_base}_analysis.json",
        {
            "topic": topic_entry,
            "classifier": classifier,
            "selected_legal_authority_packs": [
                str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths
            ],
            "selected_article_structure_variant": select_article_structure_variant(topic_entry),
            "memo": memo,
        },
    )

    draft = call_responses_api(
        openai_api_key,
        instructions=DRAFT_INSTRUCTIONS,
        input_text=build_draft_input(topic_entry, classifier, memo, website_context_text),
        schema=DRAFT_SCHEMA,
        model=OPENAI_MODEL,
    )
    draft = normalise_draft_output(draft)

    seo = call_responses_api(
        openai_api_key,
        instructions=SEO_INSTRUCTIONS,
        input_text=build_seo_input(topic_entry, draft),
        schema=SEO_SCHEMA,
        model=OPENAI_MODEL,
    )

    final_payload = {
        "topic": topic_entry,
        "classifier": classifier,
        "selected_legal_authority_packs": [
            str(path.relative_to(SCRIPT_DIR)) for path in selected_pack_paths
        ],
        "selected_article_structure_variant": select_article_structure_variant(topic_entry),
        "memo": memo,
        "draft": draft,
        "seo": seo,
    }

    write_run_artifact(f"{run_base}_final.json", final_payload)

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

    print(f"Draft email sent successfully and topic marked used: {draft['blog_title']}")


if __name__ == "__main__":
    main()
