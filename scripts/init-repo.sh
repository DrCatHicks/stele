#!/usr/bin/env bash
# One-shot setup: create the private repo on GitHub, push initial commit,
# and configure branch protection.
#
# Prerequisites:
#   - gh CLI installed and authenticated (`gh auth login`)
#   - Run from inside the repo directory after files are scaffolded
#
# Usage:  ./scripts/init-repo.sh <repo-name>

set -euo pipefail

REPO_NAME="${1:?Usage: $0 <repo-name>}"

# 1. Initialize git
git init -b main
git add .
git commit -m "Initial scaffold: CI, Claude review, CodeQL, pre-commit"

# 2. Create the private repo and push
gh repo create "$REPO_NAME" --private --source=. --remote=origin --push

# 3. Branch protection on main.
# Requires CI jobs to have run at least once before they can be required —
# if this errors, push a no-op PR first, wait for checks to run, then re-run.
gh api -X PUT "repos/{owner}/$REPO_NAME/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Python · lint (ruff)",
      "Python · typecheck (mypy)",
      "Python · test (pytest)",
      "TypeScript · lint (eslint + prettier)",
      "TypeScript · typecheck (tsc)",
      "TypeScript · test (vitest)",
      "TypeScript · build",
      "dbt · parse & compile"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo
echo "Repo created and protected: https://github.com/$(gh api user --jq .login)/$REPO_NAME"
echo
echo "Next steps:"
echo "  1. gh secret set ANTHROPIC_API_KEY    # paste your key when prompted"
echo "  2. Edit .github/CODEOWNERS to replace @your-username"
echo "  3. Enable CodeQL in Settings → Code security and analysis"
