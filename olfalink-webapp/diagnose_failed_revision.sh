#!/usr/bin/env bash
set -euo pipefail
PROJECT=project-63246068-15d1-47aa-a4b
REVISION="${1:-olfalink-00009-6g6}"
printf 'Collecting Cloud Run revision logs: %s\n' "$REVISION"
gcloud logging read   "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"olfalink\" AND resource.labels.revision_name=\"${REVISION}\""   --project="$PROJECT" --limit=100 --freshness=48h   --format='value(timestamp,severity,textPayload,jsonPayload.message)'
