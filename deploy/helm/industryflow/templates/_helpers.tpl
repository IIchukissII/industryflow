{{/*
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
*/}}

{{- define "industryflow.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "industryflow.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "industryflow.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "industryflow.labels" -}}
app.kubernetes.io/part-of: industryflow
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Secret name to reference (created or existing). */}}
{{- define "industryflow.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "industryflow.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Resolve the TimescaleDB host: in-cluster service name or external host. */}}
{{- define "industryflow.dbHost" -}}
{{- if .Values.infra.timescaledb.inCluster -}}
{{- printf "%s-timescaledb" (include "industryflow.fullname" .) -}}
{{- else -}}
{{- required "infra.timescaledb.external.host is required when inCluster=false" .Values.infra.timescaledb.external.host -}}
{{- end -}}
{{- end -}}

{{/* Resolve the Kafka bootstrap servers. */}}
{{- define "industryflow.kafkaBootstrap" -}}
{{- if .Values.infra.kafka.inCluster -}}
{{- printf "%s-kafka:%v" (include "industryflow.fullname" .) .Values.infra.kafka.port -}}
{{- else -}}
{{- required "infra.kafka.external.bootstrap is required when inCluster=false" .Values.infra.kafka.external.bootstrap -}}
{{- end -}}
{{- end -}}

{{/* Resolve the Redis host. */}}
{{- define "industryflow.redisHost" -}}
{{- if .Values.infra.redis.inCluster -}}
{{- printf "%s-redis" (include "industryflow.fullname" .) -}}
{{- else -}}
{{- required "infra.redis.external.host is required when inCluster=false" .Values.infra.redis.external.host -}}
{{- end -}}
{{- end -}}

{{/* Resolve the MinIO endpoint (host:port). */}}
{{- define "industryflow.minioEndpoint" -}}
{{- if .Values.infra.minio.inCluster -}}
{{- printf "http://%s-minio:%v" (include "industryflow.fullname" .) .Values.infra.minio.apiPort -}}
{{- else -}}
{{- required "infra.minio.external.endpoint is required when inCluster=false" .Values.infra.minio.external.endpoint -}}
{{- end -}}
{{- end -}}

{{/* Resolve the MLflow tracking URI. */}}
{{- define "industryflow.mlflowUri" -}}
{{- if .Values.infra.mlflow.inCluster -}}
{{- printf "http://%s-mlflow:%v" (include "industryflow.fullname" .) .Values.infra.mlflow.port -}}
{{- else -}}
{{- required "infra.mlflow.external.trackingUri is required when inCluster=false" .Values.infra.mlflow.external.trackingUri -}}
{{- end -}}
{{- end -}}
