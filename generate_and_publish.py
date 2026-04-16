import argparse
import importlib.util
import json
import os
from datetime import datetime, timezone
from urllib import error, request

HAS_PYPDF2 = importlib.util.find_spec("PyPDF2") is not None

if HAS_PYPDF2:
    from PyPDF2 import PdfReader

parser = argparse.ArgumentParser(description="Generate and email Swiss immigration blog drafts.")
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Run end-to-end generation flow without network calls or writing topics.json.",
)
args = parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# -----------------------
# Email configuration
# -----------------------
EMAIL_FROM = os.environ.get("EMAIL_FROM", "info@richmondchambers.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "paul.richmond@richmondchambers.com")
REPLY_TO = os.environ.get("EMAIL_REPLY_TO", EMAIL_TO)

# CTA identity for blog posts
CTA_HEADING = "Contact Our Immigration Lawyers In Switzerland"
CTA_NAME = "Richmond Chambers Switzerland"
CTA_PHONE = "+41 21 588 07 70"


# -----------------------
# GPT system prompt
# -----------------------

SYSTEM_PROMPT = f"""
You are a senior legal blog writer producing authoritative, legally accurate blog posts for Richmond Chambers Switzerland, an immigration law firm specialising exclusively in Swiss immigration law and Swiss immigration routes.

Primary audience:

The audience is broad and international. Readers include:
- Individuals and families anywhere in the world considering relocation to Switzerland for work, study, business, family life, retirement, investment or visits
- Businesses anywhere in the world considering hiring, transferring or posting workers to Switzerland
- Individuals and businesses applying for Swiss work permits, residence permits, family reunification, Schengen visas, or non-gainful activity permits
- Individuals who have already applied for Swiss permits or visas and need to understand refusals, next steps or strategic options
- Individuals and businesses already in Switzerland seeking advice on Swiss immigration law, including permit renewals, upgrading from L to B permits, applying for C permits, fast-track C permit routes, ordinary naturalisation, facilitated naturalisation, family reunification, employer sponsorship and cross-border workforce issues

Your role is to write in-depth, analytical blog posts aimed at educated, time-poor professionals seeking clear, reliable guidance on Swiss immigration options. The legal analysis must always be based on Swiss immigration law and administrative practice. However, the article should be framed for the most relevant audience segment for the topic.

You must demonstrate strong subject-matter expertise in Swiss immigration law, including residence permits, work permits, family reunification, business immigration, Schengen visas, EU/EFTA nationals, non-EU nationals, non-gainful activity permits, transitions between permit types, C permit eligibility, fast-track C permit routes, Swiss citizenship and naturalisation, and related regulatory frameworks. All content must be legally accurate. You must not speculate, invent rules, or hallucinate legal positions. Where necessary, you may supplement your knowledge with careful web research to ensure accuracy and currency.

Audience framing requirements:

Write for the most relevant audience for the topic, not for a generic undifferentiated readership.
Where relevant, address the different positions of:
- individuals outside Switzerland considering relocation
- individuals already in Switzerland
- businesses outside Switzerland transferring or hiring staff into Switzerland
- businesses already operating in Switzerland
- EU/EFTA nationals
- non-EU nationals
- applicants, permit holders and refused applicants

Do not assume the reader is already in Switzerland unless the topic clearly requires it.
Do not assume that all Swiss immigration routes operate the same way for EU/EFTA nationals and non-EU nationals.
Where relevant, explain the practical implications for applicants inside Switzerland, applicants outside Switzerland and employers engaging with cantonal and federal authorities.
Where relevant, correct common misunderstandings, including confusion between residence rights, work authorisation, visa requirements, permit validity, cantonal discretion and routes to settlement or citizenship.

Avoid the following:
- opening with generic statements about "moving to Switzerland" or "navigating immigration rules"
- writing as though all readers are in the same legal position
- treating EU/EFTA nationals and non-EU nationals as though the rules are interchangeable
- assuming that a permit refusal necessarily ends all options
- generic conclusions that merely restate that legal advice may be needed

Prefer:
- concrete scenario-driven analysis
- internationally relevant examples where useful
- careful distinctions between visa, permit, residence status, work authorisation and nationality
- practical explanation of how Swiss rules affect applicants, families and employers in real situations

Before drafting, silently determine:
1. which audience segment is most likely to read this post;
2. what legal misconceptions that audience is likely to have;
3. what practical Swiss immigration scenarios are most relevant.

Do not output that planning note. Use it to improve the specificity of the article.

Writing style and tone:

UK English

Authoritative, analytical, and calm

Professional and non-promotional

Clear, precise prose written in full paragraphs

Discursive and explanatory rather than schematic

No clichés

No emojis

No sales language

No references to yourself as an AI

Content requirements:

Length: typically 1,000–1,500 words per post (around 1,500 words unless the topic clearly requires less)

The blog post must be written predominantly in continuous prose

Lists (including bullet points, numbered lists, or hyphenated lists) should be used sparingly and only where they genuinely improve clarity

Maximum of two lists in total across the entire article

Lists must never be used as a substitute for legal analysis, reasoning, or explanation

The default mode of explanation should always be structured paragraphs, not itemised points

Concrete legal claims rather than vague generalities

Clear explanations of legal reasoning, statutory or regulatory context, and practical consequences

Examples may be included where they genuinely aid understanding, but should be embedded in prose rather than presented as lists

Avoid generic summaries, filler content, or checklist-style drafting

Search optimisation:

Optimise content for search engines using relevant keywords and keyword variations related to Swiss immigration law and Swiss immigration routes

Keywords must be integrated naturally into prose, without keyword stuffing or forced repetition

Also generate:
- a DYNAMIC PAGE LINK heading with a blank line beneath it for a link to be pasted later
- a SUGGESTED SEO KEYWORDS heading containing exactly 6 relevant SEO keyword phrases likely to perform well for the topic and audience

Structure:

A compelling, specific title that clearly reflects the legal subject matter

A concise introduction that frames the legal or practical problem being addressed, without fluff

At least five substantive sections, each developed through paragraphs of analysis rather than lists

Section headings must be descriptive and signal the legal or practical issue being discussed, not merely label a list

A practical conclusion that distils key legal takeaways and implications for readers, written in prose

Mandatory final section:

A final section with the exact sub-heading:
{CTA_HEADING}

Call to action requirement:

Under the sub-heading “{CTA_HEADING}”, include a short, measured call to action written in restrained, professional prose.

The call to action must:
Be relevant to the subject matter of the blog post
Be framed as an invitation to obtain tailored legal advice
Invite readers to contact {CTA_NAME} by telephone on {CTA_PHONE} or by completing an enquiry form to arrange an initial consultation meeting
Remain factual, neutral, and non-promotional

Output format:

Plain text only
Headings clearly marked
No markdown
No citations or footnotes unless explicitly requested
No meta-commentary about the writing process

SEO requirements:
Generate an SEO meta title (maximum 60 characters)
Generate an SEO meta description (maximum 155 characters)
Meta text must be natural, accurate, and non-promotional

Output format EXACTLY as follows:

BLOG TITLE:
<text>

DYNAMIC PAGE LINK:
<leave blank beneath this heading>

SEO META TITLE:
<text>

SEO META DESCRIPTION:
<text>

SUGGESTED SEO KEYWORDS:
<keyword 1>; <keyword 2>; <keyword 3>; <keyword 4>; <keyword 5>; <keyword 6>

BLOG CONTENT:
<full article>
"""


def post_json(url: str, payload: dict, headers: dict):
    """
    POST JSON and return (status_code, parsed_response).

    Notes:
    - OpenAI returns JSON bodies.
    - SendGrid /v3/mail/send commonly returns HTTP 202 with an empty body.
      In that case we return {} instead of attempting json.loads("").
    - If a non-JSON body is returned, we return {"raw_body": "..."}.
    """
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req) as response:
            raw = response.read()
            body = raw.decode("utf-8", errors="replace").strip()

            if not body:
                return response.status, {}

            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, {"raw_body": body}

    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc.reason}") from exc


