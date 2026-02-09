import json
import os
import requests
from openai import OpenAI

# -----------------------
# GPT system prompt
# -----------------------

SYSTEM_PROMPT = """
You are a senior content writer producing authoritative blog posts for a professional audience.

Writing requirements:
- 900–1,100 words
- UK English
- Authoritative, analytical tone
- No clichés
- No emojis
- No sales language

Structure:
- Clear blog title
- Short introduction
- 3–5 headed sections
- Practical conclusion

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
# Load topics.json
# -----------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_PATH = os.path.join(SCRIPT_DIR, "topics.json")

with open(TOPICS_PATH, "r", encoding="utf-8") as f:
    topics = json.load(f)

for index, topic in enumerate(topics):
    if topic.get("status") == "unused":
        topic_index = index
        topic_entry = topic
        break
else:
    raise RuntimeError("No unused topics available in topics.json")

# -----------------------
# Generate blog post
# -----------------------

response = client.chat.completions.create(
    model="gpt-4.1",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Topic: {topic_entry['topic']}\nAngle: {topic_entry['angle']}",
        },
    ],
)

content = response.choices[0].message.content.strip()

def extract(section):
    start = content.find(section)
    if start == -1:
        return ""
    start += len(section)
    end = content.find("\n\n", start)
    return content[start:end].strip() if end != -1 else content[start:].strip()

title = extract("BLOG TITLE:")
meta_title = extract("SEO META TITLE:")[:60]
meta_description = extract("SEO META DESCRIPTION:")[:155]
body = extract("BLOG CONTENT:")

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
# Send email via SendGrid
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
            "value": f"""BLOG TITLE:
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
