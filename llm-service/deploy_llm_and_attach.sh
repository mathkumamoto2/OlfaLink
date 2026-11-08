#!/usr/bin/env bash
# Deploy a private Qwen3.5-2B + trained LoRA service, then update the existing web app.
set -euo pipefail
cd "$(dirname "$0")"
PROJECT='project-63246068-15d1-47aa-a4b'
LLM_REGION='asia-southeast1'
WEB_REGION='asia-northeast1'
SERVICE='olfalink-llm'
WEB_SERVICE='olfalink'
WEB_SA="olfalink-app@${PROJECT}.iam.gserviceaccount.com"

[[ -s adapter/adapter_model.safetensors ]] || { echo 'LoRA adapter is missing'; exit 1; }
[[ -s ../olfalink-webapp/app/services/odor_narration.py ]] || { echo 'Web app source is missing'; exit 1; }
command -v gcloud >/dev/null || { echo 'Google Cloud CLI is required'; exit 1; }

echo 'This deploys a billed NVIDIA L4 GPU. The service scales to zero but charges while active.'
read -r -p 'Deploy the GPU service? Type YES: ' ANSWER
[[ "$ANSWER" == 'YES' ]] || { echo 'Canceled'; exit 0; }

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# Deploy LLM first: if the GPU quota/build fails, the existing web application remains unchanged.
gcloud run deploy "$SERVICE" \
  --source . \
  --project="$PROJECT" --region="$LLM_REGION" \
  --no-allow-unauthenticated \
  --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu=4 --memory=16Gi --no-cpu-throttling \
  --min-instances=0 --max-instances=1 --concurrency=1 --timeout=300

gcloud run services add-iam-policy-binding "$SERVICE" \
  --project="$PROJECT" --region="$LLM_REGION" \
  --member="serviceAccount:${WEB_SA}" --role='roles/run.invoker'

LLM_URL="$(gcloud run services describe "$SERVICE" \
  --project="$PROJECT" --region="$LLM_REGION" \
  --format='value(status.url)')"
[[ "$LLM_URL" == https://* ]] || { echo 'Invalid service URL'; exit 1; }

# Deploy the patched web app only AFTER the LLM revision and IAM binding succeed.
# --update-env-vars preserves AWS/Firestore/ingest configuration in the existing service.
gcloud run deploy "$WEB_SERVICE" \
  --source ../olfalink-webapp \
  --project="$PROJECT" --region="$WEB_REGION" \
  --service-account="$WEB_SA" \
  --update-env-vars="APP_MODE=on_demand,LLM_SERVICE_URL=${LLM_URL}" \
  --cpu=2 --memory=2Gi --timeout=300 --concurrency=1 \
  --min-instances=0 --max-instances=1 \
  --allow-unauthenticated

WEB_URL="$(gcloud run services describe "$WEB_SERVICE" \
  --project="$PROJECT" --region="$WEB_REGION" \
  --format='value(status.url)')"
printf '\nWeb: %s\nPrivate LLM: %s\n' "$WEB_URL" "$LLM_URL"
echo 'Verify /api/v1/live/status, then click the analyze button with recent live sensor data.'
echo 'The private LLM URL will return 403 without a valid Cloud Run ID token (expected).'
