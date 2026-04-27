# Contributing to see-no-evil

Thanks for taking the time! This is an early-stage project — at the M0 milestone
nothing is functional yet, so the most valuable contributions right now are:

- Reviewing the architecture (`docs/architecture.md`) and threat model
  (`docs/threat-model.md`) for holes.
- Reviewing `config.example.yaml` for ergonomic problems.
- Filing issues for use cases we haven't accounted for (school deployments,
  multi-VLAN homes, IPv6-only networks, etc.).

## Development environment

You'll need:

- Docker or Podman (with `compose` v2)
- Go 1.22+
- Python 3.12+
- Node 20+

The repository is organized as a polyglot mono-repo. Each service in
`services/<name>/` is independently buildable. There is no top-level package.

## Linting and formatting

Run before opening a PR:

- Python: `ruff check .` and `ruff format .`
- Go: `golangci-lint run ./...` and `gofmt -s -w .`
- OPA policies: `opa fmt -w .` and `opa test .`
- YAML / JSON / Markdown: `prettier --write .`

CI runs all of the above on every PR.

## Commit and PR conventions

- Conventional Commits where it's natural (`feat:`, `fix:`, `docs:`, `chore:`).
- One logical change per PR. Keep diffs reviewable.
- Reference the milestone in the PR description (`M0`, `M1`, ...).
- Update `docs/` in the same PR as the code if the change is user-visible.

## License of contributions

By contributing, you agree your contributions are licensed under
[PolyForm Noncommercial 1.0.0](LICENSE), the same license as the project.
