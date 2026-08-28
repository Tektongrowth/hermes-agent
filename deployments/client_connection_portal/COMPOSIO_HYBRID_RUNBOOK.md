# Composio Hybrid Connector Runbook

Status: implementation design and CJS gap audit

Reviewed: 2026-08-27 UTC

Owner: Tekton Growth

First tenant: CJS Landscape

## Decision

Use Composio as the default OAuth connection broker for supported services. Do not use it as Tekton's complete security boundary.

Composio will own hosted OAuth, connected-account credential storage, ordinary token refresh, and provider tool execution for approved OAuth services. Each connector will expose the provider's full supported toolkit. Tekton will own tenant identity, stable internal user identity, account binding, user permissions, approvals, audit records, and every decision to enable or disable a connection.

AWS remains the source of truth for the Composio runtime key, provider app credentials used to create custom auth configs, webhook secrets, non-OAuth credentials, unsupported services, and tenant policy. Mason and delegated workers never receive those values.

This split matches Composio's user-scoped authentication model while preserving Tekton's client and account boundaries. Composio asks the caller to supply a stable `user_id`, stores connected accounts under it, and handles credentials for that user.[1] Composio also states that projects isolate resources and connected-account credentials are encrypted, while the customer remains responsible for choosing toolkits, accounts, and agent permissions.[23]

## Non-negotiable controls

