# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow database-backup image (tier-1 DR).
# postgres:15-alpine ships pg_dump/pg_dumpall/pg_restore matching the pg15 server; aws-cli
# (alpine community) speaks S3 to MinIO or any S3-compatible / off-site bucket. curl and jq speak the
# Docker Engine API for the notebook-volume archives (notebook-backup.sh), with no Docker CLI.
FROM postgres:15-alpine

# gosu (a Go binary the postgres base ships for its entrypoint's privilege drop) is unused
# here — we run the backup script directly, not the postgres entrypoint — and it carries a
# Go-stdlib CRITICAL (CVE-2025-68121). Remove it so the image has no vulnerable Go binary.
# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml): the base
# and the packages below are unpinned, and the persistent build cache would otherwise replay a layer
# holding a package with a fixed CVE. Upgrading the base's packages takes those fixes too.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" \
 && apk upgrade --no-cache \
 && apk add --no-cache aws-cli bash curl jq \
 && rm -f /usr/local/bin/gosu

# Every script, not a list: the scripts source and call each other by name, and a script left off a
# list would fail only when that job runs (tests/test_image_scripts.py).
COPY *.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/*.sh

# Run as a non-root user (uid 10001) with a writable home (aws-cli/mktemp). pg_dump connects
# over TCP, so no local DB privileges are needed.
# The jobs' node-exporter textfile directory on a Docker host (METRICS_TEXTFILE_DIR), owned by the user.
RUN adduser -D -u 10001 -h /home/backup backup \
 && install -d -o backup -g backup -m 0755 /var/lib/industryflow/metrics
USER 10001
ENV HOME=/home/backup

# Default action is a backup run; the CronJob/compose command can override to db-restore.sh.
ENTRYPOINT ["db-backup.sh"]
