#!/usr/bin/env bash
#
# setup-branch-protection.sh — apply main-branch protection + squash-only merge.
#
# Idempotent: the protection PUT sets the complete state, so re-running re-asserts
# it. RUN AFTER #31 (the `lint` CI job) has merged to main — otherwise the required
# `lint` check never runs and all PRs block. Confirm the exact check names from a
# real run first (see the comment by `contexts`).
#
set -euo pipefail

REPO="0xBahalaNa/oscal-evidence-pipeline"
BRANCH="main"

echo "Applying branch protection to ${REPO}@${BRANCH}…"

# --input - reads the JSON body from the heredoc on stdin. We use the body form
# (not -F flags) because the endpoint requires required_pull_request_reviews and
# restrictions to be PRESENT but null — and -F cannot send a real JSON null.
#
# contexts: a GitHub Actions status-check context is the JOB name. Our jobs are
# `test` and `lint`, so these should match — but confirm against an actual run:
#   gh api repos/${REPO}/commits/main/check-runs --jq '.check_runs[].name'
# If they come back prefixed (e.g. "CI / test"), use those exact strings instead.
gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["test", "lint"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON

echo "Tightening merge policy to squash-only…"
gh repo edit "${REPO}" \
  --enable-merge-commit=false \
  --enable-rebase-merge=false \
  --enable-squash-merge=true \
  --delete-branch-on-merge=true

echo "Done. Effective protection:"
gh api "repos/${REPO}/branches/${BRANCH}/protection" \
  --jq '{required_checks: .required_status_checks.contexts,
         strict: .required_status_checks.strict,
         enforce_admins: .enforce_admins.enabled}'
