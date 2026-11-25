#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != true || "${GITHUB_REF:-}" != refs/heads/main ]]; then
  echo "Remote topic-state updates require the main GitHub Actions workflow." >&2
  exit 1
fi

git add -- topics.json
topic_git=(git -c user.name=blog-bot -c user.email=bot@richmondchambers.com)
"${topic_git[@]}" commit --only -m "$1" -- topics.json

for attempt in 1 2 3; do
  if git fetch origin refs/heads/main; then
    if ! "${topic_git[@]}" rebase FETCH_HEAD; then
      git rebase --abort
      echo "Topic state conflicts with main. Reconcile topics.json before retrying." >&2
      exit 1
    fi
    if git push origin HEAD:refs/heads/main; then
      exit 0
    fi
  fi
  if (( attempt < 3 )); then
    sleep "$attempt"
  fi
done

echo "Could not confirm that topic state was saved to main." >&2
exit 1
