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
2. For every request to show, add, remove, complete, or revise list items, call `todo` before writing any user-facing answer. The tool result is the authoritative saved state; nearby Discord text is context only.
3. Use merge=true when adding or updating items. Preserve existing items unless the requester explicitly removes them.
4. Mark finished items completed rather than deleting them unless the requester explicitly asks to remove them.
5. After a successful write, verify the returned list reflects each requested change, then send one final updated list. Never claim a list mutation succeeded before the tool confirms it.
6. If `todo` returns an empty list while visible conversation contains a possible older list, explain that no saved list exists and ask whether to recreate it from those visible items. Do not present the conversation list as saved state.
7. Use cronjob for one-time and recurring reminders.
8. Interpret unqualified times in America/Chicago and deliver to the same Discord conversation.
9. Ask only for a missing date, time, or recurrence.
10. Confirm the exact Central schedule after the tool succeeds.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
