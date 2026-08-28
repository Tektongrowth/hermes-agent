---
name: schedule-crew-planning
description: "Answer current schedule and crew questions from live CJS records."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# Schedule and Crew Planning

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Confirm the requested company and date or date range.
2. Pull live schedules and crew data before answering.
3. Report date, start and end time, work area, assigned crew, and crew count when available.
4. Flag overlapping assignments and missing crew details plainly.
5. Do not infer an employee assignment from a prior day or a job note.
6. Keep planning suggestions separate from verified scheduled facts.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
