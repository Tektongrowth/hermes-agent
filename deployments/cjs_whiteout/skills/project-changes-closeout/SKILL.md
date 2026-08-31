---
name: project-changes-closeout
description: "Control design revisions, added work, material ordering stages, final walkthroughs, and closeout without treating requests as approval."
version: 1.0.0
metadata:
  hermes:
    tags: [cjs, mason, projects, change-orders, closeout]
---

# Project Changes, Design, and Closeout

Use only inside the CJS Landscape and Whiteout tenant. Follow Mason's permissions and approval gates. A skill never grants access by itself.

## Added work and contract or design changes

1. Resolve the exact customer, property, job, and current sold scope from live sources.
2. Preserve the homeowner's exact request, source message, work area, date, photos, measurements, and requested timing.
3. Treat every new request as `Pending review` until a live source proves scope, price, and customer approval. Never call it approved, sold, scheduled, ordered, completed, or billable based only on the request.
4. Keep one change ID across the request, design revision, pricing, approval, SynkedUP record, and Drive documents.
5. Preserve prior contract and design versions. Do not overwrite or relabel old versions as current.
6. Separate design impact, materials impact, labor or schedule impact, pricing status, customer approval, completion evidence, and billing readiness.
7. Require exact approval before changing a contract, design, sold scope, change order, invoice, billing status, or customer-facing document.

## Design and material-order pipeline

Use these stages only when the business has adopted them: New Request, Site Information Needed, Site Visit Scheduled, Design in Progress, Internal Review, Customer Review, Revision Requested, Approved, Materials Ready to Order, Ordered, Confirmed, Scheduled for Delivery, Closed.

- A stage needs source evidence. Do not advance it from conversational implication.
- Name an owner and due date only when a source assigns them.
- Material ordering starts only from approved design and sold scope.
- Revised designs must be checked for stale material lists or prior orders.
- Show aging, blockers, missing decisions, and the next verified action.

## Final walkthrough

1. Pull work areas from the final sold scope plus approved change orders.
2. Build one checklist line per verified work area with scope summary, completion state, issue note, photo reference, and acknowledgment state.
3. Keep incomplete, disputed, or unavailable evidence visible. Missing evidence is not proof of failure or completion.
4. A signature needs signer identity, timestamp, and document version.
5. Add a review QR code only when the exact official Google review URL is verified. Never substitute a search-results URL.
6. Store or link a signed walkthrough only after confirmation, then verify the live file and exact project destination.

### Setting up Alyssa's reusable walkthrough workflow

When Alyssa asks to set up walkthrough sheets for future sold projects, treat that as a workflow setup request, not casual planning.

1. Check whether the sign-off template and exact official Google review URL are already available in the current message or approved Drive sources.
2. If either is missing, ask only for the missing item. Do not say the workflow is set up, do not ask her to repeat the whole request, and do not ask for a sold job number until the reusable template is ready.
3. Once both are available, create two reusable versions from the same approved template: one with the verified review QR code and one without it. Preserve the original template.
4. Verify both created files by reading them back. Report their exact names and Drive location. Creating or storing these files requires the normal confirmation gate when applicable.
5. After setup, each project run starts from one exact sold job number. Pull the final sold scope plus approved change orders, populate one line per verified work area, and produce the two project-specific print choices.
6. Never respond only with praise, a restatement, or a promise to help later. Either complete the setup with the available inputs or ask for the smallest missing prerequisite.

## Final billing reconciliation

Before saying a job is ready for final billing, check every change ID. Separate requested, awaiting scope, awaiting approval, approved, scheduled, completed, ready to bill, billed, rejected, and canceled. Do not treat revenue as approved merely because work appears in a note.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
