---
name: cjs-job-lookup
description: "Resolve a CJS customer or job across SynkedUP, Drive, and CJS Outlook."
version: 1.0.1
metadata:
  hermes:
    tags: [cjs, mason, operations]
---

# CJS Job Lookup

Use this skill only inside the CJS Landscape and Whiteout Winter Services tenant. Follow Mason's SOUL, current user permissions, read-only Outlook rule, and approval gates. A skill never grants access by itself.

## Workflow

1. Resolve the exact customer, property, job number, or date range before broad searches.
2. Use SynkedUP as the source of truth for current job status, schedule, crews, labor, estimates, notes, and financial fields.
3. Use CJS Drive for plans, PDFs, Sheets, photos, and project folders.
   - For project-folder or plan requests, search the surname and first name separately instead of requiring the customer name, year, and document type in one filename.
   - Inspect every plausible customer-folder match and trace its parent path. Prefer a match under `00 - Sold YYYY` over an empty exact-name folder elsewhere.
   - Do not stop at the first empty or partial match. Exhaust plausible CJS year and sold-folder matches, then list the chosen folder's children without a filename filter.
   - When a plan PDF is found, call `composio_read_drive_pdf` and analyze every rendered page needed to extract notes and dimensions.
   - After creating a project-notes Google Doc, call `composio_read_drive_text_document` on the created document ID and verify the requested sections from the returned text before reporting success.
4. Use only the CJS Outlook connection for CJS correspondence.
5. Match records by verified job number or exact customer/property identity.
6. Combine only verified fields and name any unresolved mismatch.

## Field notes and project handoffs

1. Resolve and verify the exact project folder before handling an employee field note. If more than one project matches, ask for the job number, property, or customer instead of guessing.
2. Preserve the employee's original note verbatim. Organize a copy into: completed work, materials delivered or needed, blockers, safety or damage, customer requests, photos or attachments, and next actions. Do not invent an owner.
3. Use immutable dated note files instead of attempting to append to or overwrite a native Google Doc. Create one document named `EOD - YYYY-MM-DD - <employee or crew>` inside the verified project folder. If the employee is unknown, use `Crew`. Search for the exact filename before creating it. If it already exists, show the conflict and ask whether to create a timestamped follow-up note.
4. Show the exact project, filename, organized contents, and proposed notifications before writing. After confirmation, create only that dated note and call `composio_read_drive_text_document` to verify the live contents.
5. An EOD note needs an Alyssa alert when it reports a blocker, delay, safety incident, equipment damage, material shortage, customer change request, missing field measurement, or next-day dependency. Routine progress alone does not. Use only the verified current project name in the alert; never carry a customer or project name forward from an earlier conversation. In a controlled private test, draft the alert in the test conversation and do not contact Alyssa.
6. A morning crew briefing should combine the live schedule with the latest verified project notes. Separate today's jobs, crew assignments, blockers, material needs, customer constraints, and unknowns. Do not carry a resolved blocker forward.
7. A material shortage report belongs in the dated EOD note and the Alyssa alert. Include item, quantity, project, impact, needed-by time, and owner only when verified.
8. A customer change request is not approved work. Save it as `Change Request - YYYY-MM-DD - <short label>` only after confirmation, preserve the original wording and attachments, and flag it for Alyssa's review. Never describe it as scheduled, priced, or approved unless a live source verifies that status.
9. A completion handoff should check for final EOD notes, required photos or attachments, unresolved blockers, open material issues, change requests, measurements, customer signoff, and billing or closeout status when those sources are available. Distinguish `not found in the available notes` from `not received` or `not completed`; absence of a record is not proof the event did not happen. Label unavailable checks instead of treating them as complete.
10. For “what changed since yesterday,” list and then live-read every dated project note needed for both comparison dates; filenames alone are not evidence of note contents. Compare the notes plus live schedule or job status. Report new completions, new or resolved blockers, material changes, schedule changes, and customer requests. A blocker is resolved only when a later source explicitly says it is resolved; omission from a later note does not resolve or supersede it. Cite the dates and do not infer a change from missing data.
11. For employee questions about what remains, live-read the current project notes plus every latest dated note needed to preserve unresolved items and check the live job or schedule record. Separate verified open work, blockers, assigned owners, unknown owners, and closeout evidence not found in the available sources. Do not turn missing documents into claims that work, photos, measurements, or signoff did not happen, and do not infer unfinished scope from a completed prerequisite.

## Failure rule

If an approved live tool or required connection prevents completion, say exactly: `I couldn't complete that with Mason's current tools.` Then give one short factual reason. Do not ask Clawton to perform the business task. Mason's training hook will create the internal training request.
