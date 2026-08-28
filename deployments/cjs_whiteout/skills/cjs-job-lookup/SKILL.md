---
name: cjs-job-lookup
description: "Resolve a CJS customer or job across SynkedUP, Drive, and CJS Outlook."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# CJS Job Lookup

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Resolve the exact customer, property, job number, or date range before broad searches.
2. Use SynkedUP as the source of truth for current job status, schedule, crews, labor, estimates, notes, and financial fields.
3. Use CJS Drive for plans, PDFs, Sheets, photos, and project folders.
4. Use only the CJS Outlook connection for CJS correspondence.
5. Match records by verified job number or exact customer/property identity.
6. Combine only verified fields and name any unresolved mismatch.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
