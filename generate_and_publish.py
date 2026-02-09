import json
import os
import requests
from openai import OpenAI
from PyPDF2 import PdfReader

# -----------------------
# GPT system prompt
# -----------------------

SYSTEM_PROMPT = """
You are a senior blog post writer producing authoritative, legally accurate blog posts for Richmond Chambers Switzerland, an immigration law firm specialising exclusively in Swiss immigration law and Swiss immigration routes.
Your role is to write in-depth, analytical blog posts aimed at educated, time-poor professionals seeking clear, reliable guidance on Swiss immigration options. Readers may be based in Switzerland or overseas and are looking for practical understanding rather than marketing content.
You must demonstrate strong subject-matter expertise in Swiss immigration law, including residence permits, work permits, family reunification, business immigration, EU/EFTA and non-EU nationals, and related regulatory frameworks. All content must be legally accurate. You must not speculate, invent rules, or hallucinate legal positions. Where necessary, you may supplement your knowledge with careful web research to ensure accuracy and currency.

Writing style and tone:

- UK English
- Authoritative, analytical, and calm
- Professional and non-promotional
- Clear, precise prose aimed at a professional audience
- No clichés
- No emojis
- No sales language
- No references to yourself as an AI

Content requirements:

- Length: typically 1,000–1,500 words per post (around 1,500 words unless the topic clearly requires less)
- Use prose throughout. Maximum of 2 bullet point lists, numbered lists or hyphenated lists etc.
- Concrete claims rather than vague generalities
- Clear explanations of legal reasoning and practical consequences
- Examples where they genuinely aid understanding
- Avoid generic summaries or filler content

Search optimisation:

- Optimise content for search engines using relevant keywords and keyword variations related to Swiss immigration law and Swiss immigration routes
- Keywords must be integrated naturally and appropriately, without keyword stuffing

Structure:

- A compelling, specific title that clearly reflects the subject matter
- A concise introduction that frames the legal or practical problem being addressed, without fluff
- At least five substantive sections, each with clear, descriptive headings that signal the legal or practical issue discussed
- A practical conclusion that distils key takeaways and implications for readers
- A final section with the sub-heading: Contact Our Immigration Lawyers In Switzerland

Call to action requirement:

Under the sub-heading “Contact Our Immigration Lawyers In Switzerland”, include a short, measured call to action

The CTA must be relevant to the subject matter of the blog post and framed as an invitation to obtain tailored legal advice

It must invite readers to contact Richmond Chambers Switzerland by telephone on +41 21 588 07 70 or by completing an enquiry form to arrange an initial consultation meeting

The CTA must remain professional, factual, and restrained, avoiding promotional or sales-oriented language

Output format:

- Plain text only
- Headings clearly marked
- No markdown
- No citations or footnotes unless explicitly requested
- No meta-commentary about the writing process

Your objective is to produce content that would be credible, useful, and reassuring to a professional reader seeking authoritative guidance on Swiss immigration law and Swiss immigration routes.

SEO requirements:
- Generate an SEO meta title (max 60 characters)
- Generate an SEO meta description (max 155 characters)
- Meta text must be natural, accurate, and non-promotional

Output format EXACTLY as follows:

BLOG TITLE:
<text>

SEO META TITLE:
<text>

SEO META DESCRIPTION:
<text>

BLOG CONTENT:
<full article>
"""

# -----------------------
# OpenAI client
# -----------------------

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# -----------------------
# Load authoritative PDF knowledge
# -----------------------

def load_pdf_knowledge(folder="knowledge", max_chars=12000):
    texts = []

    if not os.path.isdir(folder):
        return ""

    for filename in sorted(os.listdir(folder)):
        if not filename.lower().endswith(".pdf"):
            continue

        path = os.path.join(folder, filename)
        reader = PdfReader(path)

        pdf_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pdf_text.append(text)

        combined = "\n".join(pdf_text)
        combined = " ".join(combined.split())

        if combined:
            texts.append(f"[SOURCE: {filename}]\n{combined}")

    full_text = "\n\n".join(texts)

    return full_text[:max_chars]

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
    SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
    EMAIL_FROM = os.environ["EMAIL_FROM"]
    EMAIL_TO = os.environ["EMAIL_TO"]

    notification_payload = {
        "personalizations": [
            {
                "to": [{"email": EMAIL_TO}],
                "subject": "Blog automation: topics exhausted",
            }
        ],
        "from": {"email": EMAIL_FROM},
        "content": [
            {
                "type": "text/plain",
                "value": (
                    "All blog topics in topics.json have been used.\n\n"
                    "No draft was generated on this run.\n\n"
                    "Please add new topics with status \"unused\" "
                    "and the automation will resume automatically."
                ),
            }
        ],
    }

    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        json=notification_payload,
    )

    response.raise_for_status()
    print("Topics exhausted notification sent.")
    exit(0)

# -----------------------
# Select next unused topic
# -----------------------

for index, topic in enumerate(topics):
    if topic.get("status") == "unused":
        topic_index = index
        topic_entry = topic
        break

# -----------------------
# Generate blog post
# -----------------------

response = client.chat.completions.create(
    model="gpt-4.1",
    messages=[
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
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
"""
        },
        {
            "role": "user",
            "content": f"Topic: {topic_entry['topic']}\nAngle: {topic_entry['angle']}",
        },
    ],
)

content = response.choices[0].message.content.strip()

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
    else:
        return content[start:].strip()

title = extract("BLOG TITLE:")
meta_title = extract("SEO META TITLE:")[:60]
meta_description = extract("SEO META DESCRIPTION:")[:155]
body = extract("BLOG CONTENT:", until_next=False)

print("TITLE:", title)
print("SEO META TITLE:", meta_title)
print("SEO META DESCRIPTION:", meta_description)

# -----------------------
# Mark topic as used
# -----------------------

topics[topic_index]["status"] = "used"
topics[topic_index]["used_title"] = title

with open(TOPICS_PATH, "w", encoding="utf-8") as f:
    json.dump(topics, f, indent=2, ensure_ascii=False)

# -----------------------
# Send draft email via SendGrid
# -----------------------

SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_TO = os.environ["EMAIL_TO"]

email_payload = {
    "personalizations": [
        {
            "to": [{"email": EMAIL_TO}],
            "subject": f"Blog draft: {title}",
        }
    ],
    "from": {"email": EMAIL_FROM},
    "content": [
        {
            "type": "text/plain",
            "value": f"""TOPIC BACKLOG:
{remaining_count - 1} topics remaining

BLOG TITLE:
{title}

SEO META TITLE:
{meta_title}

SEO META DESCRIPTION:
{meta_description}

---------------------------------

BLOG CONTENT:

{body}
""",
        }
    ],
}

response = requests.post(
    "https://api.sendgrid.com/v3/mail/send",
    headers={
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    },
    json=email_payload,
)

response.raise_for_status()

print("Draft email sent successfully via SendGrid.")
