# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow database-backup image (tier-1 DR).
# postgres:15-alpine ships pg_dump/pg_dumpall/pg_restore matching the pg15 server; aws-cli
# (alpine community) speaks S3 to MinIO or any S3-compatible / off-site bucket.
FROM postgres:15-alpine

RUN apk add --no-cache aws-cli bash

COPY db-backup.sh db-restore.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/db-backup.sh /usr/local/bin/db-restore.sh

# Default action is a backup run; the CronJob/compose command can override to db-restore.sh.
ENTRYPOINT ["db-backup.sh"]
