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
# Prompts
# ============================================================

CLASSIFIER_INSTRUCTIONS = """
You are assisting a Swiss immigration law content workflow.
Classify the requested article before drafting.
Return strict JSON only.
Do not write the article.
""".strip()

LEGAL_MEMO_INSTRUCTIONS = f"""
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
- Distinguish carefully between automatic rules, discretionary outcomes, procedural requirements and cantonal practice.
- Identify exceptions, preservation mechanisms, qualifications and reader-category distinctions.
- If EU/EFTA and non-EU treatment differs, state that.
- If canton-specific handling matters, state that.

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
You are drafting a blog post for a Swiss immigration law firm.
Draft only from the verified legal memo and editorial instructions supplied.
Do not add new legal propositions not present in the verified memo.

Writing requirements:
- UK English
- calm, authoritative, analytical
- continuous prose as the default
- avoid generic openings
- vary sentence rhythm and section architecture naturally
- no citations in the public article text
- no markdown
- no emojis
- restrained, factual CTA
- no bullet-heavy drafting

Output strict JSON only.
""".strip()

SEO_INSTRUCTIONS = """
You are generating SEO metadata for a Swiss immigration law article.
Return strict JSON only.
Requirements:
- meta title max 60 characters
- meta description max 155 characters
- 6 keyword phrases
- natural, non-promotional, legally accurate
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
            "distinctions_required": {
                "type": "array",
                "items": {"type": "string"},
            },
            "source_needs": {
                "type": "array",
                "items": {"type": "string"},
            },
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
            "editorial_notes": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "style_profile_used": {"type": "string"},
                    "overlap_risk": {"type": "string"},
                    "internal_link_suggestions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["style_profile_used", "overlap_risk", "internal_link_suggestions"],
            },
        },
        "required": ["blog_title", "dynamic_page_link", "blog_content", "editorial_notes"],
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
            "suggested_seo_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 6,
                "maxItems": 6,
            },
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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

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

    subtopic_key = f"{pillar}:{subtopic}"
    selected.extend(authority_map.get("subtopic_overrides", {}).get(subtopic_key, []))

    selected.extend(authority_map.get("article_type_extras", {}).get(article_type, []))
    selected.extend(authority_map.get("legal_complexity_extras", {}).get(legal_complexity, []))

    deduped: list[str] = []
    seen = set()
    for item in selected:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return [LEGAL_AUTHORITIES_DIR / rel_path for rel_path in deduped]


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


def load_chunks_from_folder(folder: Path, source_kind: str) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    if not folder.is_dir():
        return chunks
    for pdf_path in sorted(folder.glob("*.pdf")):
        text = read_pdf_text(pdf_path)
        if text:
            chunks.append(KnowledgeChunk(source_name=pdf_path.name, source_kind=source_kind, text=text))
    return chunks


def legacy_load_knowledge_folder(folder: Path) -> list[KnowledgeChunk]:
    """
    Backward-compatible loader for the old flat knowledge/ folder.
    Files are treated as website_editorial unless the filename strongly suggests otherwise.
    """
    chunks: list[KnowledgeChunk] = []
    if not folder.is_dir():
        return chunks

    for pdf_path in sorted(folder.glob("*.pdf")):
        lower = pdf_path.name.lower()
        if any(token in lower for token in ["statute", "ordinance", "aig", "fnia", "sem", "directive"]):
            source_kind = "legal_authority"
        elif any(token in lower for token in ["memo", "note", "precedent", "internal"]):
            source_kind = "internal_legal_note"
        else:
            source_kind = "website_editorial"

        text = read_pdf_text(pdf_path)
        if text:
            chunks.append(KnowledgeChunk(source_name=pdf_path.name, source_kind=source_kind, text=text))
    return chunks


def load_knowledge() -> list[KnowledgeChunk]:
    if LEGAL_AUTHORITIES_DIR.exists() or INTERNAL_NOTES_DIR.exists() or WEBSITE_EDITORIAL_DIR.exists():
        chunks: list[KnowledgeChunk] = []
        chunks.extend(load_chunks_from_folder(LEGAL_AUTHORITIES_DIR, "legal_authority"))
        chunks.extend(load_chunks_from_folder(INTERNAL_NOTES_DIR, "internal_legal_note"))
        chunks.extend(load_chunks_from_folder(WEBSITE_EDITORIAL_DIR, "website_editorial"))
        return chunks

    return legacy_load_knowledge_folder(KNOWLEDGE_DIR)


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


def select_legal_sources(all_chunks: list[KnowledgeChunk], queries: list[str]) -> list[KnowledgeChunk]:
    primary = simple_retrieve(
        all_chunks,
        queries,
        limit=8,
        allowed_source_kinds={"legal_authority", "internal_legal_note"},
    )

    if primary:
        legal_authorities = [c for c in primary if c.source_kind == "legal_authority"]
        if legal_authorities or args.allow_editorial_fallback:
            return primary

    return []


def select_website_context(all_chunks: list[KnowledgeChunk], queries: list[str]) -> list[KnowledgeChunk]:
    return simple_retrieve(
        all_chunks,
        queries,
        limit=3,
        allowed_source_kinds={"website_editorial"},
    )


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


def build_legal_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    legal_sources_text: str,
) -> str:
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
    style_profiles = {
        "risk_analysis": "Open with a practical legal risk or trap, then explain the governing rule and consequences.",
        "myth_correction": "Open by correcting a legally inaccurate assumption readers often make, then unpack the true position.",
        "procedural_strategy": "Open with a process or timing problem readers mishandle, then explain the law through that procedural lens.",
        "comparison_piece": "Open by contrasting two routes or statuses readers wrongly treat as equivalent, then compare them carefully.",
        "scenario_led": "Open with a realistic scenario and use it to structure the analysis.",
    }

    article_type = classifier.get("article_type", topic_entry.get("article_type", "risk_analysis"))
    style_hint = style_profiles.get(article_type, style_profiles["risk_analysis"])

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
            "style_hint": style_hint,
            "website_context_use": "Use for continuity, overlap avoidance and internal linking only. Do not use as legal authority.",
            "website_context": website_context[:8000],
        },
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


def send_email_via_sendgrid(subject: str, body: str) -> bool:
    if not SENDGRID_API_KEY:
        raise RuntimeError("Missing SENDGRID_API_KEY")

    payload = {
        "personalizations": [{"to": [{"email": EMAIL_TO}], "subject": subject}],
        "from": {"email": EMAIL_FROM},
        "reply_to": {"email": REPLY_TO},
        "content": [{"type": "text/plain", "value": body}],
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        status, _ = post_json("https://api.sendgrid.com/v3/mail/send", payload=payload, headers=headers)
        return status == 202
    except RuntimeError:
        return False


def render_success_email(
    *,
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    verifier: dict[str, Any],
    draft: dict[str, Any],
    seo: dict[str, Any],
    remaining_after_send: int,
) -> str:
    keywords = "; ".join(seo["suggested_seo_keywords"])
    return f"""TOPIC BACKLOG:
{remaining_after_send} topics remaining

