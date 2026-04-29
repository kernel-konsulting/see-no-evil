# see-no-evil — Kubernetes-style YAML for `podman kube play`

A declarative alternative to [`deploy/pods/seenoevil.sh`](../pods/seenoevil.sh).
Same single-pod topology, same volume names, same defaults — just expressed as
a Kubernetes `Pod` + `PersistentVolumeClaim` manifest that
[`podman kube play`](https://docs.podman.io/en/latest/markdown/podman-kube-play.1.html)
understands natively.

```sh
# bring it up (must be run from the repo root so the hostPath for
# config.example.yaml resolves)
cd /path/to/see-no-evil
podman kube play deploy/kube/seenoevil.yaml

# tear it down (stops + removes containers + pod; keeps PVCs)
podman kube down deploy/kube/seenoevil.yaml

# also remove the volumes
podman kube down --force deploy/kube/seenoevil.yaml
```

## Why both?

| File                         | Best when                                                        |
|------------------------------|------------------------------------------------------------------|
| `deploy/pods/seenoevil.sh`   | Quick local dev, env-var driven knobs, build + run in one tool.  |
| `deploy/kube/seenoevil.yaml` | Declarative diffs in git, generates systemd units via `podman kube generate systemd` or feeds Quadlet `.kube` units, drop-in path for porting to Kubernetes/k3s later. |

Both produce an identical Podman pod (`seenoevil`) with identical volume names,
so you can switch between them without losing data.

## Generating a systemd Quadlet unit

On a Linux host with systemd, the YAML can be wrapped as a Quadlet `.kube`
unit so the stack auto-starts at boot:

```sh
sudo cp deploy/kube/seenoevil.yaml      /etc/containers/systemd/
sudo tee /etc/containers/systemd/seenoevil.kube <<'EOF'
[Kube]
Yaml=seenoevil.yaml

[Install]
WantedBy=default.target
EOF
sudo systemctl daemon-reload
sudo systemctl start seenoevil.service
```

## Adjusting ports

Every published host port lives on a `hostPort:` line in the manifest:

| Container | `containerPort` | `hostPort` (default) |
|-----------|----------------:|---------------------:|
| caddy     | 80              | 8088                 |
| caddy     | 443             | 8448                 |
| proxy     | 8080            | 8080                 |
| proxy     | 8443            | 8443                 |
| dns       | 53/udp + 53/tcp | 1053                 |

Edit the `hostPort` values directly if the defaults collide with something
already running on the host.

## Caveats vs. the shell script

- `podman kube play` does not honour every Podman-specific option.  Container
  user/uid is taken from the image, restart policy is set on the pod, and
  the `--restart unless-stopped` semantics from the script become Kubernetes'
  `restartPolicy: Always` (close enough — `podman kube play` does not run a
  controller, so `Always` simply means restart-on-failure when the pod is up).
- The `config.example.yaml` is mounted as a `hostPath`, which means `podman
  kube play` must be invoked from the repo root.  In production replace the
  `hostPath` block with a `ConfigMap` or a fully qualified path.
