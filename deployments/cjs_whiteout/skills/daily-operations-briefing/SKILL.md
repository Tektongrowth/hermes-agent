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

## Daily Outlook hit list

1. Search both approved Outlook mailboxes for the requested business day and keep CJS Landscape and Whiteout in separate sections.
2. Read each candidate message body with `composio_read_outlook_email`. A subject line or snippet alone does not prove urgency, ownership, or an open action.
3. Identify explicit requests, deadlines, blockers, customer changes, vendor issues, invoice reviews, and unresolved commitments. Put an item under `Urgent` only when the body proves a deadline, active delay, safety or damage issue, customer problem requiring timely response, or immediate operational impact. A platform label such as `Action required`, a new application, or a normal request is not urgent by itself.
4. Check later replies in the same thread before calling an item open. A draft, missing message, or silence does not prove resolution.
5. Carry an open item forward until a later source explicitly resolves it. Do not resolve it because a later message omits it.
6. Name an owner only when a source assigns responsibility for the next action. A recipient, sender, original requester, or person mentioned in a forwarded thread is not automatically the owner. Otherwise say only `Owner not specified`. Never add who would typically handle it.
7. Produce `Urgent`, `Tomorrow`, `Waiting on someone`, `Needs Alyssa's decision`, and `Information only`. Omit empty sections.
8. Include mailbox, sender, subject, received time, body-grounded reason, next verified action, and a safe source reference.
9. In read-only mode, do not send email, change mailbox state, create tasks, reminders, folders, documents, checklists, contacts, projects, or other records.
10. Draft responses only when requested. A draft is not approval to send.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
