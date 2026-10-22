#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Object-store bootstrap: buckets, the upload-staging expiry, and one SCOPED principal per consumer.
# The single source for both deployments — compose runs it in minio-init, the Helm chart in its
# minio-init Job (through the files/minio-init.sh symlink). Idempotent: it runs on every `up` and
# every `helm upgrade`, and converges rather than failing on what already exists.
#
# The root credential is used HERE ONLY, to create the principals. Every service authenticates with
# its own, scoped to the bucket (or prefix) its job touches, so a leaked service key reaches one
# bucket's worth of data with one service's verbs — not every tenant's models, history and backups:
#
#   principal          bucket(s)              verbs                       consumer
#   spark-checkpoint   checkpoint             read/write/delete           Spark jobs' S3A checkpoint (ADR-0025 dec 11)
#   mlflow-artifacts   artifact               read only                   the MLflow server (artifact proxy; serves reads)
#   tracking-gateway   artifact               read/write/delete           the tracking gateway (ADR-0019, ADR-0030 dec 2)
#   model-read         artifact, tenant_*/uploads/*  read only            ml_service loading admitted uploads (ADR-0030)
#   db-backup          backup                 read/write/delete           the backup job (dump, prune, restore)
#   cold-export-rw     cold                   read/write                  the cold-layer exporter (ADR-0025 dec 11)
#   cold-broker-ro     cold                   read only                   the cold-store broker (ADR-0025 dec 5)
#   tenant-delete-cold cold, tenant_*         list/delete, no read/write  the tenant-delete job (ADR-0025 dec 14)
#
# A principal is provisioned when both its keys are set and skipped (with a line saying so) when
# both are empty; one key without the other is a configuration error and fails the run.
#
# Env: MINIO_ENDPOINT, MINIO_ROOT_ACCESS_KEY, MINIO_ROOT_SECRET_KEY            (required)
#      SPARK_CHECKPOINT_BUCKET, ARTIFACT_BUCKET, BACKUP_BUCKET, COLD_STORE_BUCKET (each optional)
#      UPLOAD_STAGING_PREFIX, UPLOAD_STAGING_EXPIRE_DAYS (expiry on ARTIFACT_BUCKET; optional)
#      <PRINCIPAL>_ACCESS_KEY / <PRINCIPAL>_SECRET_KEY for each principal above (see `principal` calls)
set -eu

: "${MINIO_ENDPOINT:?}" "${MINIO_ROOT_ACCESS_KEY:?}" "${MINIO_ROOT_SECRET_KEY:?}"
ALIAS=store
POLICY_DIR=$(mktemp -d)
trap 'rm -rf "$POLICY_DIR"' EXIT

# The store may still be starting (a Job does not wait on a readiness probe).
tries=0
until mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ROOT_ACCESS_KEY" "$MINIO_ROOT_SECRET_KEY" >/dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -le 60 ] || { echo "object store at $MINIO_ENDPOINT never accepted the admin credential" >&2; exit 1; }
  echo "waiting for the object store at $MINIO_ENDPOINT ..."; sleep 2
done

for bucket in "${SPARK_CHECKPOINT_BUCKET:-}" "${ARTIFACT_BUCKET:-}" "${BACKUP_BUCKET:-}" "${COLD_STORE_BUCKET:-}"; do
  [ -n "$bucket" ] && mc mb -p "$ALIAS/$bucket" >/dev/null && echo "bucket ready: $bucket"
done

# Expire abandoned uploads (ADR-0030). An artifact is staged before it is judged, so unadmitted bytes
# have a home no tenant can address and nothing loads. Admitted and refused uploads are cleared at
# commit; this collects the third case — staged and never committed (a closed laptop is enough) —
# which nothing in the request path ever comes back for. Idempotent by CHECKING: `ilm rule add`
# happily adds a second rule for the same prefix on every run.
if [ -n "${ARTIFACT_BUCKET:-}" ] && [ -n "${UPLOAD_STAGING_PREFIX:-}" ] && [ -n "${UPLOAD_STAGING_EXPIRE_DAYS:-}" ]; then
  if mc ilm rule ls "$ALIAS/$ARTIFACT_BUCKET" 2>/dev/null | grep -q "$UPLOAD_STAGING_PREFIX"; then
    echo "upload-staging expiry already present on $ARTIFACT_BUCKET/$UPLOAD_STAGING_PREFIX"
  else
    mc ilm rule add "$ALIAS/$ARTIFACT_BUCKET" --prefix "$UPLOAD_STAGING_PREFIX" --expire-days "$UPLOAD_STAGING_EXPIRE_DAYS"
    echo "upload-staging expiry set: $ARTIFACT_BUCKET/$UPLOAD_STAGING_PREFIX after ${UPLOAD_STAGING_EXPIRE_DAYS}d"
  fi
fi