1. The model never receives a Composio API key, provider token, auth config secret, webhook secret, hosted-link token, or unrestricted MCP URL.
2. The model never chooses `tenant_id`, internal user ID, Composio `user_id`, auth config ID, connected-account ID, callback URL, AWS path, or permission profile.
3. A browser may submit only an invitation token, CSRF value, server-allowlisted account slot, and non-secret display information.
4. Every tool call is checked against the current user's permissions before exposure and again immediately before execution.
5. The gateway passes one server-selected connected-account ID. It never accepts an account ID supplied by Mason, a Discord message, or browser free text.
6. Connectors do not carry business permission filters. The full supported toolkit remains attached to the connector. User permissions decide which people can see or run each capability.
7. Connection management remains a portal and administrator function. A user permission cannot expose credentials, tokens, connector administration, workbench, sandbox, shell, or proxy execution.
8. A connected account marked `ACTIVE` is not enough. Tekton must also verify its expected `user_id`, toolkit, auth config, upstream identity, account slot, and user permission state.
9. Production payload retention is set to `Don't store data` before the first real tool call. Composio's default stores request and response payloads, while `Don't store data` keeps audit metadata without storing those payloads for new calls.[24]
10. Provider tokens are never retrieved through connected-account state. Composio redacts connected-account tokens by default, and Tekton's design prohibits any raw-credential retrieval path.[23]

## Responsibility boundary

| Surface | Composio owns | Tekton owns |
|---|---|---|
| OAuth | Hosted authorization and connected-account credential lifecycle | Invitation, user mapping, auth config selection, callback correlation, identity review, enablement |
| Identity | Isolation under the supplied `user_id` | Stable internal user ID, tenant mapping, role mapping, upstream account verification |
| Account choice | Use of a selected connected account | Account-slot registry and exact connected-account pinning |
| Tool access | Full toolkit attached to the connector | User permission profile, approval policy, and execution-time permission check |
| Secrets | Connected-account provider credentials | Composio key, provider app credentials, webhook secret, non-OAuth credentials, unsupported services |
| Logs | Composio audit metadata | Tekton authorization, approval, request, result, lifecycle, and incident records |
| Lifecycle | Connected-account states and ordinary OAuth refresh | Status reconciliation, reconnect UX, atomic slot replacement, disablement, revocation, operator alerts |
| Events | Trigger delivery and signature mechanism | Raw-body verification, deduplication, tenant mapping, queueing, idempotency, dead letters |

## Production account model

Use a stable database UUID or primary key as the canonical user identity. Do not use an email address, employee name, Discord username, or client display name. Composio explicitly recommends a stable identifier such as a database ID because connections are stored under `user_id`.[1]

One internal user may have several upstream accounts. Composio supports multiple connected accounts for one toolkit, including separate work and personal accounts.[27] Therefore, a shared `user_id` never proves two connected accounts belong to the same Google, Microsoft, Intuit, or Meta identity.

Store only non-secret metadata in the portal database:

```json
{
  "tenant_id": "cjs-landscape",
  "internal_user_id": "usr_<opaque-database-uuid>",
  "composio_user_id": "tk_prod_<opaque-database-uuid>",
  "slot_id": "gmail-primary",
  "slot_generation": 3,
  "purpose": "CJS office inbox",
  "toolkit": "gmail",
  "auth_config_id": "ac_<reference>",
  "connected_account_id": "ca_<reference>",
  "connected_account_alias": "cjs-office-gmail",
  "identity_policy": "exact | unbound | delegated_resource",
  "expected_upstream_identity_hash": "<hash-or-null>",
  "verified_upstream_identity": "<non-secret-display-value-or-null>",
  "provider_resource_ids": ["<verified-provider-native-id>"],
  "permission_profile_id": "cjs-user-permissions-v1",
  "toolkit_version": "<tested-version-contract>",
  "policy_version": "cjs-connectors-v1",
  "approval_state": "pending_review | enabled | suspended | disabled",
  "broker_status": "INITIALIZING | INITIATED | ACTIVE | EXPIRED | INACTIVE",
  "local_status": "connecting | verifying | ready | needs_reconnect | blocked | retired",
  "last_identity_check_at": "<timestamp>",
  "last_successful_probe_at": "<timestamp>",
  "created_at": "<timestamp>",
  "updated_at": "<timestamp>"
}
```

Never persist access tokens, refresh tokens, authorization codes, hosted connect links, full callback query strings, OAuth state values, provider passwords, MFA values, cookies, or raw connected-account responses.

## Auth config policy

Use Composio-managed auth only for internal sandbox testing when the default scopes and Composio branding are acceptable. Use customer-owned provider apps and custom auth configs in production when exact scopes, Tekton or client branding, dedicated provider quota, or a client-specific provider setup matters. Composio documents that managed auth uses Composio's apps and shared quota, while custom auth configs accept customer-owned provider credentials and scope choices.[25]

For each production account slot:

1. Create or select the provider app under the approved Tekton or client owner.
2. Record the exact provider scopes in the server registry.
3. Store the provider app credential bundle in the approved AWS `SecureString` path without printing it.
4. Create the Composio auth config from the controlled backend.
5. Store only the auth config ID and verified scope fingerprint in the slot policy.
6. Read the auth config back through a metadata-only API and compare toolkit, auth method, redirect configuration, and scope fingerprint.
7. Reject any browser-supplied auth config ID.
8. Require a new policy version and test matrix before changing scopes.

## Portal connection flow

The portal owns connection setup. Mason does not create or remove connections in chat.

### 1. Operator creates the account slot

The operator selects a server-registered account slot and supplies a non-secret purpose label. The server resolves the toolkit, auth config, identity policy, callback route, and AWS references. User permissions are configured separately and are not stored on the connector.

The operator sets one identity policy:

- `exact`: the authenticated provider identity must match the predeclared identity.
- `unbound`: the client selects an account on the provider page, then an operator reviews the returned identity before enablement.
- `delegated_resource`: the authenticated identity may differ, but Tekton must verify access to the declared mailbox, Drive, Page, company, or calendar resource.

### 2. Portal issues the invitation

Create a short-lived, recipient-bound, one-time invitation. Persist a hash of the recipient identity where practical. Bind the invitation to one tenant and an explicit list of account slots. Issue a session cookie and CSRF value after the invitation is claimed.

Reject expired, replayed, malformed, cross-tenant, uninvited-slot, and completed invitations.

### 3. Backend resolves the stable Composio user

Load `internal_user_id` from the authenticated portal record. Derive or retrieve the immutable `composio_user_id`. Reject missing, duplicate, mutable, or conflicting mappings.

Do not derive `composio_user_id` from the invitation recipient's email. The invitation recipient and provider account remain separate claims.

### 4. Backend creates the connector authorization session

Create a server-side Composio session containing:

- one `user_id`
- one toolkit
- the selected auth config
- the toolkit's full supported connector capability
- the tested toolkit version

Do not apply business permissions to the connector session. Keep the connector complete. Tekton's user-permission service decides what each person sees and can execute. Connection administration and credential access remain separate system permissions that ordinary users never receive.

Record the SDK version, request schema, selected toolkit version, returned session contract, and connector fingerprint. Do not depend on changing default behavior.

### 5. Backend creates a fresh hosted connect link

Call the server-side authorization method for the selected toolkit, auth config, stable `user_id`, unique account alias, and fixed callback URL. Composio returns a hosted connect link; the portal redirects the user to it and waits for connection completion.[4]

Treat the link as a secret-bearing, short-lived value. Do not log it, put it in TaskTracker, paste it into Discord, or include it in browser target inventories. A hosted link expires after roughly 10 minutes. Generate a fresh link instead of retrying the old link.[16]

### 6. Callback correlates, then retrieves

The callback must validate Tekton's own signed, single-use correlation state before reading any connected-account reference. The callback result is a hint, not proof.

After a successful callback:

1. Retrieve the connected account by its returned ID through the backend.
2. List connected accounts under the expected `user_id` and confirm the ID appears there.
3. Confirm toolkit and auth config match the slot policy.
4. Require broker status `ACTIVE`.
5. Reject an existing connected-account ID already bound to another tenant, internal user, or slot.
6. Execute one approved identity/profile tool against that exact connected-account ID.
7. Apply the slot's `exact`, `unbound`, or `delegated_resource` identity policy.
8. Verify declared provider resource IDs using approved read tools.
9. Persist a non-secret metadata projection with `approval_state=pending_review` and `local_status=verifying`.

Composio describes `INITIATED` as an incomplete connection and notes that abandoned hosted flows stay in that state until expiration.[4] Tekton does not enable a connection until the account reaches `ACTIVE` and the independent identity and resource checks pass.

### 7. Operator enables the slot

Show the operator:

- client and tenant
- account-slot purpose
- verified upstream identity
- declared shared resource or company
- toolkit and auth config label
- assigned user-permission profile
- connector version
- retention mode
- connection and identity check timestamps

The operator confirms the slot. The server atomically changes `approval_state` to `enabled` and `local_status` to `ready`. The account is unavailable to the gateway before that state change.

## Gateway execution flow

Every Composio-backed request follows this sequence:

1. Authenticate the caller and resolve the approved Discord server, channel, client, tenant, and internal user.
2. Load that user's permission profile.
3. Resolve the connector and exact connected account assigned to the tenant.
4. Confirm the user's permission level for that connector and requested capability.
5. Confirm `approval_state=enabled`, `local_status=ready`, and broker status is usable.
6. Confirm the exact toolkit, auth config, connected-account ID, `user_id`, provider identity, and slot generation.
7. Pass the requested Composio tool only when the user's permission allows it.
8. Validate required provider IDs and argument types so the request reaches the intended account and record.
9. Apply any limits or approval requirements attached to that user's permission profile.
10. Create or retrieve a short-lived server-side Composio session pinned to the correct `user_id`, toolkit, connected account, and tested version contract. The connector itself retains the full toolkit.
11. Execute through the server transport. Do not expose the session URL or headers to Mason.
12. Strip credentials, private headers, and token-bearing links from the result.
13. Write a metadata-only audit record with the user, permission decision, connector, tool, timing, and approval ID when present.
14. Return the result to Mason.

Composio permits a session to be pinned to specific connected-account IDs, which prevents silent use of the most recently connected account when several accounts exist.[27] Tekton still performs its own account and tenant checks before creating that session.

## CJS user permissions

### Crew

- Keep the existing Mason permissions until Nick assigns broader access.
- No Composio connection-management tools.
- No Gmail, Outlook, Drive, Calendar, QuickBooks, or Facebook access in the first release.
- No writes, financial operations, approvals, scheduling, publishing, billing, terminal, browser, or file access.
- No QuickBooks data under any crew approval.

### Nick or an approved CJS administrator

- Assign access through a user-permission profile.
- A permission profile can grant `none`, `read`, `read_write`, or `admin` access for each connector.
- Nick can receive full connector capability without adding filters to the connector.
- Sensitive operations can still require approval through the user's permission profile.

### Delegated workers

- Receive no Composio key, MCP URL, connection-management API, or raw connected-account metadata.
- Receive connector capabilities only when the initiating user's permission profile allows them.
- Skip profile context files and memory.
- Fail closed on ambiguous client, user, channel, account, or policy scope.

## User-level permissions

Connectors remain complete. We do not create separate filtered Gmail, Outlook, Drive, Calendar, QuickBooks, or Facebook connectors for different roles.

Permissions attach to the internal user, not the connector:

```json
{
  "internal_user_id": "usr_<opaque-database-uuid>",
  "connector_id": "cjs-gmail-primary",
  "access_level": "none | read | read_write | admin",
  "approval_required_for": ["<sensitive-capability>"],
  "permission_version": "cjs-user-permissions-v1"
}
```

Permission meanings:

- `none`: the user cannot see or use the connector.
- `read`: the user can use read capabilities from the full connector toolkit.
- `read_write`: the user can read and make ordinary changes.
- `admin`: the user can use the full connector capability, except credentials, billing, and connector administration remain system-controlled.

The same connector can give Nick `admin`, Alyssa `read_write`, an office employee `read`, and a crew member `none`. Changing one user's access does not reconnect the account or change anyone else's access.

Account binding still matters. It is routing, not a connector filter. When a user opens Gmail, the system must choose the CJS Gmail connector rather than another company or personal account.

Meta Ads follows the same user-permission model, but net-new campaign launches and billing remain Nick-controlled.

## Version control for tools

Record the Composio SDK version, toolkit version contract, exact tool schemas, required scopes, and argument/output projection in each policy version. Run contract tests before accepting a new version.

Composio recommends sessions for agents and says sessions handle toolkit versions automatically. Its direct-execution guidance also states that production versions may be pinned, tested, and rolled back.[10] Tekton will still record and test the resolved version because automatic selection is not a change-control policy.

Do not use an untested `latest` tool contract in production. Do not silently accept a tool whose slug is unchanged but schema, required scope, behavior tag, or result shape changed.

## Connection lifecycle

### Normal operation

- Reconcile local slot metadata with Composio on a schedule.
- Check the exact connected account by ID, expected `user_id`, toolkit, auth config, and status.
- Run a harmless identity or profile probe after a long idle period and before sensitive bulk reads.
- Mark stale or conflicting records `blocked` before returning data.

Composio ordinarily handles token refresh and connected-account credential management.[5] That does not replace Tekton's status checks, identity probes, or reconnect UX.

### Expired or unusable account

When status becomes `EXPIRED`, an identity probe fails because authorization is unusable, or the provider revokes access:

1. Set the local slot to `needs_reconnect` and deny execution.
2. Notify the approved operator without including token, callback, or provider error details.
3. Create a fresh hosted authorization flow for the same stable `user_id`, slot, toolkit, and auth config.
4. Validate the new connected account as if it were new.
5. Write it as the next slot generation in `pending_review`.
6. After review, atomically switch the active slot generation to the new connected-account ID.
7. Disable or revoke the old connected account after the new generation passes a live read probe.
8. Keep a metadata-only tombstone linking the old and new generations.

Composio's current connected-account guidance recommends a new auth-link session for reauthentication and says not to keep retrying an expired hosted link.[16]

### Disable, revoke, and delete

- Suspend locally first so no new call starts.
- Wait for in-flight calls to finish or time out.
- Revoke provider authorization when supported.
- Disable the connected account in Composio.
- Delete the connected account only after explicit destructive approval and provider-side revocation checks.
- Delete or retire local non-secret metadata according to retention policy while preserving the required audit tombstone.
- For non-OAuth credentials, rotate or delete the AWS secret under its separate approval process.

## Trigger and webhook flow

A Composio trigger watches one event on one user's connected account. When several accounts exist, pass the exact connected-account ID instead of accepting the first active account.[12]

For each approved trigger:

1. Store tenant, internal user, slot ID, slot generation, connected-account ID, trigger ID, trigger slug, resource IDs, policy version, and lifecycle state.
2. Register one production webhook endpoint for the isolated Composio project.
3. Store the webhook verification secret in AWS and expose it only to the webhook runtime.
4. Read the raw request body before parsing.
5. Verify the signature before JSON parsing, logging, queueing, or returning success. Composio's handler guidance requires the raw body for signature verification.[13]
6. Reject missing, invalid, stale, oversized, malformed, or unmapped events.
7. Derive a deduplication key from the provider event ID, Composio event identity, trigger ID, and payload fingerprint.
8. Atomically claim the key with a TTL before processing.
9. Map the trigger and connected account to exactly one current tenant slot generation.
10. Reject retired generations, cross-tenant mappings, wrong toolkits, and unknown resources.
11. Place the normalized, minimum-required event on a tenant-scoped queue.
12. Make the consumer idempotent. Store the business-side operation key before performing any approved mutation.
13. Retry bounded transient failures and send exhausted events to a dead-letter queue.
14. Log only metadata and redacted failure classes. Do not log raw email bodies, attachments, financial records, provider tokens, or webhook secrets.

A successful webhook response means the event was authenticated and durably accepted. It does not mean the downstream business operation succeeded.

## AWS and unsupported-service fallback

Use Composio only when the service and required OAuth flow pass the broker review. Use Tekton's AWS and gateway path for:

- API keys and bearer tokens
- service-account credentials
- human logins that lack an approved OAuth path
- Yeti and other fixed-schema non-OAuth accounts
- custom client APIs
- provider features absent from the approved Composio toolkit
- workflows requiring a provider endpoint or security control the Composio tool contract does not provide

Rules for the fallback path:

1. Resolve the secret destination from a server-side registry.
2. Store runtime secrets as KMS-encrypted SSM `SecureString` values under the approved `/tekton/prod/` hierarchy.
3. Store approved human-login records in the designated Secrets Manager namespace.
4. Give the portal write-only access to exact destinations when self-service intake is required.
5. Give the runtime read access only to the exact secret needed by its connector.
6. Deny listing, cross-client paths, browser-selected paths, and secret readback to the portal.
7. Return only connection status and safe account labels to Mason.

## Audit record

Write one append-only metadata record for each connection lifecycle change, authorization decision, approval, tool execution, trigger receipt, and revocation.

Required fields:

- event ID and timestamp
- tenant and internal user IDs
- caller platform, server, channel, user, and role IDs when applicable
- account slot and generation
- toolkit, auth config reference, and connected-account reference
- gateway tool and Composio tool slug
- policy and toolkit-version contracts
- resource IDs and bounded date or page ranges
- decision: allow or deny
- denial or result class
- approval ID and approver for gated operations
- latency and broker request correlation ID
- retry, deduplication, and idempotency keys when applicable

Do not store tool arguments or results by default. Store allowlisted summaries only when an operational need and retention policy require them.

## Current CJS implementation audit

### Present and useful

- `portal/app.py` binds invitations to one tenant, recipient, session, and allowlisted slots.
- The portal has CSRF checks, one-time invitation claiming, completion locking, and safe response headers.
- Workbench cards come from a server catalog and reject browser-supplied storage or policy fields.
- Test mode returns `404` for live OAuth and credential routes and simulates only metadata.
- `portal/registry.py` has fixed CJS slots and fixed destinations.
- `portal/store.py` and `portal/dynamo_store.py` use conditional state changes for one-time mutations.
- `portal/composio.py` contains a narrow Gmail sandbox pilot with one tenant. Its current read-tool filters conflict with the new full-connector decision and must be removed before production.
- Existing tests cover cross-tenant rejection, unreviewed toolkits, and rejection of browser-supplied tool or account overrides.
- Mason's current policy permanently denies crew writes, financial operations, QuickBooks data, credentials, connection changes, and broader administration.

### Blocking gaps

1. The production portal routes are not implemented. The UI states that production connection is not enabled.
2. The current registry models direct provider OAuth bundles stored in SSM. It does not model Composio auth configs, connected accounts, account aliases, slot generations, or broker lifecycle states.
3. The sandbox derives `user_id` from a short operator label. Production requires an immutable internal database ID.
4. The sandbox does not pin a connected-account ID because it connects no real account.
5. The sandbox response validates only a session ID prefix. It does not inspect the connector contract, toolkit version, account binding, or server acceptance.
6. The sandbox currently sends connector-level read filters. The production design removes those filters and keeps the connector's full toolkit.
7. The sandbox has no auth config selection, upstream identity check, provider resource check, connection review state, or reconnect flow.
8. No gateway transport loads the current user's permission profile and routes an allowed request to the exact connected account.
9. No second execution-time authorization check exists for Composio tools.
10. No Composio webhook handler, signature verification, deduplication store, queue, idempotency control, or dead-letter path exists.
11. No connection-expiration reconciliation, operator alert, atomic replacement, disable, revoke, or deletion workflow exists.
12. The repository has no production IAM policy for the hybrid broker boundary.
13. Data-retention mode, project isolation, API-key scope, MFA policy, and custom auth configs are not recorded as deployed evidence.
14. The repository has no user-permission model, permission-management screen, or execution-time permission service.

Conclusion: the CJS code is a safe simulated workbench and a configuration-only Gmail broker pilot. It is not a production Composio connector gateway.

## Rollout sequence

### Gate 0: Contract and ownership

- Approve the named Composio project and CJS tenant boundary.
- Confirm billing owner and spend limits.
- Enforce Composio organization MFA.
- Create a dedicated CJS project and a dedicated least-privilege runtime key.
- Set payload retention to `Don't store data` before execution.
- Record the current SDK and API contract.
- Select managed auth for sandbox or custom auth configs for production.

