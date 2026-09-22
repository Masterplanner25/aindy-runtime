# FR-15 evidence image — the shipped runtime image plus a docker CLI.
#
# NOT a shipped image. The production-safe deployment profiles (`distributed-api`,
# `distributed-worker`) require a container-grade plugin sandbox, and the runtime's probe for
# that is `shutil.which("docker")` (sandbox_runner.py) — the shipped image has no docker binary,
# so a distributed stack from the shipped compose crash-loops on its own (correct) guard. This
# image exists so ONE local topology can satisfy the chain and produce FR-15's evidence: a
# resume crossing the queue to a separate worker PROCESS. It is paired with
# docker-compose.fr15-evidence.yml, which mounts the host's docker socket — the security
# decision DEC-072 records. Read that decision before reusing either file.
ARG RUNTIME_IMAGE=aindy-runtime:2.22.0
FROM ${RUNTIME_IMAGE}

# The static docker CLI (client only — the daemon is the host's, reached through the socket).
# Downloaded on the HOST (`curl -fsSL -o docker/fr15-evidence/docker-cli.tgz
# https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz`) and copied in:
# the build VM's network fetched it at ~7 KB/s and gave up after three hours. Gitignored.
USER root
COPY docker/fr15-evidence/docker-cli.tgz /tmp/docker-cli.tgz
RUN tar -xzf /tmp/docker-cli.tgz -C /tmp     && install -m 0755 /tmp/docker/docker /usr/local/bin/docker     && rm -rf /tmp/docker /tmp/docker-cli.tgz     && docker --version
USER aindy