# Policy documents. Bucket-level and object-level actions are separate statements because S3
# matches them against different resources (the bucket ARN vs the object ARNs).
rw_policy() {  # full read/write/delete on one bucket — the multipart verbs are what large writes use
  cat <<EOF
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"], "Resource": ["arn:aws:s3:::$1"]},
  {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], "Resource": ["arn:aws:s3:::$1/*"]}
]}
EOF
}
write_policy() {  # put/get/list, no delete — the cold exporter never removes history it archived
  cat <<EOF
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:ListBucket"], "Resource": ["arn:aws:s3:::$1"]},
  {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"], "Resource": ["arn:aws:s3:::$1/*"]}
]}
EOF
}
delete_policy() {  # list and delete under one key pattern, no read or write: it removes, never sees
  cat <<EOF
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:GetBucketLocation"], "Resource": ["arn:aws:s3:::$1"]},
  {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::$1"], "Condition": {"StringLike": {"s3:prefix": ["$2"]}}},
  {"Effect": "Allow", "Action": ["s3:DeleteObject"], "Resource": ["arn:aws:s3:::$1/$2"]}
]}
EOF
}
read_policy() {  # get/list on one bucket, or only under one key pattern of it when $2 is given
  if [ -z "${2:-}" ]; then
    list='"Resource": ["arn:aws:s3:::'"$1"'"]'
    objects="arn:aws:s3:::$1/*"
  else
    list='"Resource": ["arn:aws:s3:::'"$1"'"], "Condition": {"StringLike": {"s3:prefix": ["'"$2"'"]}}'
    objects="arn:aws:s3:::$1/$2"
  fi
  cat <<EOF
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:GetBucketLocation"], "Resource": ["arn:aws:s3:::$1"]},
  {"Effect": "Allow", "Action": ["s3:ListBucket"], $list},
  {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": ["$objects"]}
]}
EOF
}

# principal <name> <ACCESS_VAR> <SECRET_VAR> <bucket> <policy-fn> [policy-args...]
principal() {
  name=$1; access_var=$2; secret_var=$3; bucket=$4; shift 4
  eval "access=\${$access_var:-}; secret=\${$secret_var:-}"
  if [ -z "$access" ] && [ -z "$secret" ]; then
    echo "principal $name: not configured ($access_var unset), skipped"; return 0
  fi
  [ -n "$access" ] && [ -n "$secret" ] || { echo "principal $name: $access_var and $secret_var must be set together" >&2; exit 1; }
  [ -n "$bucket" ] || { echo "principal $name: its bucket is not configured" >&2; exit 1; }
  [ "$access" != "$MINIO_ROOT_ACCESS_KEY" ] || { echo "principal $name: must not reuse the root access key" >&2; exit 1; }
  "$@" > "$POLICY_DIR/$name.json"
  mc admin policy create "$ALIAS" "$name" "$POLICY_DIR/$name.json" >/dev/null
  # `user add` on an existing user resets its secret, so a rotated key converges here.
  mc admin user add "$ALIAS" "$access" "$secret" >/dev/null
  # `attach` refuses a policy that is already attached; converge by checking afterwards instead.
  mc admin policy attach "$ALIAS" "$name" --user "$access" >/dev/null 2>&1 || true
  mc admin user info "$ALIAS" "$access" | grep -q "PolicyName:.*$name" \
    || { echo "principal $name: policy did not attach to its user" >&2; exit 1; }
  echo "principal $name ready on $bucket"
}

principal spark-checkpoint SPARK_CHECKPOINT_ACCESS_KEY SPARK_CHECKPOINT_SECRET_KEY "${SPARK_CHECKPOINT_BUCKET:-}" rw_policy "${SPARK_CHECKPOINT_BUCKET:-}"
# The MLflow server only serves artifact READS (serving resolves models:/ through it). Every write
# goes through the tracking gateway (ADR-0030 dec 2), and the server's proxy endpoints are
# unauthenticated — so its principal holds no write or delete verb to hand them.
principal mlflow-artifacts MLFLOW_STORE_ACCESS_KEY MLFLOW_STORE_SECRET_KEY "${ARTIFACT_BUCKET:-}" read_policy "${ARTIFACT_BUCKET:-}"
principal tracking-gateway TRACKING_STORE_ACCESS_KEY TRACKING_STORE_SECRET_KEY "${ARTIFACT_BUCKET:-}" rw_policy "${ARTIFACT_BUCKET:-}"
principal model-read MODEL_READ_ACCESS_KEY MODEL_READ_SECRET_KEY "${ARTIFACT_BUCKET:-}" read_policy "${ARTIFACT_BUCKET:-}" "tenant_*/uploads/*"
principal db-backup BACKUP_STORE_ACCESS_KEY BACKUP_STORE_SECRET_KEY "${BACKUP_BUCKET:-}" rw_policy "${BACKUP_BUCKET:-}"
# The cold policies keep the names deployments already carry, so a re-run converges in place.
principal cold-export-rw COLD_STORE_ACCESS_KEY COLD_STORE_SECRET_KEY "${COLD_STORE_BUCKET:-}" write_policy "${COLD_STORE_BUCKET:-}"
principal cold-broker-ro COLD_READ_ACCESS_KEY COLD_READ_SECRET_KEY "${COLD_STORE_BUCKET:-}" read_policy "${COLD_STORE_BUCKET:-}"
# Only the tenant-delete job removes cold objects (ADR-0025 dec 14), and only under tenant prefixes.
principal tenant-delete-cold TENANT_DELETE_COLD_ACCESS_KEY TENANT_DELETE_COLD_SECRET_KEY "${COLD_STORE_BUCKET:-}" delete_policy "${COLD_STORE_BUCKET:-}" "tenant_*"
echo "object store ready"
