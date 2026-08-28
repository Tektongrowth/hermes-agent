---
name: hit-lists-reminders
description: "Maintain named checklists and Central Time reminders in the requesting Discord conversation."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# Hit Lists, Checklists, and Reminders

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Use todo to create a complete named list.
2. Use merge=true when adding or updating items. Preserve existing items unless the requester explicitly removes them.
3. Mark finished items completed rather than deleting them.
4. Use cronjob for one-time and recurring reminders.
5. Interpret unqualified times in America/Chicago and deliver to the same Discord conversation.
6. Ask only for a missing date, time, or recurrence.
7. Confirm the exact Central schedule after the tool succeeds.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
