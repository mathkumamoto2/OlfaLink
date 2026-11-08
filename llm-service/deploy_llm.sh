#!/usr/bin/env bash
# OPTIONAL: deploy the real Qwen3.5-2B + user-provided LoRA to Cloud Run GPU.
# Requires GPU quota and incurs GPU charges while instances run.
set -euo pipefail
cd "$(dirname "$0")"
PROJECT_ID='project-63246068-15d1-47aa-a4b'
REGION='asia-southeast1'
SERVICE='olfalink-llm'
WEB_SA="olfalink-app@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud run deploy "$SERVICE" \
  --source . --project="$PROJECT_ID" --region="$REGION" \
  --no-allow-unauthenticated \
  --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu=4 --memory=16Gi --no-cpu-throttling \
  --min-instances=0 --max-instances=1 --concurrency=1 --timeout=300

gcloud run services add-iam-policy-binding "$SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --member="serviceAccount:${WEB_SA}" --role='roles/run.invoker'

URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT_ID" \
  --region="$REGION" --format='value(status.url)')"
gcloud run services update olfalink --project="$PROJECT_ID" --region=asia-northeast1 \
  --update-env-vars="LLM_SERVICE_URL=${URL}"
echo 'Private LLM service deployed; Web service configured to use it.'
