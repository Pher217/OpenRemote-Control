# Contributing

Thank you for your interest in this project. **The backend foundation exists; runtime adapters, the host daemon, and the frontend are not built yet.** Contributions at this stage are most useful as:

1. **Adapter design proposals.** If you maintain or use an agent runtime you'd like to integrate, open a discussion with: how its session lifecycle works, what auth modes it supports, what events/hooks it exposes, what platform constraints apply.
2. **Threat model contributions.** The MVP gates implementation on a threat model. See `docs/security/threat-model.md`.
3. **Bug reports and tests** against the existing backend apps.

The detailed design specification and delegated task breakdown are maintained in the maintainer's private knowledge base; open an issue if you need design context for a contribution.

## Workflow

- Every change goes through a delegation packet (worker tier + scope + verification command + acceptance).
- Every PR runs CI (Python: `ruff` + `pytest`; TypeScript: `eslint` + `vitest` + `playwright`).
- Conventional commit messages: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Security-sensitive areas (vault, policy engine, secrets redactor, auth) require explicit design review before implementation and security review before merge.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be respectful and professional.

## License

By contributing, you agree your contributions are licensed under [Apache-2.0](LICENSE).
