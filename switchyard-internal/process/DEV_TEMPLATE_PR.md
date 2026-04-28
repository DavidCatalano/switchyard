# Pull Request (PR) Template

**Title**: [Short descriptive name for the feature or fix]
**ID**: SEP-###-0#-PR-[feature-name] (04 or next available doc index for SEP-###)
**Status**: Draft (final PR in Github)
**Date**: YYYY-MM-DD

---

# Squash Commit Message

> All commits in the branch will be squashed. Generate a conventional commit that spans the work



# Draft PR

> Post the content to GH when making the pull request 

---

## 1. Summary

- What this PR implements (high-level)
- Why it matters (single concise paragraph)

## 2. Scope of Changes

- Code modules updated
- Tests added/modified
- Fixtures or schemas updated
- Documentation updates (Implementation Guide, DEV_DETAILS, README)

## 3. Validation

-

## 4. Risks / Considerations

- Backward compatibility notes
- Known limitations (if any)
- Deployment or migration concerns

## 5. Reviewer Checklist

-

---

### Usage Notes

- Title and ID should match the naming convention for traceability.
- This document is the canonical draft for the GitHub PR body (`gh pr create --body-file` or copy/paste).
- Keep PR concise and reviewer-focused: summarize changes, validation, and risks only.
- Do not link or reference PRD/PLAN in the PR body.
- Reviewers use the checklist to ensure completeness before merge.
- No AI Coding Agent attribution in footer