Stop if any ownership, billing, retention, project, or key boundary is unclear.

### Gate 1: Local implementation

- Add the production account-slot model and state transitions.
- Add stable internal user mapping.
- Add current Composio transport interfaces with redacted error handling.
- Add auth config and connected-account metadata validation.
- Add portal connect, callback, review, reconnect, disable, revoke, and delete flows.
- Add account-pinned gateway execution.
- Add the user-permission model, permission-management screen, and execution-time permission checks.
- Add webhook verification, deduplication, queues, and idempotency.
- Add AWS fallback connectors without broadening portal IAM.

### Gate 2: Automated negative tests

The test suite must prove:

- wrong tenant denied
- wrong internal user denied
- wrong Composio `user_id` denied
- wrong auth config denied
- wrong connected account denied
- wrong upstream identity denied
- wrong provider resource denied
- connector hidden when the user has `none` access
- write capability denied when the user has `read` access
- full connector capability available when the user has `admin` access
- sensitive capability denied when that user's profile requires approval and no valid approval exists
- CJS crew denied every Composio and QuickBooks capability under the initial permission profile
- expired, inactive, retired, and pending-review connections denied
- replayed callback and invitation denied
- expired connect link replaced rather than retried
- account reconnect switches generations atomically
- invalid webhook signature denied before parsing
- duplicate webhook acknowledged once and processed once
- retired trigger and account generation denied
- no secret or payload in logs, exceptions, fixtures, snapshots, or responses
- portal cannot list or read secrets
- runtime cannot access another client's secret or connection
- toolkit schema or scope drift fails the contract test

