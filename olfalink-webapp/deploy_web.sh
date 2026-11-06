#!/usr/bin/env bash
# Run from the olfalink-webapp directory inside Google Cloud Shell.
set -euo pipefail

PROJECT_ID='project-63246068-15d1-47aa-a4b'
REGION='asia-northeast1'
SERVICE='olfalink'
SECRET='olfalink-aws-read-key'
SA='olfalink-app'
SA_EMAIL="${SA}@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")"
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com iam.googleapis.com --project "$PROJECT_ID"

printf '\nAWS読み取りキーを貼り付けてEnter（入力内容は画面に表示しません）: '
IFS= read -r -s AWS_API_KEY
printf '\n'
trap 'unset AWS_API_KEY' EXIT
if [[ -z "$AWS_API_KEY" ]]; then echo 'キーが未入力です'; exit 1; fi

# IMPORTANT: This is a REAL request to the upstream AWS API, not a stub or fixture.
RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"; unset AWS_API_KEY' EXIT
HTTP_CODE="$(curl --silent --show-error --max-time 65 \
  -o "$RESPONSE_FILE" -w '%{http_code}' \
  -H "x-api-key: ${AWS_API_KEY}" \
  'https://jk8dcwjr0i.execute-api.ap-southeast-2.amazonaws.com/publish')"
if [[ "$HTTP_CODE" != '200' ]]; then
  echo "AWS実接続に失敗しました (HTTP $HTTP_CODE)。キーとAWS側の権限を確認してください。"
  exit 1
fi
python - "$RESPONSE_FILE" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f:
 d=json.load(f)
assert isinstance(d.get('version'),int) or str(d.get('version','')).isdigit(), 'version is missing'
print('AWS実接続OK｜セッション:',d.get('sessions'),'｜測定行数:',d.get('rows'),'｜version:',d.get('version'))
PY

# Verify the actual training Excel is downloadable using the credential.
DATASET_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE" "$DATASET_FILE"; unset AWS_API_KEY' EXIT
DATASET_HTTP="$(curl --silent --show-error --max-time 70 \
  -o "$DATASET_FILE" -w '%{http_code}' \
  -H "x-api-key: ${AWS_API_KEY}" \
  'https://jk8dcwjr0i.execute-api.ap-southeast-2.amazonaws.com/publish/dataset.xlsx')"
if [[ "$DATASET_HTTP" != '200' ]]; then
  echo "AWS Excelの実取得に失敗しました (HTTP $DATASET_HTTP)。"; exit 1
fi
python - "$DATASET_FILE" <<'PYZIP'
import sys, zipfile, os
p=sys.argv[1]
if not zipfile.is_zipfile(p): raise RuntimeError('AWS dataset response is not valid XLSX')
with zipfile.ZipFile(p) as z:
 if 'xl/workbook.xml' not in z.namelist(): raise RuntimeError('Excel workbook.xml not found')
print('AWS実測Excelの取得OK｜サイズ:',round(os.path.getsize(p)/1024/1024,2),'MiB')
PYZIP

if ! gcloud secrets describe "$SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET" --project "$PROJECT_ID" --replication-policy=automatic
fi
CREATED="$(printf '%s' "$AWS_API_KEY" | gcloud secrets versions add "$SECRET" \
  --project "$PROJECT_ID" --data-file=- --format='value(name)')"
VERSION="${CREATED##*/}"
if [[ ! "$VERSION" =~ ^[0-9]+$ ]]; then
  echo 'Secret Managerにキーを登録しましたが、バージョン番号を取得できません。'; exit 1
fi
unset AWS_API_KEY
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA" --project "$PROJECT_ID" \
    --display-name='OlfaLink Cloud Run runtime (AWS read only)'
fi
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role='roles/secretmanager.secretAccessor' >/dev/null

echo 'Cloud Runへ実データ接続版をデプロイします...'
gcloud run deploy "$SERVICE" \
  --source . --project="$PROJECT_ID" --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --set-secrets="OLFALINK_READ_KEY=${SECRET}:${VERSION}" \
  --set-env-vars='APP_MODE=aws,AI_PROVIDER=disabled,LLM_PROVIDER=disabled' \
  --allow-unauthenticated \
  --cpu=2 --memory=2Gi --timeout=240 --concurrency=8 \
  --min-instances=0 --max-instances=1

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" \
  --region="$REGION" --format='value(status.url)')"
echo "OlfaLink Web: $URL"
echo "AWS実接続の確認: $URL/api/v1/integration/status"
echo '注意：AWSの履歴計測データを解析します。現在時刻のリアルタイム測定ではありません。'
