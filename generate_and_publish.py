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
parser.add_argument(
    "--allow-low-confidence-legal-memo",
    action="store_true",
    help="Allow drafting even if the legal memo contains low-confidence issues. Not recommended for production.",
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
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "info@richmondchambers.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "paul.richmond@richmondchambers.com")
REPLY_TO = os.environ.get("EMAIL_REPLY_TO", EMAIL_TO)

CTA_HEADING = "Contact Our Immigration Lawyers In Switzerland"
CTA_NAME = "Richmond Chambers Switzerland"
CTA_PHONE = "+41 21 588 07 70"
CMS_SUPPLIES_STANDARD_CTA = os.environ.get("CMS_SUPPLIES_STANDARD_CTA", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

SCRIPT_DIR = Path(__file__).resolve().parent
TOPICS_PATH = SCRIPT_DIR / "topics.json"
KNOWLEDGE_DIR = SCRIPT_DIR / "knowledge"
LEGAL_AUTHORITIES_DIR = KNOWLEDGE_DIR / "legal_authorities"
INTERNAL_NOTES_DIR = KNOWLEDGE_DIR / "internal_legal_notes"
WEBSITE_EDITORIAL_DIR = KNOWLEDGE_DIR / "website_editorial"
OUTPUT_DIR = SCRIPT_DIR / "generated_blog_runs"
AUTHORITY_MAP_PATH = SCRIPT_DIR / "authority_pack_map.json"

SUPPORTED_KNOWLEDGE_EXTENSIONS = {".md", ".txt", ".json", ".pdf"}


# ============================================================
# Editorial constants
# ============================================================

MAX_BLOG_WORDS = 1500
TARGET_BLOG_WORDS = "1,050 to 1,300"
MAX_REPAIR_ATTEMPTS = 2

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

TITLE_LABEL_LIKE_PATTERNS = [
    r"\brequirements?\b",
    r"\brules?\b",
    r"\boverview\b",
    r"\bprocedure\b",
    r"\bprocess\b",
    r"\bguide\b",
    r"\bchecklist\b",
    r"\bcriteria\b",
    r"\bsteps?\b",
    r"\bby\s+[a-z][\w/-]*\b",
]

TITLE_SIGNAL_PATTERNS = [
    r"\?",
    r"\b(whether|should|can|must|qualify|eligible|eligibility|refusal|risk|risky|consequence|impact)\b",
    r"\b(vs\.?|versus|rather than|or)\b",
    r"\b(misconception|myth|mistake|trap|pitfall|timing|deadline|delay|too early|too late)\b",
    r"\b(problem|issue|challenge|difference|compare|comparison|matters?)\b",
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
            "applicant next steps with lawyer support",
            "CTA",
            "disclaimer",
        ],
        "required_section_headings": [],
        "optional_heading_examples": [
            "How the Legal Framework Applies",
            "Why Timing and Permit History Matter",
            "How Applicants Should Approach the Issue",
            "How Our Swiss Immigration Lawyers Can Help",
        ],
        "next_steps_style": "applicant_first_then_lawyer_support",
    },
    {
        "name": "problem_solution",
        "description": (
            "Start with the practical problem or misconception, then explain why it arises, "
            "how the law approaches it and how applicants can respond."
        ),
        "preferred_flow": [
            "opening",
            "practical problem",
            "why the issue arises",
            "legal framework",
            "risk areas",
            "examples",
            "practical response",
            "CTA",
            "disclaimer",
        ],
        "required_section_headings": [],
        "optional_heading_examples": [
            "Where Applicants Often Go Wrong",
            "Why the Answer Depends on the Route",
            "How to Approach the Problem",
            "When Legal Advice Can Change the Strategy",
        ],
        "next_steps_style": "problem_response",
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
            "practical next steps",
            "CTA",
            "disclaimer",
        ],
        "required_section_headings": [],
        "optional_heading_examples": [
            "Two Common Patterns",
            "Why Similar Cases Can Produce Different Outcomes",
            "Evidence and Timing Issues",
            "Planning the Next Step",
        ],
        "next_steps_style": "scenario_based",
    },
    {
        "name": "myth_correction",
        "description": (
            "Start by correcting a common misconception, then explain the accurate legal position "
            "and how applicants should approach the issue."
        ),
        "preferred_flow": [
            "opening",
            "common misconception",
            "correct legal position",
            "exceptions or qualifications",
            "practical risks",
            "evidence considerations",
            "applicant action points",
            "CTA",
            "disclaimer",
        ],
        "required_section_headings": [],
        "optional_heading_examples": [
            "The Common Misunderstanding",
            "The More Accurate Position",
            "Why the Detail Matters",
            "What Applicants Should Do Before Filing",
        ],
        "next_steps_style": "action_points_in_prose",
    },
    {
        "name": "decision_framework",
        "description": (
            "Structure the article around the sequence of decisions an applicant needs to make, "
            "rather than around a fixed legal explainer format."
        ),
        "preferred_flow": [
            "opening",
            "first decision point",
            "second decision point",
            "legal framework",
            "evidence and timing",
            "common mistakes",
            "next decision",
            "CTA",
            "disclaimer",
        ],
        "required_section_headings": [],
        "optional_heading_examples": [
            "First, Identify the Route",
            "Then Check the Timing",
            "Then Review the Evidence",
            "Deciding Whether to Apply Now",
        ],
        "next_steps_style": "decision_sequence",
    },
    {
        "name": "practical_consequences",
        "description": (
            "Focus on the practical consequences of a rule, decision or mistake, then explain "
            "how applicants can reduce risk before taking action."
        ),
        "preferred_flow": [
            "opening",
            "practical consequences",
            "legal basis",
            "risk reduction",
            "evidence and procedure",
            "when to seek advice",
            "CTA",
            "disclaimer",
        ],
        "required_section_headings": [],
        "optional_heading_examples": [
            "Why This Can Affect the Application",
            "How the Authorities May Look at the Issue",
            "Reducing the Risk Before You Apply",
            "Where Legal Advice Helps",
        ],
        "next_steps_style": "risk_reduction",
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
- For each practical mechanism discussed (for example, a deadline, preservation route, lapse rule, discretionary request, exception, approval pathway or restoration route), identify the legal basis that should be cited near the public-facing explanation.
- Identify evidence/documents that a lawyer would usually want to review.
- Identify common mistakes and refusal risks.
- State which propositions are safe for public article use and which need cautious wording.
- Where documents or evidence are mentioned, make clear they are examples only and not an exhaustive checklist.
- In public-facing formulations, use clear English by default.
- If a non-English official or legal term is useful for precision, give both French and German where both are relevant. For example: "SEM Directives on the Foreign Nationals and Integration Act (Directives LEI / AIG; Weisungen AIG)".
- Do not use only the German term or only the French term where both terms are commonly relevant in Switzerland.
- Identify and flag any translation ambiguity where a legal proposition depends on translated legislation or translated official guidance.
- For Article 34(5) LEI / AIG analysis, distinguish clearly between temporary education/training residence, the final five-year uninterrupted residence period, and the two-year post-study durable residence-permit condition.

Return strict JSON only.
""".strip()

DRAFT_INSTRUCTIONS = f"""
You are drafting a client-facing blog post for a Swiss immigration law firm.
Draft only from the legal memo, legal sources and editorial instructions supplied.
Do not add new legal propositions that are not supported by the memo or legal sources.

Core editorial target:
- Write like a careful Swiss immigration practitioner advising a discerning prospective client.
- Be concise but still thorough.
- Be specific rather than generic.
- Be practical rather than merely descriptive.
- Be authoritative without overclaiming.
- Be conversion-aware without sounding like marketing copy.
- The article must read as though it was written by a human Swiss immigration lawyer, not by AI.

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
- Use the article_structure_variant to vary the shape, order and emphasis of the article.
- The article should not always follow the same sequence of headings.
- Do not automatically include sections headed **What This Means in Practice** or **What To Do Next**.
- Only use those headings if they are genuinely the best headings for the specific article.
- Prefer varied headings that suit the topic and authorial approach, such as **How Applicants Should Approach the Issue**, **Where Timing Problems Arise**, **Planning the Next Step**, **Reducing the Risk Before You Apply**, **When Legal Advice Can Change the Strategy**, or other natural topic-specific headings.
- Each post should read as though it was written by a different human author, not as though it follows a single house template.
- Vary paragraph rhythm, section order, heading style and use of examples.
- You may begin with a practical misconception, a legal framework, a decision point, a short anonymised scenario or a timing problem, depending on the variant.
- Do not rely on fixed practical headings. Use headings that fit the article’s topic, structure variant and natural flow.
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
- a practical next-step or strategy section, but its heading should vary by article
- restrained CTA
- short italicised legal disclaimer at the very end, after the CTA

Writing requirements:
- UK English.
- Calm, authoritative, analytical and natural.
- Professional but clear and easy to understand.
- Explain legal issues in practical language that a non-lawyer can follow.
- Every post must deliver concrete reader value, not just legal description.
- Include practical guidance that a reader can act on, such as best-practice tips, practical case patterns, decision checks, or evidence-preparation advice tailored to the topic.
- Include at least two concrete "reader-helpful" elements in each post (for example: a best-practice tip and a short anonymised scenario, or a practical decision framework and common pitfall-avoidance advice).
- Write as a human legal professional, not as an internal system.
- Never refer to "the memo", "the verified memo", "the materials", "the verified materials", "any article on this topic", "the supplied guidance", "the supplied legal sources", source packs, drafting inputs or internal validation.
- Avoid phrasing that suggests the article was generated from supplied materials or AI prompts.
- Avoid empty emphasis, filler and generic transition language.
- Avoid formulaic phrases such as "that distinction matters", "the practical point is", and "it is important to note" unless genuinely necessary.
- Prefer concrete framing over broad marketing abstractions.
- Prefer prose over bullet points.
- Use bullet points or numbered lists only where they materially improve clarity and no prose alternative would work as well.
- Avoid multiple bullet-point sections in the same article.
- Avoid repeated references to "the person". Prefer "an applicant", "the applicant", "a foreign national", "a family member", "the sponsor", "the employer" or another precise category depending on context.
- Where a route, preservation mechanism, exception, approval or benefit is discretionary or subject to authority assessment, avoid wording that implies automatic availability. Prefer formulations such as "Swiss law allows an applicant to request...", "the competent authority may...", or "a request may be made...", depending on the legal memo.
- Where practical shorthand such as "centre of life", "genuine residence", "main home" or similar factual-residence language is used, make clear where relevant that this is a factual assessment, not a single-document test or guaranteed day-count formula.
- Where a more precise client-friendly formulation is available, avoid unnecessarily absolute or punitive wording. For example, prefer "may need to establish a fresh basis for residence" over "will be treated as a new entrant" unless the legal memo clearly supports the stronger formulation.

Title requirements:
- Write a polished public-facing title, not an internal topic label.
- Make the title clear, client-facing, specific, legally accurate, restrained and SEO-conscious.
- Avoid clickbait, melodramatic framing, generic legal labels and overbroad claims.
- Avoid flat label patterns such as "[Permit Type] Requirements", "[Route] Rules", "[Topic] by [Factor]", "[Legal Category] Overview", "[Topic] Procedure", "[Topic] Process" or "[Topic] Guide".
- Where appropriate, prefer a title that naturally introduces a practical question, decision point, client problem, risk, consequence, contrast, misconception or timing issue.
- Question-led titles are often effective where the article addresses a common client misconception.
- Colons are allowed where they add useful contrast or a practical question; avoid colon subtitles that just append generic keywords.
- Avoid long title-plus-subtitle constructions using a colon, or a question followed by multiple explanatory phrases, unless genuinely necessary.
- For question-led titles, do not usually add a subtitle after the question.
- Keep the blog title preferably under 75 characters and normally under 90 characters.

Terminology:
- Do not use the phrase "ordinary C".
- Prefer "an ordinary C-permit", "the ordinary C-permit route", or "the ordinary route to a C permit", depending on context.
- Use "C permit" or "C-permit" consistently and naturally.
- Always use LEI / AIG, never AIG on its own.
- Always use OASA / VZAE, never VZAE on its own.

Swiss official-source terminology:
- Use clear English by default.
- For SEM directives/instructions, prefer "SEM Directives" or, on the first technical reference where precision helps, "SEM Directives on the Foreign Nationals and Integration Act (Directives LEI / AIG; Weisungen AIG)".
- Do not use only "Weisungen AIG", only "SEM Weisungen", only "Directives LEI / AIG", or only another non-English term in the public blog content.
- Where a non-English Swiss legal or official term is used and both French and German terms are commonly relevant, provide both French and German rather than only one language.
- Do not overload the article with bilingual terminology. Use the English term alone where that is clearer and sufficient.

Formatting requirements:
- Use SEO-aware sub-headings throughout, but keep them natural and readable.
- Integrate relevant keywords into sub-headings only where they fit naturally.
- Do not force exact-match keyword phrases into every sub-heading.
- Avoid repetitive keyword stems across consecutive sub-headings.
- Prioritise clear, user-helpful sub-headings over aggressive optimisation.
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
- Where the article explains a specific legal mechanism, deadline, preservation route, lapse rule, discretionary request or exception, include the relevant article number close to the first substantive explanation of that mechanism where supported by the legal memo.
- Do not place the only article-number citation for an operative mechanism in a neighbouring section if that mechanism is explained substantively later.
- Refer to named official sources where appropriate, such as SEM guidance, SEM Directives, the SEM Directives on the Foreign Nationals and Integration Act (Directives LEI / AIG; Weisungen AIG), or specific legislation.
- Do not write "the supplied guidance", "the supplied materials", "the guidance supplied", "the materials provided", "the legal sources supplied", or similar phrases in the blog content.
- Do not invent nationality lists, canton-specific practice, forms, fees or procedural requirements.

Evidence and document caveats:
- Whenever suggesting documents or evidence, state that the documents mentioned are examples only.
- Make clear, where relevant, that the documents required in any individual case depend on the applicant's facts, route, canton, timing and procedural posture.
- Vary the wording across posts. Do not use the same document caveat formula every time.
- Suitable formulations include:
  "These are examples only. The documents required in any individual case depend on the applicant’s facts, route, canton, timing and procedural posture."
  "At Richmond Chambers Switzerland, we provide our clients with a tailored checklist of all required and recommended supporting documents based on the circumstances of their case. We then carefully review our clients’ supporting documents to ensure that they satisfy the strict requirements set by the migration authorities in terms of content, format, translation and certification."
  "The precise evidence required will depend on the route, canton and procedural stage. Our Swiss immigration lawyers provide tailored document checklists and review supporting evidence against the requirements of the competent migration authority."
- Do not present document examples as an exhaustive checklist.
- Do not imply that producing the listed examples will necessarily be sufficient.
- Do not repeat the document caveat in the CTA if it has already been explained in the body.

Practicality and next-step requirements:
- Include a practical section that helps applicants understand what they should do next, but vary the heading and structure.
- Do not automatically call this section **What To Do Next**.
- The practical next-step section should open from the applicant’s perspective.
- Start by explaining what applicants should identify, check, reconstruct, preserve, gather, compare or decide.
- Then explain how our Swiss immigration lawyers, Swiss immigration team or immigration lawyers in Switzerland can help refine the analysis, review the evidence and advise on strategy.
- Avoid a section that only describes what our lawyers would do.
- A good pattern is: first, what the applicant needs to think about; second, how legal advice adds value; third, what strategic choice may follow.
- Use varied formulations across posts.
- Suitable approaches include:
  "Applicants should start by reconstructing their residence history before assuming that a particular route is available. Our Swiss immigration lawyers can then assess whether the issue is legal, evidential, timing-related or discretionary."
  "Before filing, applicants should identify the exact weakness in the case. Our Swiss immigration team can review the legal basis, evidence and timing before advising whether to apply now, wait or take a different procedural step."
  "If the issue is already live, the first step is to understand whether the problem is one of eligibility, evidence or timing. Our immigration lawyers in Switzerland can then advise on the realistic options."
- Prefer prose over numbered steps unless a sequence is essential.
- Where useful, include one or two brief anonymised scenarios or patterns.
- Do not invent facts, lists, nationality categories or canton-specific practice.
- Where the topic concerns a preventable immigration risk before a step is taken (for example, departure, deregistration, filing, renewal, appeal deadline, employment change or sponsorship action), include the risk-reduction point before the final evidence/CTA section, not only at the end.

Specificity and uncertainty:
- Where the article refers to "some nationalities", "certain countries", "some cantons" or similar, either name the relevant category accurately from the legal memo or say that the point must be checked against current official guidance from the named official body.
- Distinguish clearly between settled legal framework, canton-specific practice, evidential issues and genuinely discretionary areas.
- Use caution where needed, but avoid repeated hedging.
- Avoid stacking cautious verbs such as "may", "might", "can" and "often" where the legal memo supports a more direct proposition.

CTA:
- The final CTA heading must be exactly: {CTA_HEADING}
- The CTA must be the final substantive section before the disclaimer.
- Do not create a practical fallback section after the CTA.
- Do not use the heading **Practical Tips Before You Apply**.
- The CTA must be concrete, personal and restrained.
- It should explain what {CTA_NAME} and our specialist Swiss immigration lawyers would review or do.
- Prefer personal formulations such as:
  "At Richmond Chambers Switzerland, our specialist Swiss immigration lawyers would be pleased to review..."
  "Our Swiss immigration team can review..."
  "Our immigration lawyers in Switzerland can advise on..."
- The CTA should not merely repeat the practical next-step section.
- Avoid repeating the document-checklist point if it has already been made in the body.
- If document support has not been mentioned elsewhere, the CTA may include a concise value proposition about tailored document checklists, evidence review, strategy, timing, procedural options or risk reduction.
- Vary the CTA value proposition across posts.
- Invite readers to contact {CTA_NAME} by telephone on {CTA_PHONE} or by completing an enquiry form to arrange an initial consultation meeting.
- Avoid generic sales language.

Disclaimer:
- The disclaimer must appear at the very end of the blog content, after the CTA.
- The disclaimer must be italicised using single asterisks.
- It must say in substance that the article summarises Swiss immigration law/guidance at the date of writing, individual facts/evidence/cantonal handling/procedural posture may affect the outcome, and it is not legal advice.
- Do not place the disclaimer before the CTA.

SEO:
- Integrate relevant SEO keywords and keyword variations naturally.
- Avoid duplicating existing firm content where website editorial context indicates an overlapping article.
- Do not let SEO phrasing dominate the article.

Under DYNAMIC PAGE LINK, return an empty string only.

Output strict JSON only.
""".strip()

REPAIR_INSTRUCTIONS = f"""
You are repairing an existing Swiss immigration blog draft that failed public validation.

Repair-only scope:
- Preserve the legal substance from the supplied legal memo and draft.
- Do not add new legal propositions, new facts, invented authority, invented procedures, invented nationality lists, invented canton-specific practice, invented fees or invented document requirements.
- Fix malformed headings, duplicated sections, awkward generated phrasing, excessive repetition and structural defects.
- Soften unnecessarily absolute consequences where the legal memo supports a more precise cautious formulation, while preserving the legal warning.
- Keep the CTA heading exactly: {CTA_HEADING}
- Keep the CTA as the final substantive section before the italicised disclaimer.
- Keep the article under {MAX_BLOG_WORDS} words.
- If the draft is over the word limit, shorten materially by removing repetition, merging overlapping sections, cutting generic commentary and preserving only the strongest legal and practical points.
- Do not preserve every paragraph if length/structure requires edits.
- When validation errors indicate the title is flat, label-like or overlong, rewrite the title as a clearer client-facing title that preserves the draft's legal scope.
- Do not make the revised title clickbait, melodramatic, legally overbroad or narrower than the article.
- Prefer practical title angles such as a question, decision point, contrast, risk or consequence where this fits the article.
- Keep revised titles preferably under 75 characters and normally under 90 characters.
- Keep the first paragraph bold and the final disclaimer italicised with single asterisks.
- Use UK English.
- Return strict JSON only using DRAFT_SCHEMA.
""".strip()

SEO_INSTRUCTIONS = """
You are generating SEO metadata for a Swiss immigration law article.
Return strict JSON only.

Requirements:
- meta title max 60 characters
- meta title should be concise, search-led and legally accurate
- the article title may be more reader-facing; the meta title may be shorter and more keyword-led
- do not use a flat duplicate of the article title where a more natural search title is available
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
                        "translation_or_source_caution": {"type": "string"},
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
                        "translation_or_source_caution",
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
        raise RuntimeError(f"Cannot read PDF because PyPDF2 is not installed: {path}")

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise RuntimeError(f"Could not open PDF file: {path}") from exc

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


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace").strip()


def read_knowledge_file(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return read_pdf_text(path)

    if suffix in {".md", ".txt", ".json"}:
        return read_text_file(path)

    raise RuntimeError(f"Unsupported knowledge file type: {path}")


def load_selected_legal_authority_chunks(authority_paths: list[Path]) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []

    for authority_path in authority_paths:
        if not authority_path.exists():
            raise RuntimeError(f"Mapped legal authority pack not found: {authority_path}")

        text = read_knowledge_file(authority_path)
        if text:
            chunks.append(
                KnowledgeChunk(
                    str(authority_path.relative_to(SCRIPT_DIR)),
                    "legal_authority",
                    text,
                )
            )

    return chunks


def load_chunks_from_folder(folder: Path, source_kind: str) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    if not folder.is_dir():
        return chunks

    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
            continue

        text = read_knowledge_file(path)
        if text:
            chunks.append(
                KnowledgeChunk(
                    str(path.relative_to(SCRIPT_DIR)),
                    source_kind,
                    text,
                )
            )

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
                "official_source_language_style": (
                    "Use clear English by default. If a non-English official term is useful for precision, "
                    "provide both French and German where both are relevant. For SEM directives/instructions, "
                    "use 'SEM Directives' or first use 'SEM Directives on the Foreign Nationals and Integration Act "
                    "(Directives LEI / AIG; Weisungen AIG)'. Do not use only the French or only the German term."
                ),
                "subheading_style": (
                    "Use bold sub-headings with a blank line above and below each one. "
                    "Sub-headings should be SEO-aware but natural: include relevant keywords only where they "
                    "fit cleanly, avoid stuffing, and do not force exact-match keyword phrases into every heading."
                ),
                "opening_style": "The first paragraph must be fully bold, followed immediately by a second introductory paragraph without legal citations.",
                "title_style_guidance": (
                    "Generate a polished, public-facing blog title, not an internal topic label. "
                    "The title should be clear, specific, restrained and legally accurate, and should "
                    "ideally surface a practical question, decision point, contrast, risk, consequence, "
                    "client problem, misconception or timing issue where appropriate."
                ),
                "do_not_include_quick_answer": True,
                "prefer_prose_over_bullets": True,
                "avoid_multiple_bullet_sections": True,
                "fixed_practical_headings_required": False,
                "avoid_default_headings": [
                    "What This Means in Practice",
                    "What To Do Next",
                ],
                "practical_section_guidance": (
                    "Include a practical next-step or strategy section, but vary the heading according to the "
                    "article_structure_variant and topic. The section should open from the applicant's perspective: "
                    "what the applicant should identify, check, reconstruct, preserve, gather, compare or decide. "
                    "Then explain how Richmond Chambers Switzerland, our Swiss immigration lawyers, our Swiss "
                    "immigration team or our immigration lawyers in Switzerland can add value by reviewing the "
                    "legal basis, evidence, timing, procedural posture and strategic options."
                ),
                "reader_value_requirement": (
                    "Every post must be concretely useful to readers. Include at least two practical reader-helpful "
                    "elements in the article body, such as best-practice tips, practical case examples/patterns, "
                    "pitfall-avoidance guidance, decision checks, timing strategy or evidence-preparation advice."
                ),
                "document_evidence_caveat_required": (
                    "Whenever documents or evidence are suggested, say that they are examples only. "
                    "Use varied wording across posts. Depending on context, explain that Richmond Chambers "
                    "Switzerland provides clients with a tailored checklist of all required and recommended "
                    "supporting documents based on the circumstances of their case, and carefully reviews "
                    "supporting documents to ensure that they satisfy the strict requirements set by the "
                    "migration authorities in terms of content, format, translation, certification, date "
                    "and submission. Do not repeat this point in the CTA if it has already been made in "
                    "the body."
                ),
                "terminology_preferences": {
                    "avoid": ["ordinary C"],
                    "prefer": [
                        "an ordinary C-permit",
                        "the ordinary C-permit route",
                        "the ordinary route to a C permit",
                    ],
                },
                "cta_style": (
                    "Use a personal, lawyer-led formulation. For example: 'At Richmond Chambers Switzerland, "
                    "our specialist Swiss immigration lawyers would be pleased to review...' or 'Our Swiss "
                    "immigration team can advise on...'. Avoid repeating document checklist wording if it "
                    "already appears in the article body. Vary the CTA value proposition across posts."
                ),
                "what_to_do_next_style": (
                    "Do not create a lawyer-only next-step section. Open from the applicant's perspective, then "
                    "explain how our Swiss immigration lawyers can help. Vary the section heading and wording."
                ),
                "avoid_person_wording": (
                    "Avoid repeated references to 'the person'. Prefer 'the applicant', 'an applicant', "
                    "'the sponsor', 'the employer', 'the family member' or another precise category."
                ),
                "disclaimer_position": (
                    "The disclaimer must appear at the very end of the blog content, after the CTA, and must be italicised."
                ),
                "avoid_ai_source_language": (
                    "Do not write 'the supplied guidance', 'the supplied materials', 'the legal materials supplied', "
                    "'the source material', or any phrase suggesting that the article was produced from internal inputs."
                ),
                "optional_heading_examples": structure_variant.get("optional_heading_examples", []),
                "next_steps_style": structure_variant.get("next_steps_style", ""),
                "authorial_variation_required": (
                    "Vary the structure, heading sequence, paragraph rhythm and practical framing so that posts do "
                    "not all read as if they were written from the same template. The article should read as if "
                    "written by an individual human author."
                ),
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


def build_repair_input(
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    draft: dict[str, Any],
    validation_errors: list[str],
) -> str:
    return json.dumps(
        {
            "topic": topic_entry.get("topic", ""),
            "angle": topic_entry.get("angle", ""),
            "audience": topic_entry.get("audience", "general_global"),
            "classifier": classifier,
            "legal_memo": memo,
            "draft_to_repair": draft,
            "validation_errors": validation_errors,
            "repair_guardrails": (
                "Repair only. Do not add unsupported law, new facts, invented procedures, nationality lists, "
                "canton-specific practice, fees or document requirements."
            ),
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
    text = re.sub(r"\bLEI\s*/\s*LEI\s*/\s*AIG\b", "LEI / AIG", text, flags=re.IGNORECASE)
    text = re.sub(r"\bOASA\s*/\s*OASA\s*/\s*VZAE\b", "OASA / VZAE", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!LEI / )\bAIG\b", "LEI / AIG", text)
    text = re.sub(r"(?<!OASA / )\bVZAE\b", "OASA / VZAE", text)
    return text


def replace_informal_c_permit_terms(text: str) -> str:
    text = re.sub(
        r"\bThe an ordinary C-permit-Permit Route\b",
        "The Ordinary C-Permit Route",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\ban ordinary C-permit-Permit\b", "an ordinary C-permit", text, flags=re.IGNORECASE)
    text = re.sub(r"\bC-permit-Permit\b", "C-permit", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bordinary C\b(?!\s*(?:permit|route|application|case|rules|clock|timing)\b)",
        "an ordinary C-permit",
        text,
        flags=re.IGNORECASE,
    )
    return text


def replace_sem_directives_terms(text: str) -> str:
    """
    Normalise references to SEM directives/instructions.

    Public style:
    - Prefer "SEM Directives" for readability.
    - Avoid stacked bilingual or repeated labels such as:
      "SEM Directives on the Foreign Nationals and Integration Act
      (Directives LEI / AIG; SEM Directives AIG); SEM Directives LEI / AIG)".
    - Where the model has already produced a long technical reference, collapse it
      to the readable public form unless the full technical label is genuinely
      needed elsewhere.
    """
    cleaned = text

    sem_directives_expanded_patterns = [
        r"SEM Directives on the Foreign Nationals and Integration Act\s*"
        r"\([^)]*(?:LEI\s*/\s*AIG|AIG|Weisungen)[^)]*\)"
        r"(?:\s*(?:;|,|and)\s*"
        r"(?:SEM\s+Directives(?:\s+(?:LEI\s*/\s*AIG|AIG))?|Directives\s+LEI\s*/\s*AIG|Weisungen\s+AIG))*",
        r"SEM Directives on the Foreign Nationals and Integration Act\s*"
        r"(?:\s*(?:;|,|and)\s*"
        r"(?:SEM\s+Directives(?:\s+(?:LEI\s*/\s*AIG|AIG))?|Directives\s+LEI\s*/\s*AIG|Weisungen\s+AIG))+",
        r"SEM Directives\s*\([^)]*(?:LEI\s*/\s*AIG|AIG|Weisungen)[^)]*\)"
        r"(?:\s*(?:;|,|and)\s*"
        r"(?:SEM\s+Directives(?:\s+(?:LEI\s*/\s*AIG|AIG))?|Directives\s+LEI\s*/\s*AIG|Weisungen\s+AIG))*",
    ]

    for pattern in sem_directives_expanded_patterns:
        cleaned = re.sub(pattern, "SEM Directives", cleaned, flags=re.IGNORECASE)

    replacements = {
        r"\bSEM Weisungen LEI / AIG\b": "SEM Directives",
        r"\bSEM Weisungen AIG\b": "SEM Directives",
        r"\bWeisungen AIG\b": "SEM Directives",
        r"\bDirectives LEI / AIG\b": "SEM Directives",
        r"\bSEM Directives LEI / AIG\b": "SEM Directives",
        r"\bSEM Directives AIG\b": "SEM Directives",
        r"\bSEM Weisungen\b": "SEM Directives",
        r"\bWeisungen\b": "SEM Directives",
    }

    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(
        r"\bSEM Directives\b(?:\s*(?:;|,|and)\s*\bSEM Directives\b)+",
        "SEM Directives",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\bSEM Directives\s*\(\s*SEM Directives\s*\)",
        "SEM Directives",
        cleaned,
        flags=re.IGNORECASE,
    )

    return cleaned


def replace_ai_source_phrases(text: str) -> str:
    replacements = {
        r"\bthe supplied guidance\b": "SEM guidance",
        r"\bthe guidance supplied\b": "SEM guidance",
        r"\bthe supplied legal sources\b": "the relevant legal framework",
        r"\bthe supplied legal materials\b": "the relevant legal framework",
        r"\bthe supplied materials\b": "the relevant legal framework",
        r"\bthe source material\b": "the relevant legal framework",
        r"\bthe source materials\b": "the relevant legal framework",
        r"\bthe materials provided\b": "the relevant legal framework",
        r"\blegal sources supplied\b": "the relevant legal framework",
        r"\blegal materials supplied\b": "the relevant legal framework",
    }

    cleaned = text
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    return cleaned


def replace_person_references(text: str) -> str:
    replacements = {
        r"\bthe person’s\b": "the applicant’s",
        r"\ba person’s\b": "an applicant’s",
        r"\bthe person\b": "the applicant",
        r"\ba person\b": "an applicant",
        r"\bthat person\b": "that applicant",
        r"\bthis person\b": "this applicant",
    }

    cleaned = text
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    return cleaned


def remove_forbidden_public_phrases(text: str) -> str:
    cleaned = text
    for phrase in FORBIDDEN_PUBLIC_PHRASES:
        cleaned = re.sub(re.escape(phrase), "this issue", cleaned, flags=re.IGNORECASE)
    return cleaned


def split_blocks(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", text.strip()) if chunk.strip()]


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def repair_sentence_start_capitalisation(text: str) -> str:
    sentence_start_patterns = [
        r"(?<=[.!?])(\s+)(an applicant\b)",
        r"(?<=[.!?])(\s+)(the applicant\b)",
        r"(?<=[.!?])(\s+)(an applicant['’]s\b)",
        r"(?<=[.!?])(\s+)(the applicant['’]s\b)",
    ]

    repaired = text
    for pattern in sentence_start_patterns:
        repaired = re.sub(
            pattern,
            lambda match: f"{match.group(1)}{match.group(2)[0].upper()}{match.group(2)[1:]}",
            repaired,
            flags=re.IGNORECASE,
        )
    return repaired


def find_sentence_start_capitalisation_artefacts(text: str) -> list[str]:
    artefact_patterns = [
        r"(?<=[.!?])\s+an applicant\b",
        r"(?<=[.!?])\s+the applicant\b",
        r"(?<=[.!?])\s+an applicant['’]s\b",
        r"(?<=[.!?])\s+the applicant['’]s\b",
    ]
    matches: list[str] = []
    for pattern in artefact_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            token = match.group(0).strip()
            if token and token[0].islower():
                matches.append(token)
    return matches


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


def soften_repeated_practical_headings(blog_content: str, topic_entry: dict[str, Any]) -> str:
    variant = select_article_structure_variant(topic_entry)
    examples = variant.get("optional_heading_examples", [])

    replacement_pairs = {
        "What This Means in Practice": examples[1] if len(examples) > 1 else "What This Means for Applicants",
        "What To Do Next": examples[-1] if examples else "Planning the Next Step",
    }

    cleaned = blog_content

    for old_heading, new_heading in replacement_pairs.items():
        if old_heading == new_heading:
            continue
        cleaned = re.sub(
            rf"\*\*{re.escape(old_heading)}\*\*",
            f"**{new_heading}**",
            cleaned,
            count=1,
        )

    return cleaned


def is_disclaimer_block(block: str) -> bool:
    text = re.sub(r"^\*|\*$", "", block.strip()).strip().lower()

    disclaimer_markers = [
        "not legal advice",
        "does not constitute legal advice",
        "individual facts",
        "individual circumstances",
        "case-specific",
        "cantonal handling",
        "procedural posture",
        "date of writing",
        "current position",
    ]

    return sum(1 for marker in disclaimer_markers if marker in text) >= 2


def insert_before_cta_or_disclaimer(blocks: list[str], new_blocks: list[str]) -> list[str]:
    cta_block = f"**{CTA_HEADING}**"
    for idx, block in enumerate(blocks):
        if block.strip() == cta_block:
            return blocks[:idx] + new_blocks + blocks[idx:]

    for idx, block in enumerate(blocks):
        if is_disclaimer_block(block):
            return blocks[:idx] + new_blocks + blocks[idx:]

    return blocks + new_blocks


def detect_duplicate_practical_sections(blog_content: str) -> list[str]:
    blocks = split_blocks(blog_content)
    practical_heading_patterns = [
        r"practical",
        r"before you apply",
        r"reducing the risk",
        r"planning",
        r"strategy",
        r"evidence",
        r"next step",
    ]

    practical_headings: list[str] = []
    for block in blocks:
        if not is_bold_heading(block):
            continue
        heading_text = re.sub(r"^\*\*|\*\*$", "", block).strip().lower()
        if any(re.search(pattern, heading_text, flags=re.IGNORECASE) for pattern in practical_heading_patterns):
            practical_headings.append(block)

    if len(practical_headings) > 3:
        return [
            f"Too many practical/evidence/strategy sections ({len(practical_headings)}). "
            "Maximum allowed is 3."
        ]
    return []


def validate_title_style(blog_title: str) -> list[str]:
    errors: list[str] = []
    normalized = re.sub(r"\s+", " ", blog_title).strip()
    lowered = normalized.lower()
    title_length = len(normalized)

    if title_length > 95:
        errors.append(f"blog_title is too long ({title_length} characters > 95).")
    if ":" in normalized and title_length > 90:
        errors.append(
            "blog_title uses an overlong title-plus-subtitle format (contains ':' and exceeds 90 characters)."
        )

    if re.search(r"^[^:]{1,40}:\s*(guide|overview|requirements?|rules?|process|procedure)\b", lowered):
        errors.append("blog_title uses a colon subtitle that only appends a generic label.")

    looks_label_like = any(
        re.search(pattern, lowered, flags=re.IGNORECASE)
        for pattern in TITLE_LABEL_LIKE_PATTERNS
    )
    if looks_label_like:
        errors.append(
            "blog_title appears flat or label-like; rewrite as a client-facing title with a practical angle."
        )

    has_practical_signal = any(
        re.search(pattern, lowered, flags=re.IGNORECASE)
        for pattern in TITLE_SIGNAL_PATTERNS
    )
    if not has_practical_signal:
        errors.append(
            "blog_title lacks a practical signal (question, decision point, risk, consequence, contrast or client problem)."
        )

    if re.search(r"\bby\s+[a-z][a-z\s/-]{2,40}$", lowered) and "?" not in normalized:
        errors.append(
            "blog_title ends in a flat 'by [factor]' construction without a client-facing question or contrast."
        )
    return errors


def validate_public_draft(draft: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    blog_title = (draft.get("blog_title") or "").strip()
    blog_content = (draft.get("blog_content") or "").strip()
    dynamic_page_link = draft.get("dynamic_page_link")

    if not blog_title:
        errors.append("blog_title must not be empty.")
    else:
        errors.extend(validate_title_style(blog_title))
    if not blog_content:
        errors.append("blog_content must not be empty.")
    if dynamic_page_link != "":
        errors.append("dynamic_page_link must be exactly an empty string.")

    word_count = count_words(blog_content)
    if word_count > MAX_BLOG_WORDS:
        errors.append(
            f"blog_content exceeds MAX_BLOG_WORDS ({word_count} > {MAX_BLOG_WORDS})."
        )

    malformed_patterns = [
        r"\bThe an\b",
        r"\ban ordinary C-permit-Permit\b",
        r"\bC-permit-Permit\b",
        r"\bLEI\s*/\s*LEI\s*/\s*AIG\b",
        r"\bOASA\s*/\s*OASA\s*/\s*VZAE\b",
        r"\bSEM Directives Directives\b",
        r"\bContact Our Immigration Lawyers In Switzerland\b[\s\S]*\bPractical Tips Before You Apply\b",
    ]
    for pattern in malformed_patterns:
        if re.search(pattern, blog_content, flags=re.IGNORECASE):
            errors.append(f"Malformed or undesirable output pattern found: {pattern}")
    capitalisation_artefacts = find_sentence_start_capitalisation_artefacts(blog_content)
    if capitalisation_artefacts:
        errors.append(
            "Sentence-start capitalisation artefacts found after punctuation: "
            + ", ".join(sorted(set(capitalisation_artefacts)))
        )

    blocks = split_blocks(blog_content)
    cta_block = f"**{CTA_HEADING}**"
    cta_indexes = [idx for idx, block in enumerate(blocks) if block.strip() == cta_block]
    if len(cta_indexes) != 1:
        errors.append(f"Expected exactly one CTA heading '{cta_block}', found {len(cta_indexes)}.")
    else:
        cta_index = cta_indexes[0]
        if cta_index == len(blocks) - 1:
            errors.append("CTA heading must have body text after it.")
        else:
            cta_body_blocks = blocks[cta_index + 1 :]
            if not any(not is_bold_heading(block) and not is_disclaimer_block(block) for block in cta_body_blocks):
                errors.append("CTA heading must have body text after it.")
            else:
                cta_body_text = "\n\n".join(
                    block for block in cta_body_blocks if not is_bold_heading(block) and not is_disclaimer_block(block)
                )
                cta_body_lower = cta_body_text.lower()
                has_help_text = any(
                    marker in cta_body_lower
                    for marker in [
                        "can help",
                        "would review",
                        "will review",
                        "our immigration lawyers",
                        "richmond chambers switzerland",
                    ]
                )
                if not has_help_text:
                    errors.append("CTA must include a substantive paragraph explaining how our lawyers can help.")
                if not CMS_SUPPLIES_STANDARD_CTA:
                    has_consultation_sentence = CTA_PHONE in cta_body_text and "enquiry form" in cta_body_lower
                    if not has_consultation_sentence:
                        errors.append(
                            "CTA must include the standard consultation sentence with phone number and enquiry form wording."
                        )

            next_heading_after_cta = any(is_bold_heading(block) for block in cta_body_blocks)
            if next_heading_after_cta:
                errors.append("CTA must be the final substantive section before the disclaimer.")

    if not blocks:
        errors.append("blog_content must contain at least one content block.")
    else:
        final_block = blocks[-1].strip()
        if not is_disclaimer_block(final_block):
            errors.append("The final block must be a disclaimer.")

        italic_single_asterisk = bool(
            re.match(r"^\*(?!\*)([\s\S]+?)(?<!\*)\*$", final_block)
        )
        if not italic_single_asterisk:
            errors.append("The final disclaimer must be italicised with single asterisks.")

    errors.extend(detect_duplicate_practical_sections(blog_content))
    return errors


def validate_legal_memo(memo: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    issues = memo.get("issues")

    if not isinstance(issues, list) or not issues:
        errors.append("Legal memo must contain at least one issue.")
        return errors

    for index, issue in enumerate(issues, start=1):
        confidence = issue.get("confidence")
        if confidence == "low" and not args.allow_low_confidence_legal_memo:
            errors.append(
                f"Issue {index} has low confidence; rerun with --allow-low-confidence-legal-memo only if intentional."
            )

        support = issue.get("support")
        if not isinstance(support, list) or not support:
            errors.append(f"Issue {index} has empty support; each issue must include at least one support item.")

        if issue.get("authority_type") == "unclear":
            errors.append(f"Issue {index} has authority_type='unclear', which is not allowed.")

    return errors


def repair_draft_if_needed(
    *,
    openai_api_key: str,
    topic_entry: dict[str, Any],
    classifier: dict[str, Any],
    memo: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    current = draft
    errors = validate_public_draft(current)
    if not errors:
        return current

    for _ in range(MAX_REPAIR_ATTEMPTS):
        repaired = call_responses_api(
            openai_api_key,
            instructions=REPAIR_INSTRUCTIONS,
            input_text=build_repair_input(
                topic_entry=topic_entry,
                classifier=classifier,
                memo=memo,
                draft=current,
                validation_errors=errors,
            ),
            schema=DRAFT_SCHEMA,
            model=OPENAI_MODEL,
        )
        current = normalise_draft_output(repaired, topic_entry)
        errors = validate_public_draft(current)
        if not errors:
            return current

    raise RuntimeError("Public draft validation failed after repair:\n- " + "\n- ".join(errors))


def ensure_italic_disclaimer_at_end(blog_content: str) -> str:
    blocks = split_blocks(blog_content)
    if not blocks:
        return blog_content

    disclaimer_blocks: list[str] = []
    non_disclaimer_blocks: list[str] = []

    for block in blocks:
        if is_disclaimer_block(block):
            disclaimer_blocks.append(block)
        else:
            non_disclaimer_blocks.append(block)

    if disclaimer_blocks:
        disclaimer_text = re.sub(r"^\*|\*$", "", disclaimer_blocks[-1].strip()).strip()
    else:
        disclaimer_text = (
            "This article summarises Swiss immigration law and guidance as at the date of writing. "
            "Individual facts, evidence, cantonal handling and procedural posture can materially affect "
            "the outcome. It is not legal advice."
        )

    disclaimer_text = disclaimer_text.rstrip(".") + "."
    italic_disclaimer = f"*{disclaimer_text}*"

    return "\n\n".join(non_disclaimer_blocks + [italic_disclaimer])


def ensure_reader_usefulness_content(blog_content: str) -> str:
    blocks = split_blocks(blog_content)
    if not blocks:
        return blog_content

    disclaimer_block = ""
    if is_disclaimer_block(blocks[-1]):
        disclaimer_block = blocks.pop()

    body_text = "\n\n".join(blocks).lower()
    guidance_markers = [
        "best practice",
        "in practice",
        "for example",
        "scenario",
        "next step",
        "before filing",
        "common mistake",
        "risk",
        "evidence",
        "timing",
        "check whether",
        "should start by",
    ]
    marker_count = sum(1 for marker in guidance_markers if marker in body_text)

    practical_signal_terms = [
        "timeline",
        "timing",
        "evidence",
        "filing strategy",
        "strategy",
        "preparation",
        "next step",
        "next steps",
        "risk reduction",
        "reduce risk",
        "before you apply",
        "before filing",
    ]

    def has_substantive_practical_section(content_blocks: list[str]) -> bool:
        idx = 0
        while idx < len(content_blocks):
            block = content_blocks[idx]
            if not is_bold_heading(block):
                idx += 1
                continue

            heading_text = re.sub(r"^\*\*|\*\*$", "", block).strip().lower()
            section_chunks: list[str] = []
            look_ahead = idx + 1
            while look_ahead < len(content_blocks) and not is_bold_heading(content_blocks[look_ahead]):
                if not is_disclaimer_block(content_blocks[look_ahead]):
                    section_chunks.append(content_blocks[look_ahead].strip())
                look_ahead += 1

            section_text = " ".join(section_chunks).strip().lower()
            section_word_count = count_words(section_text)
            signal_hits = sum(
                1
                for term in practical_signal_terms
                if term in heading_text or term in section_text
            )
            if signal_hits >= 2 and section_word_count >= 35:
                return True
            idx = look_ahead
        return False

    has_existing_substantive_practical_section = has_substantive_practical_section(blocks)

    if marker_count >= 2 and has_existing_substantive_practical_section:
        return blog_content

    fallback_heading = "**Planning the Filing Strategy**"
    fallback_paragraph = (
        "Before filing, applicants should identify the route being relied on, reconstruct the permit chronology "
        "and test whether any period creates a timing or evidence risk. The key issue is not only how long the "
        "applicant has lived in Switzerland, but which periods count for the particular C-permit route."
    )
    blocks = insert_before_cta_or_disclaimer(blocks, [fallback_heading, fallback_paragraph])
    if disclaimer_block and not (blocks and is_disclaimer_block(blocks[-1])):
        blocks.append(disclaimer_block)

    return "\n\n".join(blocks)


def enforce_max_blog_words(blog_content: str, max_words: int) -> str:
    if count_words(blog_content) <= max_words:
        return blog_content

    blocks = split_blocks(blog_content)
    if not blocks:
        return blog_content

    cta_block = f"**{CTA_HEADING}**"
    cta_index = next((idx for idx, block in enumerate(blocks) if block.strip() == cta_block), None)

    protected_indexes: set[int] = set()
    if cta_index is not None:
        protected_indexes.update(range(cta_index, len(blocks)))

    if blocks and is_disclaimer_block(blocks[-1]):
        protected_indexes.add(len(blocks) - 1)

    def removable_indexes(prefer_body_only: bool) -> list[int]:
        indexes: list[int] = []
        for idx, block in enumerate(blocks):
            if idx in protected_indexes:
                continue
            if prefer_body_only and is_bold_heading(block):
                continue
            indexes.append(idx)
        return indexes

    for prefer_body_only in (True, False):
        while count_words("\n\n".join(blocks)) > max_words:
            candidates = removable_indexes(prefer_body_only=prefer_body_only)
            if not candidates:
                break
            del blocks[candidates[-1]]

    return "\n\n".join(blocks)


def ensure_cta_requirements(blog_content: str) -> str:
    blocks = split_blocks(blog_content)
    if not blocks:
        return blog_content

    cta_block = f"**{CTA_HEADING}**"
    try:
        cta_index = next(idx for idx, block in enumerate(blocks) if block.strip() == cta_block)
    except StopIteration:
        return blog_content

    section_start = cta_index + 1
    section_end = len(blocks)
    for idx in range(section_start, len(blocks)):
        if is_disclaimer_block(blocks[idx]) or is_bold_heading(blocks[idx]):
            section_end = idx
            break

    cta_body_blocks = [block for block in blocks[section_start:section_end] if block.strip()]
    cta_body_text = "\n\n".join(cta_body_blocks)
    cta_body_lower = cta_body_text.lower()

    lawyer_support_markers = [
        "can help",
        "would review",
        "will review",
        "our immigration lawyers",
        "richmond chambers switzerland",
        "prepare",
        "strategy",
        "evidence",
        "filing",
        "risk",
    ]
    has_substantive_help_paragraph = any(marker in cta_body_lower for marker in lawyer_support_markers) and any(
        count_words(block) >= 20 for block in cta_body_blocks
    )

    consultation_sentence = (
        f"To discuss your case, contact {CTA_NAME} by telephone on {CTA_PHONE} "
        "or complete our enquiry form to arrange an initial consultation meeting."
    )
    has_consultation_sentence = CTA_PHONE in cta_body_text and "enquiry form" in cta_body_lower

    if not has_substantive_help_paragraph:
        cta_body_blocks.insert(
            0,
            (
                f"Our specialist Swiss immigration lawyers at {CTA_NAME} can assess your immigration history, "
                "identify timing and evidence risks, and advise on a filing strategy tailored to your route "
                "and procedural position."
            ),
        )

    if not CMS_SUPPLIES_STANDARD_CTA and not has_consultation_sentence:
        cta_body_blocks.append(consultation_sentence)

    updated_blocks = blocks[:section_start] + cta_body_blocks + blocks[section_end:]
    return "\n\n".join(updated_blocks)


def normalise_draft_output(draft: dict[str, Any], topic_entry: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(draft)
    cleaned["dynamic_page_link"] = ""

    blog_content = cleaned.get("blog_content", "").strip()
    blog_content = replace_legal_abbreviation_style(blog_content)
    blog_content = replace_sem_directives_terms(blog_content)
    blog_content = remove_forbidden_public_phrases(blog_content)
    blog_content = replace_ai_source_phrases(blog_content)
    blog_content = remove_near_top_summary_section(blog_content)
    blog_content = soften_repeated_practical_headings(blog_content, topic_entry)
    blog_content = replace_informal_c_permit_terms(blog_content)
    blog_content = replace_person_references(blog_content)
    blog_content = repair_sentence_start_capitalisation(blog_content)
    blog_content = ensure_reader_usefulness_content(blog_content)
    blog_content = ensure_cta_requirements(blog_content)
    blog_content = ensure_italic_disclaimer_at_end(blog_content)
    blog_content = enforce_max_blog_words(blog_content, MAX_BLOG_WORDS)
    blog_content = re.sub(r"\n{3,}", "\n\n", blog_content).strip()

    cleaned["blog_content"] = blog_content
    cleaned["blog_title"] = replace_person_references(
        replace_informal_c_permit_terms(
            replace_ai_source_phrases(
                replace_sem_directives_terms(
                    replace_legal_abbreviation_style(cleaned.get("blog_title", "").strip())
                )
            )
        )
    )
    return cleaned


# ============================================================
# HTML rendering
# ============================================================

def inline_markdown_to_html(text: str) -> str:
    escaped = escape_html(text)

    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)

    return escaped


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
    html.append("<thead><tr>" + "".join(f"<th>{inline_markdown_to_html(h)}</th>" for h in headers) + "</tr></thead>")
    html.append("<tbody>")

    for row in rows:
        html.append("<tr>" + "".join(f"<td>{inline_markdown_to_html(cell)}</td>" for cell in row) + "</tr>")

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
        html.append(f"<li>{inline_markdown_to_html(item)}</li>")

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

        html_parts.append(f"<p>{inline_markdown_to_html(stripped)}</p>")

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
                        {
                            "path": str(path.relative_to(SCRIPT_DIR)),
                            "exists": path.exists(),
                            "suffix": path.suffix,
                        }
                        for path in selected_pack_paths
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
    memo_validation_errors = validate_legal_memo(memo)
    if memo_validation_errors:
        raise RuntimeError("Legal memo validation failed:\n- " + "\n- ".join(memo_validation_errors))

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
    draft = normalise_draft_output(draft, topic_entry)
    draft = repair_draft_if_needed(
        openai_api_key=openai_api_key,
        topic_entry=topic_entry,
        classifier=classifier,
        memo=memo,
        draft=draft,
    )

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
