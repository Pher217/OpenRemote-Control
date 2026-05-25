# Contributing

Thank you for your interest in this project. **It is currently in specification phase — no runnable code yet.** Contributions at this stage are most useful as:

1. **Review of the specification.** Read [docs/specs/agent-command-center-v5-build-spec.md](docs/specs/agent-command-center-v5-build-spec.md) and the V5 addendum + Codex review. Open issues if you spot gaps, conflicts, or unrealistic assumptions.
2. **Threat model contributions.** The MVP gates implementation on a threat model. See `docs/security/threat-model-pending.md` once it exists.
3. **Adapter design proposals.** If you maintain or use an agent runtime you'd like to integrate, open a discussion with: how its session lifecycle works, what auth modes it supports, what events/hooks it exposes, what platform constraints apply.

## When code starts

The project follows the workflow in `docs/specs/agent-command-center-v5-build-spec.md` §8:

- Every change goes through a delegation packet (worker tier + scope + verification command + acceptance).
- Every PR runs CI (Python: `ruff` + `pytest`; TypeScript: `eslint` + `vitest` + `playwright`).
- Conventional commit messages: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Security-sensitive areas (vault, policy engine, secrets redactor, auth) require explicit design review before implementation and security review before merge.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be respectful and professional.

## License

By contributing, you agree your contributions are licensed under [Apache-2.0](LICENSE).
