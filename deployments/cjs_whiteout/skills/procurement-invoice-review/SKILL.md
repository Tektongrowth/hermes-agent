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

1. Enumerate the live SynkedUP `SOLD JOB REVENUE` panel with `synkedup_sold_jobs`. Do not search for literal strings such as `status:sold`, `status:won`, or `status:approved`; the generic job search treats those as text and can return a false empty result. Treat panel membership as the live sold-status source. When a project detail exposes a Status field, verify the source says it is sold there too. Active, scheduled, proposed, or discussed is not sold unless the live record explicitly proves it.
2. For each listed sold job, call `synkedup_sold_job_materials` with the exact job number and `page_size: 500`. Inspect `pagination.total_rows`, the actual returned row count, and `pagination.has_more`. The counts must match and `has_more` must be false before classifying that job. If either check fails, continue with `pagination.next_cursor`; never classify pagination totals as retrieved rows.
3. Build a source ledger before drafting. Give every retrieved row a stable sequence and preserve its job number, job name, work area, item name, estimated quantity, and full source unit exactly as returned. Classify each row as `material`, `explicit labor`, or `fee/uncertain`. Do not merge duplicate-looking rows across work areas and do not add quantities together. Reconcile the retrieved-row count to the classified-row count before drafting.
4. Exclude `explicit labor` rows from the vendor draft. Put every `fee/uncertain` row in a separate review section. If a source unit is blank, shortened, or contains an ellipsis, identify that exact row as unresolved instead of completing or guessing the unit.
5. For plants, preserve source name, cultivar or species, size, estimated quantity, source unit, work area, and delivery timing. For materials, preserve item, estimated quantity, source unit, work area, and timing.
6. Never convert units, combine uncertain variants, choose substitutions, assign a vendor, or change estimated quantity into order quantity without an approved business rule or live source.
7. Group by nursery or vendor only when the mapping is verified. Put unassigned lines under `Vendor not verified`.
8. Reconcile every output line to one source-ledger row before drafting. State the counts: retrieved, material, explicit labor excluded, fee/uncertain, and unresolved. These counts must reconcile.
9. Draft only. Show mailbox or sender, To/CC, subject, full body, attachments, job references, missing fields, and the consequence. Never invent a recipient, sender, vendor, mailbox, delivery date, attachment, or placeholder address. If the recipient or sender is not verified, write `To: Not verified` or `From: Not verified` and keep the draft unsent. Never send or place an order without exact approval.

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
