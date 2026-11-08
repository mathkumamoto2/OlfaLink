"""Show non-sensitive metadata about AWS /data without exposing the API key or readings.

Run in Cloud Shell:
OLFALINK_READ_KEY="$(gcloud secrets versions access latest \
  --secret=olfalink-aws-read-key --project=project-63246068-15d1-47aa-a4b)" \
  python3 tools/check_aws_data.py
"""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ORIGIN = "https://jk8dcwjr0i.execute-api.ap-southeast-2.amazonaws.com/data"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def rows_from_payload(data):
    node = data
    for _ in range(5):
        if isinstance(node, list):
            return node
        if not isinstance(node, dict):
            break
        for key in ("data", "rows", "items", "samples", "readings", "results", "result", "measurements", "records", "points"):
            if isinstance(node.get(key), (list, dict)):
                node = node[key]
                break
        else:
            break
    return None


def main() -> None:
    key = os.environ.get("OLFALINK_READ_KEY")
    if not key:
        raise SystemExit("OLFALINK_READ_KEY がありません。Secret Managerの参照権限を確認してください。")
    now = int(time.time() * 1000)
    query = urlencode({"device": "esp1", "since": now - 120000, "until": now})
    request = Request(ORIGIN + "?" + query, headers={"x-api-key": key, "Accept": "application/json"})
    opener = build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=20) as response:
            data = response.read(2_000_001)
            code = response.status
    except HTTPError as exc:
        raise SystemExit(f"AWS /data HTTP {exc.code}（キー・API権限・パラメータを確認）") from None
    except URLError as exc:
        raise SystemExit("AWSへの通信に失敗しました。ネットワーク状態を確認してください。") from None
    if len(data) > 2_000_000:
        raise SystemExit("AWS応答が大きすぎます。")
    try:
        payload = json.loads(data)
    except ValueError:
        raise SystemExit("AWS応答がJSONではありません。") from None
    records = rows_from_payload(payload)
    print("HTTP:", code)
    print("JSON root type:", type(payload).__name__)
    if isinstance(payload, dict):
        print("Top-level keys:", sorted(str(x) for x in payload.keys()))
    if records is None:
        print("測定値のリストが見つかりません。AWSのレスポンス構造を確認してください。")
        return
    print("Records:", len(records))
    if not records:
        print("対象時間帯にデータがありません。ESP32の送信とAWS側の保持期間を確認してください。")
        return
    first = next((r for r in records if isinstance(r, dict)), None)
    if first is None:
        print("レコードの要素が辞書ではありません。")
        return
    print("Sample field names:", sorted(str(x) for x in first.keys()))
    stamps = []
    for row in records:
        if not isinstance(row, dict):
            continue
        try:
            stamps.append(int(row["epoch_ms"]))
        except (KeyError, ValueError, TypeError):
            continue
    if stamps:
        print("Observed time span (s):", round((max(stamps) - min(stamps))/1000,1))
        print("Latest reading age (s):", round((now - max(stamps))/1000,1))
    else:
        print("epoch_ms列がありません。バックエンド側の時刻フィールド対応が必要です。")


if __name__ == "__main__":
    main()