TOPIC:
{topic_entry.get('topic', '')}

ANGLE:
{topic_entry.get('angle', '')}

AUDIENCE:
{topic_entry.get('audience', 'general_global')}

CLASSIFIER:
{json.dumps(classifier, ensure_ascii=False, indent=2)}

LEGAL MEMO:
{json.dumps(memo, ensure_ascii=False, indent=2)}

VERIFIER:
{json.dumps(verifier, ensure_ascii=False, indent=2)}

BLOG TITLE:
{draft['blog_title']}

DYNAMIC PAGE LINK:
{draft['dynamic_page_link']}

SEO META TITLE:
{seo['seo_meta_title']}

SEO META DESCRIPTION:
{seo['seo_meta_description']}

SUGGESTED SEO KEYWORDS:
{keywords}

EDITORIAL NOTES:
{json.dumps(draft['editorial_notes'], ensure_ascii=False, indent=2)}

---------------------------------

BLOG CONTENT:

{draft['blog_content']}
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

    all_chunks = load_knowledge()

    classifier = call_responses_api(
        openai_api_key,
        instructions=CLASSIFIER_INSTRUCTIONS,
        input_text=build_classifier_input(topic_entry),
        schema=CLASSIFIER_SCHEMA,
        model=OPENAI_MODEL,
    )

    retrieval_queries = list(classifier.get("key_issues", [])) + [topic_entry.get("topic", ""), topic_entry.get("angle", "")]

    legal_chunks = select_legal_sources(all_chunks, retrieval_queries)
    if not legal_chunks:
        reason = "No usable legal authority or internal legal note was retrieved for this topic."
        review_email = render_review_email(
            topic_entry=topic_entry,
            classifier=classifier,
            memo={"article_positioning": "", "issues": [], "open_questions": [reason]},
            verifier={
                "publishable": False,
                "unsupported_claims": [reason],
                "overbroad_claims": [],
                "missing_qualifications": [],
                "cantonal_sensitivity": [],
                "required_reader_distinctions": [],
                "revision_actions": ["Add primary legal source material for this topic before drafting."],
            },
            reason=reason,
        )
        sent = send_email_via_sendgrid(
            subject=f"Review required: {topic_entry.get('topic', 'Swiss immigration blog topic')}",
            body=review_email,
        )
        if not sent:
            raise RuntimeError("No legal sources found and review email delivery failed.")
        print("Review email sent because no legal sources were retrieved.")
        return

    legal_sources_text = format_sources_for_prompt(legal_chunks)
    website_context_text = format_sources_for_prompt(select_website_context(all_chunks, retrieval_queries))

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
            reason="Verifier blocked publication pending legal review.",
        )
        sent = send_email_via_sendgrid(
            subject=f"Review required: {topic_entry.get('topic', 'Swiss immigration blog topic')}",
            body=review_email,
        )
        if not sent:
            raise RuntimeError("Verifier blocked publication and review email delivery failed.")
        print("Review email sent because verifier blocked publication.")
        return

    draft = call_responses_api(
        openai_api_key,
        instructions=DRAFT_INSTRUCTIONS,
        input_text=build_draft_input(topic_entry, classifier, memo, verifier, website_context_text),
        schema=DRAFT_SCHEMA,
        model=OPENAI_MODEL,
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
            "memo": memo,
            "verifier": verifier,
            "draft": draft,
            "seo": seo,
        },
    )

    email_body = render_success_email(
        topic_entry=topic_entry,
        classifier=classifier,
        memo=memo,
        verifier=verifier,
        draft=draft,
        seo=seo,
        remaining_after_send=remaining_count - 1,
    )

    sent = send_email_via_sendgrid(
        subject=f"Blog draft [{topic_entry.get('audience', 'general_global')}]: {draft['blog_title']}",
        body=email_body,
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
