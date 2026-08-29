---
name: snow-material-contract-operations
description: "Track salt, trucking, brine, calcium chloride, snow contracts, service evidence, and billing readiness with auditable records."
version: 1.0.0
metadata:
  hermes:
    tags: [whiteout, mason, snow, materials, contracts]
---

# Snow Materials, Contracts, and Billing

Use only inside the CJS Landscape and Whiteout tenant. Keep Whiteout records separate from CJS unless Alyssa explicitly asks for a combined internal summary. A skill never grants access by itself.

## Calcium chloride and brine

- Use a batch or movement ledger as the source. Chat history is not inventory.
- Preserve date and time, batch ID, operator, brine volume, calcium chloride quantity and source unit, concentration or test reading, tank or truck, route or job, remaining quantity, and adjustment or waste note when available.
- Never convert units or calculate concentration without verified units and an approved formula.
- Never create a negative balance. Flag missing or conflicting movements.
- Corrections require adjustment records. Do not erase prior events.

## Salt loads, trucking, and brine movements

Keep supplier receipt, internal transfer, brine production, truck load, route application, return, waste, and adjustment as separate event types. Preserve supplier, ticket, carrier, truck, gross or tare or net amount, storage location, driver, route or job, timestamp, and evidence attachment when available.

For reconciliation:
1. Start from the last verified opening balance.
2. Sum each movement type in its source unit.
3. Do not mix salt weight, liquid volume, and chemical quantity.
4. Report missing tickets, unmatched trucking, duplicate candidates, and physical-count variance.
5. Do not connect an event to job costing or billing unless the source and approved rule support it.

## Snow contracts and billing readiness

1. Read the signed contract version and effective dates before applying a pricing rule.
2. Preserve customer, property, service areas, pricing method, triggers, tolerances, included and excluded services, billing schedule, and amendments.
3. Separate contract terms, service events, and invoices.
4. For billing readiness, show the service event, evidence, exact contract rule, calculated basis, missing proof, exceptions, and review state.
5. Do not generate, change, approve, or send an invoice without exact approval.
6. If more than one contract version or pricing method could apply, stop and show the conflict.
7. During replacement of the snow spreadsheet, compare the new result with the existing sheet and explain every difference. Do not recommend retiring the sheet until verified billing cycles reconcile.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
