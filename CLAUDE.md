# Teachable Sales Intelligence - Claude Code Instructions

## Project Reference

Read `PROJECT_STATUS.md` at the start of every session for current project state.

## Working Directory

Always work in the repo root (the directory containing `server.py` and `dashboard_template.html`). Never use temp directories.

## Session Hygiene

Before ending any session or when prompted with "update status":
1. Update `PROJECT_STATUS.md` with what changed
2. Move completed items from "In Progress" to "What's Working"
3. Add any new issues to "Known Issues"
4. Update the "Last updated" date and "Updated by" field
5. Commit the updated file

## Analysis Workflow

### Analysis Workflow Protocol

When asked to run analysis on new/pending calls:

**Step 0 — Triage**
Read pending calls from `test_output/index.html` (the DATA object). Classify each pending/unanalyzed call:

- **Auto-junk** (under 100 words): Empty or failed recordings. Mark as junk — set `pending_analysis: false`, `analyzed: true`, `is_junk: true`. Do not extract features, marketing_data, or segment_data.
- **Auto-analyze** (300+ words, title matches a prospect pattern): Clearly prospect calls regardless of detected speakers. Fireflies sometimes misattributes prospect speakers as internal names. Recognized title patterns:
  - `Teachable <> Company Name` (standard format)
  - `Teachable Followup` / `Teachable Reconnect` (follow-up format)
  - `Connect with Teachable` (inbound format)
  - `Person Name and Rep Name` (person-name format, e.g. "Casey Caston and Zach McCall" — title contains an internal rep name alongside a non-internal name)
- **Flag as suspicious but still analyze** (300+ words, all detected speakers are internal, but title doesn't clearly indicate internal-only): Show a warning like `(⚠ speakers look internal, verify)`. Err on the side of analyzing — a wasted analysis on an internal call is cheap, missing a prospect call is expensive.
- **Review zone** (100-300 words): Flag for manual check with `(⚠ short transcript, review)`. Still put in ANALYZE list.

Only auto-junk when it's clearly empty (under 100 words). Everything else goes to ANALYZE with appropriate flags.

Report the triage results before proceeding. Example:
Triage complete:

Analyze: [4] Unite Health (4649w), [5] Pure Life Ministries (4732w, ⚠ speakers look internal, verify), ...
Junk/skip: [1] (0w), [7] (5w), ...


Wait for confirmation before analyzing.

**Step 1 — Analyze (one call at a time)**
For each analyzable call, in order:
1. Read the full transcript from the dashboard data
2. Apply the analysis prompt from `analyze_features.py extract` (categories from `categories.json`, segments from `segments.json`, competitors from `competitors.json`)
3. Extract: features, segment_data, competitor_mentions, marketing_data, notes (HubSpot format)
4. Follow ALL rules in the extract prompt: prospect speakers only, never Teachable employees, exact canonical names for categories/segments/competitors, NEEDS_REVIEW for anything that doesn't fit
5. Write the single-call output to a temp JSON file
6. Run: `python3 analyze_features.py validate <output.json> --fix`
7. Run: `python3 analyze_features.py inject test_output/index.html <output.json>`
8. Confirm success and summarize what was extracted (feature count, segment, competitors found)
9. Move to the next call

**Step 2 — Post-analysis**
After all calls are processed:
- Report total: N calls analyzed, M features extracted, segments assigned, competitors found
- Run `python3 sync_to_sheets.py --dry-run` and report what would sync
- Update PROJECT_STATUS.md with new data counts

### Junk Call Handling
Junk calls should NOT remain as "pending" in the dashboard. They clutter the pending count and make it look like work is outstanding. Mark them as processed-but-empty so the dashboard shows accurate pending counts.

### Internal Speaker List (always exclude from feature extraction)
- Zach McCall (zach.mccall@teachable.com)
- Jerome Olaloye (jerome.olaloye@teachable.com)
- Kevin Codde (kevin.codde@teachable.com)
- Lennie Zhu
- Sarah Dean
- Jonathan Corvin-Blackburn
- Luke Easley
- Any @teachable.com email

### Word Count Threshold
- Under 100 words: auto-junk (empty or failed recording)
- 100-300 words: analyze but flag `(⚠ short transcript, review)`
- 300+ words: analyze (flag if all speakers look internal)
