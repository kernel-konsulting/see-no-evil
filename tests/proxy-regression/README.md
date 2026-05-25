# Proxy regression runner

This directory holds reusable wiring for the proxy/image regression workflow.
The image corpus itself stays out of Git under `test_data/`.

## Local Podman flow

Use `./tests/proxy-regression/run.sh` from the repo root.

What it does:

- Builds missing Podman images with `deploy/pods/seenoevil.sh build`.
- Starts the `seenoevil` pod with `deploy/pods/seenoevil.sh up` when needed.
- Runs the image regression harness inside the built `image-classifier` image.
- Writes JSON and HTML artifacts under `tests/proxy-regression/results/`.
- Optionally opens the latest HTML report in your browser.

Common commands:

```bash
./tests/proxy-regression/run.sh --write-baseline --view
./tests/proxy-regression/run.sh --fail-on-change
./tests/proxy-regression/run.sh --view-only
```

Defaults assume the local bootstrap admin login:

- Email: `admin@example.local`
- Password: `changeme`

Override them with `--username` / `--password` or `SEENOEVIL_EMAIL` /
`SEENOEVIL_PASSWORD`.

## Future CI shape

The runner already accepts `--dataset-dir`, so CI can point it at a prepared
directory later. If the corpus moves to direct downloads from a hosting site,
that fetch step can live ahead of this runner without changing the harness
contract.
