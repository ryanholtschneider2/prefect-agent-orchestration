{{/*
Expand the name of the chart.
*/}}
{{- define "po.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully-qualified app name. Truncated at 63 chars per DNS-1123 label.
*/}}
{{- define "po.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name + version label.
*/}}
{{- define "po.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels applied to every object.
*/}}
{{- define "po.labels" -}}
helm.sh/chart: {{ include "po.chart" . }}
{{ include "po.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Selector labels (must be stable across upgrades).
*/}}
{{- define "po.selectorLabels" -}}
app.kubernetes.io/name: {{ include "po.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Component-scoped names: <fullname>-<component>
*/}}
{{- define "po.workerName" -}}
{{- printf "%s-worker" (include "po.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "po.serverName" -}}
{{- printf "%s-prefect-server" (include "po.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "po.poolRegisterName" -}}
{{- printf "%s-pool-register" (include "po.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "po.rigClaimName" -}}
{{- if .Values.rig.existingClaim -}}
{{- .Values.rig.existingClaim -}}
{{- else -}}
{{- printf "%s-rig" (include "po.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "po.serverPvcName" -}}
{{- printf "%s-prefect-server" (include "po.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "po.claudeHomePvcName" -}}
{{- printf "%s-claude-home" (include "po.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
ServiceAccount name. Mirrors the helm best-practice helper.
*/}}
{{- define "po.serviceAccountName" -}}
{{- if .Values.worker.serviceAccount.create -}}
{{- default (include "po.workerName" .) .Values.worker.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.worker.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Prefect API URL inside the cluster — points at the in-chart server when
enabled, otherwise leaves an empty string for callers to override via
extraEnv.
*/}}
{{- define "po.prefectApiUrl" -}}
{{- if .Values.prefectServer.enabled -}}
{{- printf "http://%s:%d/api" (include "po.serverName" .) (int .Values.prefectServer.service.port) -}}
{{- end -}}
{{- end -}}

{{/*
Image refs. poolRegister inherits the worker image when its own
repository/tag are blank — keeps the chart from needing an extra image
just to run `prefect work-pool create`.
*/}}
{{- define "po.workerImage" -}}
{{- printf "%s:%s" .Values.worker.image.repository .Values.worker.image.tag -}}
{{- end -}}

{{- define "po.poolRegisterImage" -}}
{{- $repo := default .Values.worker.image.repository .Values.poolRegister.image.repository -}}
{{- $tag  := default .Values.worker.image.tag        .Values.poolRegister.image.tag -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{- define "po.serverImage" -}}
{{- printf "%s:%s" .Values.prefectServer.image.repository .Values.prefectServer.image.tag -}}
{{- end -}}
