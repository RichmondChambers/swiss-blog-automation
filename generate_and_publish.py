import json
import os
import requests
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_PATH = os.path.join(SCRIPT_DIR, "topics.json")

# Load topics
with open(TOPICS_PATH, "r") as f:
    topics = json.load(f)

try:
    topic = next(t for t in topics if t["status"] == "unused")
except StopIteration as exc:
    raise RuntimeError("No unused topics available in topics.json.") from exc

response = client.chat.completions.create(
    model="gpt-4.1",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Topic: {topic['topic']}\nAngle: {topic['angle']}"}
    ]
)

content = response.choices[0].message.content.strip()
if "\n" in content:
    title, body = content.split("\n", 1)
else:
    title, body = content, ""

print("TITLE:", title)
print(body)

topic["status"] = "used"

with open(TOPICS_PATH, "w") as f:
    json.dump(topics, f, indent=2)

import subprocess
import os

subprocess.run(["git", "config", "user.name", "blog-bot"])
subprocess.run(["git", "config", "user.email", "bot@richmondchambers.com"])

repo = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"

subprocess.run(["git", "add", TOPICS_PATH])
subprocess.run(["git", "commit", "-m", "Mark topic as used"], check=False)
subprocess.run(["git", "push", repo, "HEAD:main"])
