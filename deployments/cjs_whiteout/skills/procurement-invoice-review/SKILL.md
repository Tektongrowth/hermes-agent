---
name: procurement-invoice-review
description: "Prepare sold-job plant and material orders, review emailed invoices, and suggest verified project matches without sending or silently filing."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, procurement, invoices]
---

# Procurement and Invoice Review

Use only inside the CJS Landscape and Whiteout tenant. A skill never grants access by itself.

## Sold-job plant and material requests

1. Resolve the exact SynkedUP job and verify the source says it is sold. Active, scheduled, proposed, or discussed is not sold unless the live record explicitly proves it.
2. Read the approved estimate, proposal, work areas, items, materials, and approved change orders needed for the request.
3. For plants, preserve source name, cultivar or species, size, quantity, work area, and delivery timing. For materials, preserve item, quantity, source unit, work area, and timing.
4. Never convert units, combine uncertain variants, choose substitutions, assign a vendor, or change estimated quantity into order quantity without an approved business rule or live source.
5. Group by nursery or vendor only when the mapping is verified. Put unassigned lines under `Vendor not verified`.
6. Reconcile every output line to a source line before drafting.
7. Draft only. Show mailbox or sender, To/CC, subject, full body, attachments, job references, missing fields, and the consequence. Never send or place an order without exact approval.

## Emailed invoice review

1. Search the approved Outlook mailbox read-only with `composio_query_outlook_emails`, using `subject_contains: invoice` when appropriate. Never invent an Outlook search slug or try to read a server-side saved-result path. Read each candidate with `composio_read_outlook_email`, then inspect invoice attachments through approved read tools.
2. Extract only visible evidence: vendor, invoice number, invoice date, amount, PO or job reference, customer, address, and line-item summary.
3. Check duplicates by vendor, invoice number, amount, source message, and attachment identity when those fields are available.
4. Search SynkedUP and Drive for exact job numbers, customer names, property addresses, and verified project folders.
5. Report `Suggested match`, `Needs review`, `Duplicate candidate`, or `Not job related`. Give the matching evidence. Never silently allocate an invoice.
6. A missing or ambiguous match stays unassigned. Do not infer from a vendor's usual work, employee role, or nearby project.
7. For weekly review, list new items, suggested matches, ambiguous items, duplicate candidates, and missing documents. Do not create folders, copy files, edit documents, or change email state during a read-only review.
8. Before an approved filing action, verify the exact destination folder, proposed filename, source attachment, and duplicate check. Require confirmation. Preserve the source file unchanged and verify the copied file by readback.

## Juli requirements

When Alyssa asks what Juli needs regularly, first search available source messages and existing instructions. If the required fields, cadence, naming, or accounting handoff are not documented, say which decisions are missing. Do not invent Juli's requirements.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