### Gate 3: Isolated sandbox

- Use no real client account.
- Validate project settings and session policy.
- Confirm the connector contains the toolkit's full supported capability.
- Confirm each test user sees only the capabilities granted by that user's permission profile.
- Confirm workbench, sandbox, proxy execution, credentials, and connection management remain system-only.
- Verify the toolkit and tool version contract.
- Stop at configuration-only status if no approved test account exists.

### Gate 4: Approved test account

- Connect one dedicated non-client test account.
- Verify identity and resource binding.
- Run only harmless reads.
- Prove wrong-user, wrong-account, cross-tenant, insufficient-permission, and expired-account refusal.
- Test provider revocation and fresh-link reconnect.
- Test webhook signature, replay, deduplication, and dead-letter handling.
- Scan logs and Composio execution records for payload retention.

### Gate 5: CJS pilot

Start with one connector and assign it only to Nick during the pilot. Keep the connector complete, but begin testing with harmless reads. Recommended order:

1. Gmail profile and bounded message metadata
2. Google Drive file metadata inside one approved folder or Drive
3. Calendar free/busy and bounded event reads
4. Outlook profile and bounded message metadata
5. Facebook Page details and insights
6. QuickBooks company identity, then one bounded report

Give crew `none` access to these connectors during the pilot.

