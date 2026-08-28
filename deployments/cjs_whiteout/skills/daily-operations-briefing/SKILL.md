---
name: daily-operations-briefing
description: "Build a short live daily brief for CJS and Whiteout without guessing or mixing companies."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# Daily Operations Briefing

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Confirm whether the request covers CJS, Whiteout, or both.
2. Pull today's schedules, crews, work areas, reminders, and urgent read-only inbox items from approved live tools.
3. Keep each company in its own section.
4. Lead with conflicts, missing assignments, blocked jobs, and time-sensitive messages.
5. State when a source returned no records. Do not convert a failed lookup into an empty result.
6. Keep the final brief short and action-oriented.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
