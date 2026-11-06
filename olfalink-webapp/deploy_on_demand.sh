#!/usr/bin/env bash
# Deploy from the olfalink-webapp directory in Google Cloud Shell.
set -euo pipefail
PROJECT='project-63246068-15d1-47aa-a4b'
REGION='asia-northeast1'
SERVICE='olfalink'
SA="olfalink-app@${PROJECT}.iam.gserviceaccount.com"
LIVE_SECRET='olfalink-live-ingest-key'
cd "$(dirname "$0")"

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  firestore.googleapis.com --project "$PROJECT" >/dev/null

if ! gcloud firestore databases describe --database='(default)' --project "$PROJECT" >/dev/null 2>&1; then
  echo 'Firestoreの(default)データベースを新規作成します。リージョンは東京です。'
  gcloud firestore databases create --database='(default)' --project "$PROJECT" \
    --location="$REGION" --edition=standard --type=firestore-native
fi

if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  echo 'Cloud Runのサービスアカウントが見つかりません。v0.9の設定を確認してください。' >&2
  exit 1
fi

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" --role='roles/datastore.user' >/dev/null

if ! gcloud secrets describe "$LIVE_SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets create "$LIVE_SECRET" --project "$PROJECT" \
    --replication-policy=automatic >/dev/null
  python3 -c 'import secrets;print(secrets.token_urlsafe(48),end="")' | \
    gcloud secrets versions add "$LIVE_SECRET" --project "$PROJECT" \
    --data-file=- >/dev/null
  echo '転送専用のランダムなキーをSecret Managerに作成しました。'
fi

gcloud secrets add-iam-policy-binding "$LIVE_SECRET" --project "$PROJECT" \
  --member="serviceAccount:$SA" --role='roles/secretmanager.secretAccessor' >/dev/null

# Keep existing OLFALINK_READ_KEY and LLM_SERVICE_URL settings unchanged.
# Cost control: no idle instance; at most one instance and one active inference.
gcloud run deploy "$SERVICE" --source . --project="$PROJECT" --region="$REGION" \
  --service-account="$SA" --update-secrets="LIVE_INGEST_KEY=$LIVE_SECRET:latest" \
  --update-env-vars='APP_MODE=on_demand,LIVE_DEVICE_ID=esp1' \
  --allow-unauthenticated --cpu=2 --memory=2Gi --timeout=180 \
  --concurrency=1 --min-instances=0 --max-instances=1

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region="$REGION" --format='value(status.url)')"
printf '\nOlfaLink: %s\nSensor state: %s/api/v1/live/status\n' "$URL" "$URL"
printf 'AWS Lambdaから転送するまでは、ライブ測定は未接続です。\n'
printf '転送キーはAWS Lambda側で安全に設定し、GitHubへ書かないでください。\n'