For each slot, require:

- Nick's approval for the named account and scopes
- successful hosted authorization by the right provider user
- independent identity and resource verification
- exact account pinning
- clean read probe
- restart persistence
- negative account and tenant tests
- log and retention review
- documented rollback

### Gate 6: Production release

Release only when every acceptance test passes in the production boundary and the active runtime loads the approved policy version. A live connection does not prove the end-to-end gateway is safe.

## Rollback

If a release fails:

1. Set the affected local slot to `suspended`.
2. Set affected users' connector permission to `none`.
3. Disable the connected account.
4. Stop affected triggers and queue consumers.
5. Preserve metadata-only incident evidence.
6. Restore the previous policy and slot generation only if its provider grant and identity probe remain valid.
7. Revoke provider access when compromise or account confusion is possible.
8. Rotate the Composio runtime key or webhook secret if exposure is suspected.
9. Re-run tenant, account, tool, retention, and leakage tests before reopening.

## Definition of done

The hybrid method is production-ready only when:

- Composio handles approved OAuth without exposing provider credentials to Tekton agents.
- Tekton resolves one tenant, internal user, permission profile, connector, and connected account for every call.
- The portal owns setup, status, reconnect, and operator review.
- Every connector retains its full supported toolkit.
- Each user sees and runs only the capabilities granted by that user's permission profile.
- CJS crew begins with `none` access and remains fail-closed.
- AWS holds non-OAuth and runtime secrets behind exact IAM.
- Payload retention is disabled for new Composio executions.
- Webhooks verify raw-body signatures and process idempotently.
- Expiration and reconnect replace account generations atomically.
- Tool schema and version drift fail closed.
- All positive, negative, restart, rollback, leakage, and cross-tenant tests pass.

## Sources

[1] https://docs.composio.dev/docs/authentication.md
[4] https://docs.composio.dev/docs/authentication/manually-authenticating.md
[5] https://docs.composio.dev/docs/auth-configuration/connected-accounts.md
[10] https://docs.composio.dev/docs/tools-direct/toolkit-versioning.md
[12] https://docs.composio.dev/docs/setting-up-triggers/creating-triggers.md
[13] https://docs.composio.dev/docs/setting-up-triggers/subscribing-to-events.md
[16] https://docs.composio.dev/kb/guide/platform-connected-accounts.md
[23] https://docs.composio.dev/docs/security/overview.md
[24] https://docs.composio.dev/docs/security/data-retention.md
[25] https://docs.composio.dev/docs/authentication/custom-app-vs-managed-app.md
[27] https://docs.composio.dev/docs/authentication/managing-multiple-connected-accounts.md