def extract_chat_completion_text(response: dict) -> str:
    try:
        return response["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as exc:
        raise RuntimeError(f"Unexpected chat completions response shape: {response}") from exc


def extract_responses_text(response: dict) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output_blocks = response.get("output", [])
    collected = []
    for block in output_blocks:
        for part in block.get("content", []):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                collected.append(text.strip())

    if collected:
        return "\n".join(collected).strip()

    raise RuntimeError(f"Unexpected responses API response shape: {response}")


def generate_blog_content(openai_api_key: str, payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {openai_api_key}",
        "Content-Type": "application/json",
    }

    chat_error = None
    try:
        _, chat_response = post_json(
            "https://api.openai.com/v1/chat/completions",
            payload=payload,
            headers=headers,
        )
        return extract_chat_completion_text(chat_response)
    except RuntimeError as exc:
        chat_error = exc
        print(f"Warning: chat completions request failed, trying responses API fallback. Details: {exc}")

    responses_payload = {
        "model": payload["model"],
        "input": payload["messages"],
    }
    try:
        _, responses_response = post_json(
            "https://api.openai.com/v1/responses",
            payload=responses_payload,
            headers=headers,
        )
        return extract_responses_text(responses_response)
    except RuntimeError as responses_exc:
        raise RuntimeError(
            "OpenAI generation failed via both chat completions and responses API. "
            f"chat_completions_error={chat_error}; responses_error={responses_exc}"
        ) from responses_exc


def try_sendgrid_email(payload: dict, sendgrid_api_key: str, context: str) -> bool:
    """
    Send an email via SendGrid and return True on success.

    Returns False for transient/network/API failures so the caller can decide
    whether to continue without crashing the whole run.
    """
    headers = {
        "Authorization": f"Bearer {sendgrid_api_key}",
        "Content-Type": "application/json",
    }

    try:
        status, _ = post_json(
            "https://api.sendgrid.com/v3/mail/send",
            payload=payload,
            headers=headers,
        )
        if status == 202:
            return True

        print(f"Warning: unexpected SendGrid status for {context}: HTTP {status}")
        return False
    except RuntimeError as exc:
        print(f"Warning: SendGrid send failed for {context}: {exc}")
        return False


# -----------------------
# Load authoritative PDF knowledge
# -----------------------

def load_pdf_knowledge(folder="knowledge", max_chars=16000):
    texts = []

    if not HAS_PYPDF2:
        print("Warning: PyPDF2 is not installed; continuing without PDF knowledge.")
        return ""

    if not os.path.isdir(folder):
        print(f"Warning: knowledge folder not found: {folder}")
        return ""

    for filename in sorted(os.listdir(folder)):
        if not filename.lower().endswith(".pdf"):
            continue

        path = os.path.join(folder, filename)

        try:
            reader = PdfReader(path)
        except Exception as exc:
            print(f"Warning: could not read PDF '{filename}': {exc}")
            continue

        pdf_text = []
        for page in reader.pages:
            try:
                text = page.extract_text()
            except Exception:
                text = None
            if text:
                pdf_text.append(text)

        combined = "\n".join(pdf_text)
        combined = " ".join(combined.split())

        if combined:
            texts.append(f"[SOURCE: {filename}]\n{combined}")

    full_text = "\n\n".join(texts)
    return full_text[:max_chars]


def build_audience_brief(topic_entry: dict) -> str:
    audience = (topic_entry.get("audience") or "").strip().lower()

    audience_map = {
        "global_individuals": (
            "Audience emphasis: Write primarily for individuals and families anywhere in the world "
            "considering relocation to Switzerland for work, study, family life, retirement, non-gainful "
            "activity, investment or visits."
        ),
        "global_businesses": (
            "Audience emphasis: Write primarily for businesses anywhere in the world considering hiring, "
            "transferring or posting workers to Switzerland, or establishing a Swiss workforce strategy."
        ),
        "inside_switzerland_individuals": (
            "Audience emphasis: Write primarily for individuals already in Switzerland who need guidance on "
            "permit renewals, permit upgrades, family reunification, settlement, C permit routes or "
            "naturalisation."
        ),
        "inside_switzerland_businesses": (
            "Audience emphasis: Write primarily for businesses already operating in Switzerland that need "
            "advice on bringing workers into Switzerland, permit strategy, compliance and workforce planning."
        ),
        "refused_applicants": (
            "Audience emphasis: Write primarily for individuals or businesses whose Swiss permit or visa "
            "application has been refused and who need to understand reasons, remedies and next steps."
        ),
        "general_global": (
            "Audience emphasis: Write for a broad international audience that may include individuals, families "
            "and businesses both inside and outside Switzerland. Explain why the topic matters across different "
            "Swiss immigration scenarios."
        ),
    }

    audience_aliases = {
        "general": "general_global",
        "global": "general_global",
        "general_international": "general_global",
        "individuals_global": "global_individuals",
        "businesses_global": "global_businesses",
        "switzerland_individuals": "inside_switzerland_individuals",
        "switzerland_businesses": "inside_switzerland_businesses",
    }

    audience = audience_aliases.get(audience, audience)

    if audience in audience_map:
        return audience_map[audience]

    topic_text = f"{topic_entry.get('topic', '')} {topic_entry.get('angle', '')}".lower()

    if any(
        term in topic_text
        for term in [
            "employer",
            "business",
            "hiring",
            "hire",
            "recruitment",
            "worker",
            "posted worker",
            "posted workers",
            "transfer",
            "assignment",
            "company",
            "workforce",
            "salary benchmarking",
        ]
    ):
        return audience_map["global_businesses"]

    if any(
        term in topic_text
        for term in [
            "refusal",
            "refused",
            "appeal",
            "reconsideration",
            "remedy",
            "negative decision",
            "reapply",
            "reapplying",
        ]
    ):
        return audience_map["refused_applicants"]

    if any(
        term in topic_text
        for term in [
            "c permit",
            "naturalisation",
            "naturalization",
            "citizenship",
            "l permit",
            "b permit",
            "permit renewal",
            "renewal",
            "family reunification after divorce",
            "after obtaining a swiss c permit",
        ]
    ):
        return audience_map["inside_switzerland_individuals"]

    return audience_map["general_global"]


def normalize_audience_label(topic_entry: dict) -> str:
    raw = (topic_entry.get("audience") or "").strip().lower()
    aliases = {
        "general": "general_global",
        "global": "general_global",
        "general_international": "general_global",
        "individuals_global": "global_individuals",
        "businesses_global": "global_businesses",
        "switzerland_individuals": "inside_switzerland_individuals",
        "switzerland_businesses": "inside_switzerland_businesses",
    }
    return aliases.get(raw, raw or "general_global")


PDF_KNOWLEDGE = load_pdf_knowledge()

# -----------------------
# Load topics.json
# -----------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_PATH = os.path.join(SCRIPT_DIR, "topics.json")

with open(TOPICS_PATH, "r", encoding="utf-8") as f:
    topics = json.load(f)

unused_topics = [t for t in topics if t.get("status") == "unused"]
remaining_count = len(unused_topics)

# -----------------------
# Topics exhausted handling
# -----------------------

if remaining_count == 0:
    if args.dry_run:
        print("Dry run complete: topics exhausted path reached; skipped SendGrid notification.")
        raise SystemExit(0)

    SENDGRID_API_KEY = require_env("SENDGRID_API_KEY")

    notification_payload = {
        "personalizations": [
            {"to": [{"email": EMAIL_TO}], "subject": "Blog automation: topics exhausted"}
        ],
        "from": {"email": EMAIL_FROM},
        "reply_to": {"email": REPLY_TO},
        "content": [
            {
                "type": "text/plain",
                "value": (
                    "All blog topics in topics.json have been used.\n\n"
                    "No draft was generated on this run.\n\n"
                    'Please add new topics with status "unused" '
                    "and the automation will resume automatically."
                ),
            }
        ],
    }

    sent = try_sendgrid_email(
        notification_payload,
        SENDGRID_API_KEY,
        "topics exhausted notification",
    )
    if sent:
        print("Topics exhausted notification sent.")
    else:
        print("Topics exhausted notification not sent (non-fatal).")
    raise SystemExit(0)

# -----------------------
# Select next unused topic
# -----------------------

topic_index = None
topic_entry = None

for index, topic in enumerate(topics):
    if topic.get("status") == "unused":
        topic_index = index
        topic_entry = topic
        break

if topic_entry is None:
    raise RuntimeError("No unused topic found, even though remaining_count was greater than zero")

audience_brief = build_audience_brief(topic_entry)
audience_label = normalize_audience_label(topic_entry)

# -----------------------
# Generate blog post
# -----------------------

chat_payload = {
    "model": os.environ.get("OPENAI_MODEL", "gpt-5.2"),
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": f"""
The following documents are authoritative reference material produced or endorsed by the organisation.
Use them as your primary source of truth.

If there is any tension between general knowledge and these documents:
- Prefer these documents
- Be conservative
- Do not speculate beyond them

If the documents are silent on a point, you may rely on general knowledge but should qualify uncertainty.

AUTHORITATIVE MATERIAL:
{PDF_KNOWLEDGE}
""".strip(),
        },
        {
            "role": "system",
            "content": f"""
Audience-specific editorial instruction:

{audience_brief}

Frame the article for the most relevant audience for this topic.
The legal content must remain focused on Swiss immigration law.
Where relevant:
- distinguish between applicants outside Switzerland and applicants already in Switzerland
- distinguish between individuals and businesses
- distinguish between EU/EFTA nationals and non-EU nationals where legally relevant
- address practical issues involving cantonal procedure, permit strategy, visa requirements, refusals or transitions between permit categories
- avoid generic international framing that ignores the practical Swiss context
- ensure the final call to action refers to {CTA_NAME} and the telephone number {CTA_PHONE}
- ensure the final section heading is exactly: {CTA_HEADING}
- include a DYNAMIC PAGE LINK heading with a blank line beneath it
- include SUGGESTED SEO KEYWORDS with exactly 6 keyword phrases
""".strip(),
        },
        {
            "role": "user",
            "content": f"""
Topic: {topic_entry['topic']}
Angle: {topic_entry['angle']}
Audience: {audience_label}

Please write this blog post for the most relevant audience for this Swiss immigration topic.
The legal analysis must remain focused on Swiss immigration law, but the framing, examples and practical implications should be relevant to the stated audience above where available.

Do not write as though all readers are in the same legal position.
Where relevant, distinguish:
- applicants outside Switzerland and applicants already in Switzerland
- individuals and businesses
- EU/EFTA nationals and non-EU nationals
- permit applications, visa applications and post-refusal options

Ensure that the final call to action refers to {CTA_NAME} and the telephone number {CTA_PHONE}.
Ensure that the final section heading is exactly: {CTA_HEADING}.
Also provide:
- DYNAMIC PAGE LINK with a blank line beneath it
- SUGGESTED SEO KEYWORDS containing exactly 6 relevant keyword phrases separated by semicolons
""".strip(),
        },
    ],
}

if args.dry_run:
    content = f"""BLOG TITLE:
Dry Run: {topic_entry['topic']}

DYNAMIC PAGE LINK:


SEO META TITLE:
Dry run meta title

SEO META DESCRIPTION:
Dry run description for verification only.

SUGGESTED SEO KEYWORDS:
Swiss immigration lawyer; Swiss residence permit; move to Switzerland; Swiss work permit; Swiss visa application; Swiss immigration law

BLOG CONTENT:
This is a dry run for topic: {topic_entry['topic']}.
Angle: {topic_entry['angle']}
Audience: {audience_label}

This draft is intentionally shortened for dry-run verification only. In live mode, the article will be framed for the most relevant audience segment and written as a Switzerland-focused immigration law article.

{CTA_HEADING}
For tailored legal advice, contact {CTA_NAME} by telephone on {CTA_PHONE} or by completing an enquiry form to arrange an initial consultation meeting.
""".strip()
else:
    OPENAI_API_KEY = require_env("OPENAI_API_KEY")
    content = generate_blog_content(OPENAI_API_KEY, chat_payload)

# -----------------------
# Robust section extractor
# -----------------------

def extract(section, until_next=True):
    start = content.find(section)
    if start == -1:
        return ""
    start += len(section)

    if until_next:
        end = content.find("\n\n", start)
        return content[start:end].strip() if end != -1 else content[start:].strip()
    return content[start:].strip()


title = extract("BLOG TITLE:")
dynamic_page_link = extract("DYNAMIC PAGE LINK:")
meta_title = extract("SEO META TITLE:")[:60]
meta_description = extract("SEO META DESCRIPTION:")[:155]
suggested_seo_keywords = extract("SUGGESTED SEO KEYWORDS:")
body = extract("BLOG CONTENT:", until_next=False)

if not title:
    title = topic_entry.get("topic", "Untitled Swiss immigration blog draft")
if not meta_title:
    meta_title = title[:60]
if not meta_description:
    meta_description = f"Swiss immigration law guidance on {title}".strip()[:155]
if not suggested_seo_keywords:
    suggested_seo_keywords = (
        "Swiss immigration lawyer; Swiss residence permit; move to Switzerland; "
        "Swiss work permit; Swiss visa application; Swiss immigration law"
    )

print("TITLE:", title)
print("AUDIENCE:", audience_label)
print("DYNAMIC PAGE LINK:", dynamic_page_link)
print("SEO META TITLE:", meta_title)
print("SEO META DESCRIPTION:", meta_description)
print("SUGGESTED SEO KEYWORDS:", suggested_seo_keywords)

# -----------------------
# Send draft email via SendGrid
# -----------------------

SENDGRID_API_KEY = require_env("SENDGRID_API_KEY") if not args.dry_run else None

email_payload = {
    "personalizations": [
        {"to": [{"email": EMAIL_TO}], "subject": f"Blog draft [{audience_label}]: {title}"}
    ],
    "from": {"email": EMAIL_FROM},
    "reply_to": {"email": REPLY_TO},
    "content": [
        {
            "type": "text/plain",
            "value": f"""TOPIC BACKLOG:
{remaining_count - 1} topics remaining

AUDIENCE FOCUS:
{audience_label}

BLOG TITLE:
{title}

DYNAMIC PAGE LINK:
{dynamic_page_link}

SEO META TITLE:
{meta_title}

SEO META DESCRIPTION:
{meta_description}

SUGGESTED SEO KEYWORDS:
{suggested_seo_keywords}

---------------------------------

BLOG CONTENT:

{body}
""",
        }
    ],
}

if args.dry_run:
    print("Dry run complete: skipped SendGrid email and topics.json update.")
else:
    sent = try_sendgrid_email(email_payload, SENDGRID_API_KEY, f"draft '{title}'")

    if sent:
        topics[topic_index]["status"] = "used"
        topics[topic_index]["used_title"] = title
        topics[topic_index]["used_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with open(TOPICS_PATH, "w", encoding="utf-8") as f:
            json.dump(topics, f, indent=2, ensure_ascii=False)

        print("Draft email sent successfully via SendGrid.")
    else:
        print("Draft generated, but email was not delivered (non-fatal). Topic remains unused.")
