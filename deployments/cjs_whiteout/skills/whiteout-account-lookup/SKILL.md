---
name: whiteout-account-lookup
description: "Find Whiteout records without leaking or mixing CJS information."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# Whiteout Account Lookup

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Pin all email work to the Whiteout Outlook connection.
2. Keep Whiteout findings separate from CJS findings even when customers or staff overlap.
3. Search by exact account, property, sender, subject, or date range.
4. Outlook remains read-only. Never send, reply, move, mark, archive, categorize, or delete.
5. If a requested Whiteout system is not connected, report the missing connection as a training blocker. Do not substitute CJS data.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
