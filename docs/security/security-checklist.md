# Security Checklist

> Document: T-104c  
> Status: Accepted  
> Use: Pre-implementation design review + pre-merge security review

## How to use this checklist

1. **Before starting any security-sensitive task** (T-015, T-017, T-042, T-043, T-053, T-070, T-071, T-073, T-081, T-086, T-087, T-090, T-091, T-093, T-102): complete the **Pre-Implementation** section and get a second opinion.
2. **Before merging any PR that touches auth, secrets, policy, approvals, observability, or adapters**: complete the **Pre-Merge** section and attach the results to the PR description.
3. **Non-security tasks** (UI polish, non-auth API additions, docs) can skip this checklist.

---

## Pre-Implementation Design Review

### Threat model alignment

- [ ] I have read `docs/security/threat-model.md` §9 (STRIDE inventory) for my component.
- [ ] I can name at least one STRIDE threat my change mitigates or accepts.
- [ ] If my change introduces a new trust boundary, I have added it to the threat model.
- [ ] If my change handles a new asset class, I have added it to the assets table.

### Authentication and session

- [ ] I know whether my endpoint/session uses cookies, tokens, or signatures.
- [ ] If cookies: httpOnly, Secure, SameSite are configured.
- [ ] If tokens: short-lived, rotated, never in URL query params.
- [ ] If signatures: nonce, expiry, and replay protection are included.
- [ ] Session expiry and idle-timeout are defined.
- [ ] Multi-user implications (if any) are considered.

### Authorization

- [ ] Every new endpoint has a permission class or decorator.
- [ ] Row-level access is enforced (user A cannot see user B's threads/accounts).
- [ ] Admin endpoints are gated behind superuser or staff checks.
- [ ] Default policy is deny-by-default (explicit allow lists, not implicit allow).

### Secrets and credentials

- [ ] No plaintext secrets are logged, traced, or returned in API responses.
- [ ] No secrets are hardcoded in source files (use environment variables or age-encrypted storage).
- [ ] Credential envelope metadata (`key_id`, `recipient`, `scheme_version`, `rotated_at`, `revoked_at`) is populated.
- [ ] Key rotation and revocation paths are considered before implementation.
- [ ] OS key store fallback (macOS Keychain, Windows DPAPI, libsecret) is documented if applicable.

### Input validation

- [ ] All user-facing inputs are validated at system boundaries.
- [ ] File path inputs are restricted to allowed roots (no `../` traversal).
- [ ] Shell command inputs are classified (T-070) or gated behind approval (T-071).
- [ ] JSONB payloads have schema validation where feasible.

### Redaction and retention

- [ ] Redaction runs before ORM save.
- [ ] Redaction runs before WebSocket replay.
- [ ] Redaction runs before queue fan-out.
- [ ] Redaction runs before logging.
- [ ] Redaction runs before tracing and metrics.
- [ ] Raw encrypted retention is opt-in and expiry-bound.

### Approval and notifications

- [ ] Approval notification payloads contain only id + tier + generic title.
- [ ] Full command preview is fetched only inside authenticated surfaces.
- [ ] Destructive actions require explicit confirmation.
- [ ] Approval actions use signed nonces with expiry and replay protection.

### External integrations

- [ ] API base URLs are configurable (no hardcoded endpoints).
- [ ] TLS certificate verification is not disabled.
- [ ] OAuth tokens are not persisted in client storage.
- [ ] Rate limiting and timeout behavior are defined.

### Observability

- [ ] Logs do not contain raw credentials, tokens, or messages.
- [ ] Metric labels do not expose sensitive values (email, org ID, repo URL).
- [ ] Traces redact authorization headers and request bodies.

---

## Pre-Merge Security Review

### Static analysis

- [ ] `bandit` (Python security linter) passes with no high-severity findings.
- [ ] `semgrep` or `pygrep` rules for `eval`, `exec`, `subprocess.shell=True`, `pickle.loads`, `yaml.load(unsafe)` return clean.
- [ ] `npm audit` (frontend) passes with no critical vulnerabilities.
- [ ] `django-check --deploy` warnings are reviewed and accepted or fixed.

### Tests

- [ ] Every new endpoint has at least one authentication failure test (401/403).
- [ ] CSRF-protected mutations have a missing-token rejection test.
- [ ] Approval flow has replay and expiry rejection tests.
- [ ] Redaction is verified in unit tests (redacted output ≠ raw input).
- [ ] Credential rotation has an "old recipient invalid" test.
- [ ] Policy deny-by-default has a "missing allow → reject" test.

### Code review focus

- [ ] No `print()`, `logging.info()`, or `trace` of raw credentials or tokens.
- [ ] No `DEBUG=True` or `SECRET_KEY` fallback in settings.
- [ ] No `CORS_ALLOW_ALL_ORIGINS=True` in non-local settings.
- [ ] No `ALLOWED_HOSTS = ['*']` in non-local settings.
- [ ] No `SECURE_SSL_REDIRECT = False` in production settings.
- [ ] No `SESSION_COOKIE_SECURE = False` in production settings.
- [ ] Database queries are not injectable (ORM use or parameterized queries only).
- [ ] File operations use `pathlib` with resolved, absolute paths inside allowed roots.
- [ ] Subprocess calls use `subprocess.run(..., shell=False)` with explicit argument lists.

### Documentation

- [ ] New endpoints are documented in API docs (OpenAPI schema or manual).
- [ ] Security-relevant configuration options are documented in deployment docs.
- [ ] If this PR changes the threat model, `docs/security/threat-model.md` is updated.

### Secrets check (before every commit)

- [ ] No `.env` files are staged.
- [ ] No `*.key`, `*.pem`, `*.p12`, `*.pfx` files are staged.
- [ ] No hardcoded API keys, tokens, or passwords in diff.
- [ ] `git-secrets` or `detect-secrets` scan passes.

---

## Sign-off

| Reviewer | Date | Result | Notes |
|---|---|---|---|
| | | Pass / Fail / Conditional | |
| | | Pass / Fail / Conditional | |

**Merge rule:** Two security review passes required for any PR touching auth, secrets, policy, approvals, observability, or adapters. One pass is acceptable for non-security PRs that accidentally touch adjacent code, provided the security-touching lines are explicitly reviewed.
