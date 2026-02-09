import json
import os
from openai import OpenAI

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
- Clear title
- Short introduction
- 3–5 headed sections
- Practical conclusion
"""

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Always resolve topics.json relative to this file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_PATH = os.path.join(SCRIPT_DIR, "topics.json")

# Load topics
with open(TOPICS_PATH, "r", encoding="utf-8") as f:
    topics = json.load(f)

# Find first unused topic
for index, topic in enumerate(topics):
    if topic.get("status") == "unused":
        topic_index = index
        topic_entry = topic
        break
else:
    raise RuntimeError("No unused topics available in topics.json")

# Generate blog post
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

if "\n" in content:
    title, body = content.split("\n", 1)
else:
    title, body = content, ""

print("TITLE:", title)
print(body)

# Mark topic as used
topics[topic_index]["status"] = "used"
topics[topic_index]["used_title"] = title

# Write back to topics.json
with open(TOPICS_PATH, "w", encoding="utf-8") as f:
    json.dump(topics, f, indent=2, ensure_ascii=False)

