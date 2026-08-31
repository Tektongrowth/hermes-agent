---
name: job-cost-project-review
description: "Reconcile completed SynkedUP jobs against the Project Review Sheet."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# Job Cost and Project Review

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Call synkedup_job_costing with exactly status:completed for completed dashboard jobs.
2. Find the exact 2026 Project Review Sheet in CJS Drive. For `GOOGLEDRIVE_FIND_FILE`, use `q: "name contains 'Project Review Sheet' and trashed = false"`. Never use `title`; Google Drive's filename field is `name`, and Composio may convert invalid fields into a literal full-text search that returns no files.
3. Read matching rows by passing every completed SynkedUP job number to composio_read_drive_spreadsheet.
4. Compare Estimated Hours, Actual Hours, Final Net Profit %, Final Net Profit $, and Final Total in that order.
5. Normalize equivalent numeric formatting but report every real numeric mismatch, including zero and small decimal differences.
6. Build the complete normalized mismatch list first. Derive the mismatch count from that final list, then reconcile completed = matched + missing + mismatched before answering. Never state a count that differs from the number of jobs actually listed.
7. Never perform the comparison from remembered or cached chat text.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
