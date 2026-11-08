#!/usr/bin/env bash
# OlfaLink v1.9.2: AWS real measurements, explicit synthetic demo fallback, and dictionary.
set -euo pipefail
PROJECT='project-63246068-15d1-47aa-a4b'
REGION='asia-northeast1'
SERVICE='olfalink'
SA="olfalink-app@${PROJECT}.iam.gserviceaccount.com"
AWS_SECRET='olfalink-aws-read-key'
cd "$(dirname "$0")"

if ! gcloud secrets describe "$AWS_SECRET" --project="$PROJECT" >/dev/null 2>&1; then
  echo "Secret Managerの ${AWS_SECRET} が見つかりません。既存のAWS読み取りキー設定を確認してください。" >&2
  exit 1
fi

gcloud secrets add-iam-policy-binding "$AWS_SECRET" \
  --project="$PROJECT" --member="serviceAccount:$SA" \
  --role='roles/secretmanager.secretAccessor' >/dev/null

# Preserve existing LLM_SERVICE_URL and other environment variables.
gcloud run deploy "$SERVICE" \
  --project="$PROJECT" --region="$REGION" \
  --source . --service-account="$SA" \
  --update-secrets="OLFALINK_READ_KEY=$AWS_SECRET:latest" \
  --update-env-vars='APP_MODE=aws_pull,LIVE_DEVICE_ID=esp1,OLFALINK_DEMO_FALLBACK=true' \
  --allow-unauthenticated --cpu=2 --memory=2Gi --timeout=300 \
  --concurrency=1 --min-instances=0 --max-instances=1

printf '\n完了: https://olfalink-15647954758.asia-northeast1.run.app\n'
printf 'v1.9.2: 負値ADCを0に補正・実測優先＋デモ／辞書フォールバック。解析結果のキャッシュはインスタンス内のみです。GPUサービスは変更しません。\n'
