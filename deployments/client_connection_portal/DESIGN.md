# Client Connection Portal

## Purpose

A reusable AWS-hosted onboarding portal for tenant-isolated service connections. CJS Landscape is the first tenant.

The portal accepts only two connection classes:

1. Official OAuth for Google Workspace, Microsoft 365, and Intuit.
2. Narrowly approved non-OAuth credentials for providers such as Yeti.

It never asks for a Google or Microsoft password.

## Deployment modes

### Test

- Uses a dedicated Lambda and DynamoDB table.
- Stores only simulated connection metadata in the test table.
- Has no IAM permission to write SSM parameters, Secrets Manager values, or KMS ciphertext.
- Does not call live provider authorization, token, identity, or mailbox endpoints.
- Real OAuth and credential-intake routes return 404.
- Safe simulation routes are available only inside a valid invitation session.

### Production

- Uses a separate Lambda role, DynamoDB table, signing key, OAuth client configuration, and log group.
- Uses official provider OAuth pages.
- Writes OAuth bundles only to an allowlisted tenant and slot destination.
- Writes an approved non-OAuth credential only to its fixed tenant slot.
- Does not read stored credential values back to the browser.

Test and production resources never share tables, signing keys, OAuth clients, or write permissions.

## Tenant boundary

Every invitation and persisted record contains:

- `tenant_id`
- `invitation_id`
- intended recipient email
- an allowlisted set of connector slots
- issue and expiration timestamps
- completion and one-time-use state

The server resolves all provider names, OAuth scopes, callback paths, and AWS storage destinations from a server-side tenant registry. Browser input never chooses an AWS path, tenant, provider endpoint, callback URI, or OAuth scope.

The first production role is restricted to explicit CJS resources. A future tenant receives a separate deployment role or an explicitly enumerated resource set. Wildcards such as `clients/*` are not accepted for test or CJS production writes.

## Invitation flow

1. An operator issues an invitation with a short TTL, recipient email, tenant ID, and explicit slots.
2. The operator command writes an invitation state record and returns a signed opaque link.
3. The recipient opens the link. The server verifies signature, expiry, tenant, recipient, and persisted invitation state.
4. The setup page issues a session-bound CSRF token.
5. Each connector action must name a slot present in that invitation.
6. OAuth state is signed, session-bound, slot-bound, provider-bound, single-use, and persisted before redirect.
7. Provider identity must match the slot's expected authorizing identity when one is configured.
8. Successful completion stores metadata only in the session record and locks the slot.
9. Completing the invitation locks the session. Replayed links or state records are rejected.

## Provider contracts

### Google Workspace email

- Authorization endpoint: Google's official OAuth endpoint.
- Scopes: OpenID identity plus Gmail read-only.
- No send, modify, delete, forwarding, settings, or administration scope.
- Connection check reads identity and harmless mailbox metadata only.

### Microsoft 365 email

- Authorization endpoint: Microsoft's official OAuth endpoint.
- Scopes: `openid`, `profile`, `email`, `offline_access`, `User.Read`, and `Mail.Read`.
- No `Mail.Send`, mailbox settings, directory write, or administration scope.
- A shared mailbox slot records both the authorizing principal and target mailbox. The portal never treats an alias as independent proof of access.

### Google Drive

- Separate slot and consent from Gmail.
- Read-only scope.

### Intuit QuickBooks

- Separate CJS and Whiteout slots where required.
- Accounting scope only.

### Yeti

- Non-OAuth intake is allowed only for a predeclared slot.
- The portal accepts only the fixed fields for that slot.
- Test mode stores field names and a success marker only, never submitted values.
- Production writes once to the fixed Secrets Manager destination and never returns the saved values.

## Storage contracts

### DynamoDB

Partition key format:

- Invitation: `TENANT#{tenant_id}#INVITE#{invitation_id}`
- OAuth state: `TENANT#{tenant_id}#OAUTH#{state_id}`
- Audit event: `TENANT#{tenant_id}#AUDIT#{event_id}`

Records include an `expires_at` TTL attribute. Audit events contain metadata only.

### SSM Parameter Store

OAuth bundles use a server-side destination registry. Example shape:

`/tekton/clients/cjs-landscape/runtime/oauth/{slot}`

The browser cannot supply or alter this path.

### Secrets Manager

Non-OAuth credentials use a preprovisioned secret ARN or exact resource name for the slot. The runtime can call `PutSecretValue` but not `GetSecretValue`.

## Logging and response policy

- No credential values, OAuth tokens, authorization codes, setup tokens, CSRF tokens, state tokens, cookies, or raw provider responses in application logs.
- Request bodies and query strings are not logged.
- Errors use fixed public messages and opaque event IDs.
- Security headers prohibit framing, sniffing, caching, referrer leakage, and third-party scripts.
- Setup pages use no analytics, external fonts, or third-party JavaScript.

## Acceptance gates

The isolated test deployment is acceptable only when all of the following pass:

- invitation signature, expiry, recipient, tenant, and one-time-use tests
- cross-tenant and uninvited-slot refusal tests
- CSRF and OAuth-state binding tests
- official-provider URL and least-scope tests
- test-mode route-isolation and zero-secret-write tests
- fixed-destination tests
- response and log leakage tests
- completion and restart-persistence tests
- desktop and mobile browser QA
- test IAM review proving no SSM, Secrets Manager, or KMS write permission
- DynamoDB TTL and encryption verification
- deployed route and security-header checks

Alyssa does not receive a link until Nick approves the tested provider callback configuration and production invitation.
