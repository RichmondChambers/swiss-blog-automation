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

# Load topics
with open("topics.json", "r") as f:
    topics = json.load(f)

topic = next(t for t in topics if t["status"] == "unused")
topic["status"] = "used"

with open("topics.json", "w") as f:
    json.dump(topics, f, indent=2)

response = client.chat.completions.create(
    model="gpt-4.1",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Topic: {topic['topic']}\nAngle: {topic['angle']}"}
    ]
)

content = response.choices[0].message.content.strip()
title, body = content.split("\n", 1)

print("TITLE:", title)
print(body)

import subprocess
import os

subprocess.run(["git", "config", "user.name", "blog-bot"])
subprocess.run(["git", "config", "user.email", "bot@richmondchambers.com"])

repo = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"

subprocess.run(["git", "add", "topics.json"])
subprocess.run(["git", "commit", "-m", "Mark topic as used"], check=False)
subprocess.run(["git", "push", repo, "HEAD:main"])

