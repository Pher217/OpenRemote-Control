# Contributing

Thank you for your interest in this project. **The backend foundation is implemented and tested** (multi-runtime observe, the universal MCP bridge, the Telegram surface and messaging-gateway connector, a multi-host backend, and a host daemon — see the [README](README.md) for the full shipped/in-progress breakdown). The most useful contributions right now are about **reaching more tools and standing it up live**:

1. **Add a runtime adapter.** If you maintain or use an agent CLI we don't observe yet, open a discussion describing its session lifecycle, the auth modes it supports, and the events/hooks it exposes.
2. **Test the deploy path** on your own self-hosted infrastructure and report where the docs fall short — the deploy runbook is being written now, and real-world friction is invaluable.
3. **Improve the `orc-mcp` install docs** for Cursor, Codex, Claude Code, Copilot, and Kiro (see [`connectors/orc-mcp/README.md`](connectors/orc-mcp/README.md)).
4. **Harden the security boundaries.** The vault, policy engine, secrets redactor, and approval flow get design review before changes — security-minded eyes are very welcome. See [SECURITY.md](SECURITY.md) and [`docs/security/`](docs/security/).
5. **Bug reports and tests** against the existing backend apps.

Some early design notes still live in the maintainer's private knowledge base; if a decision is unclear, open an issue and we'll move the needed context into public docs.

## Workflow

- Every change goes through a delegation packet (worker tier + scope + verification command + acceptance).
- Every PR runs CI (Python: `ruff` + `pytest`; TypeScript: `eslint` + `vitest` + `playwright`).
- Conventional commit messages: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Security-sensitive areas (vault, policy engine, secrets redactor, auth) require explicit design review before implementation and security review before merge.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be respectful and professional.

## License

By contributing, you agree your contributions are licensed under [Apache-2.0](LICENSE).
