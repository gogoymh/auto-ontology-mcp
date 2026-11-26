# Agent notes

## Reports in Korean

Before you post a completion report (mission_finish summary and next, the comment when an issue moves to review, a result message in a channel), follow the "완료 보고 전 점검" section of `.claude/skills/polish/SKILL.md`:

1. Run `python3 scripts/lint_report.py <draft>` (or pipe the draft on stdin).
2. Rewrite every flagged line with the matching pattern in the skill until it prints `ok: no polish patterns found`.
3. Check that numbers, file names, ticket numbers and commands did not change.

`scripts/polish_hook.py` runs the same check as a PreToolUse hook on mission_finish, issue_update (comment) and message_post (text), and blocks the call while a pattern is left.
