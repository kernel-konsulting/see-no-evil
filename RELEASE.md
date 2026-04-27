# Releasing see-no-evil

## Versioning

- Tags are SemVer: `vMAJOR.MINOR.PATCH`.
- Pre-1.0 we bump MINOR for any user-visible change and PATCH for fixes.
- The first public tag is `v0.1.0`.

## Pre-flight checklist

Run from a clean clone (no uncommitted changes):

```bash
# Python services
for svc in api image-classifier text-classifier updater scanner; do
  (cd services/$svc && pytest && ruff check . && ruff format --check .)
done

# Go proxy
(cd services/proxy && make generate && go build ./... && go test ./... \
  && golangci-lint run ./...)

# UI
(cd services/ui && npm ci && npm run lint && npm run test && npm run build)

# Compose
docker compose -f deploy/compose/docker-compose.yml config >/dev/null

# Policies
opa fmt --diff policies/ && opa test -v policies/

# Smoke-test the install path on a throwaway VM:
docker compose --profile setup run --rm sne-setup
docker compose --profile core --profile observability up -d
# Click through every page in the UI, trigger a block, watch Grafana.
```

## Tagging

```bash
git switch main && git pull --ff-only
# Update CHANGELOG.md: move "Unreleased" to the new version, add the date.
$EDITOR CHANGELOG.md
git add CHANGELOG.md
git commit -m "release: v0.1.0"
git tag -a v0.1.0 -m "see-no-evil v0.1.0"
git push origin main --tags
```

The `release` GitHub Actions workflow takes over from there:

1. Multi-arch buildx for every service image (amd64, arm64).
2. Push to `ghcr.io/kernel-konsulting/seenoevil-*:vX.Y.Z` and `:latest`.
3. Generate SBOMs (Syft) and sign images (Cosign).
4. Draft a GitHub Release with the relevant CHANGELOG section.

## Post-release

- Bump versions in `services/*/pyproject.toml` and `services/proxy/internal/version`.
- Open the next milestone's tracking issue.
- Update the README "Status" line if the next release scope changes.

## Hot-fix branches

Critical issues land on `release/vX.Y` branches that fork from the tag and
flow back to `main` via merge commits.
