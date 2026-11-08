#!/usr/bin/env bash
# Presentation mode: deploy Tokyo Web, attach existing private LLM, and PREWARM one GPU instance.
# CAUTION: scaling=1 incurs GPU compute charges continuously until stopped.
set -Eeuo pipefail
PROJECT='project-63246068-15d1-47aa-a4b'
WEB_REGION='asia-northeast1'
GPU_REGION='asia-southeast1'
WEB_SERVICE='olfalink'
GPU_SERVICE='olfalink-llm'
SA="olfalink-app@${PROJECT}.iam.gserviceaccount.com"
cd "$(dirname "$0")"

command -v gcloud >/dev/null || { echo '[ERROR] Google Cloud CLI not found. Run in Cloud Shell.' >&2; exit 2; }
if ! gcloud run services describe "$GPU_SERVICE" --project="$PROJECT" --region="$GPU_REGION" >/dev/null 2>&1; then
  echo "[ERROR] ${GPU_SERVICE} could not be described in ${GPU_REGION}; check name, IAM permissions, billing status, and region before redeploying." >&2
  exit 2
fi
LLM_URL="$(gcloud run services describe "$GPU_SERVICE" --project="$PROJECT" --region="$GPU_REGION" --format='value(status.url)')"
case "$LLM_URL" in https://*) ;; *) echo '[ERROR] Could not retrieve LLM HTTPS URL.' >&2; exit 2;; esac

echo '=== 1/4 Deploy OlfaLink v1.9.2 Web (AWS->CNN, demo fallback, UI) ==='
bash ./deploy_aws_data_pull.sh

# A successful deploy is not sufficient: verify the new Web service is accepting
# HTTP requests before enabling billable GPU resources.
WEB_URL="$(gcloud run services describe "$WEB_SERVICE" --project="$PROJECT" --region="$WEB_REGION" --format='value(status.url)')"
echo "[CHECK] Web health: ${WEB_URL}/api/v1/healthz"
curl --fail --silent --show-error --retry 3 --retry-delay 2 --max-time 45   "${WEB_URL}/api/v1/healthz" >/dev/null || {
    echo '[ERROR] Web health check failed. GPU is NOT started.' >&2
    exit 1
  }

echo '=== 2/4 Ensure Web service account may invoke the private LLM ==='
gcloud run services add-iam-policy-binding "$GPU_SERVICE" \
  --project="$PROJECT" --region="$GPU_REGION" \
  --member="serviceAccount:${SA}" --role='roles/run.invoker' >/dev/null

echo '=== 3/4 Enable LLM on the Web app (keep existing settings) ==='
gcloud run services update "$WEB_SERVICE" \
  --project="$PROJECT" --region="$WEB_REGION" \
  --update-env-vars="OLFALINK_LLM_ENABLED=true,LLM_SERVICE_URL=${LLM_URL}" >/dev/null

echo '=== 4/4 START NVIDIA GPU: manual scaling = 1. CHARGES BEGIN NOW. ==='
gcloud run services update "$GPU_SERVICE" \
  --project="$PROJECT" --region="$GPU_REGION" --scaling=1 >/dev/null

echo
printf '[OK] WEB: https://olfalink-15647954758.asia-northeast1.run.app\n'
printf '[OK] LLM service: %s (private; 403 without service ID token is expected)\n' "$LLM_URL"
echo 'IMPORTANT: GPU compute is billable while manual scaling=1.'
echo 'After the presentation: bash stop_server_gpu.sh'
echo 'Real-sensor AWS/JSON and actual LLM generation still require a button-click test.'
