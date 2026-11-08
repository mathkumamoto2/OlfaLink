#!/usr/bin/env bash
# Disable expensive GPU; keep deployed models and Web app ready for future presentations.
set -Eeuo pipefail
PROJECT='project-63246068-15d1-47aa-a4b'

echo '=== Stop GPU first to stop ongoing compute charges ==='
gcloud run services update olfalink-llm \
 --project="$PROJECT" --region=asia-southeast1 --scaling=0

echo '=== Prevent the Web app from invoking the inactive GPU ==='
gcloud run services update olfalink \
 --project="$PROJECT" --region=asia-northeast1 \
 --update-env-vars=OLFALINK_LLM_ENABLED=false

echo '[OK] Manual scaling=0: the GPU service is disabled. Web/CNN and other cloud resources may still incur charges.'
