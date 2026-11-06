# OlfaLink v1.1 — button-only 30-second inference

A FastAPI + botanical Web UI for on-demand odor inference. It keeps reading actual ESP32 sensor measurements, but runs the CNN and optional LLM **only when the user presses the button**. Firestore persists incoming samples across Cloud Run restarts. The CNN checkpoint `models/odor_intensity_multitask_v0_7.pt` is included.

See [README_ON_DEMAND_30S.md](README_ON_DEMAND_30S.md) for complete Japanese setup and limitations.

- Deploy with `bash deploy_on_demand.sh` from Cloud Shell after committing to GitHub.
- Configure the AWS ingest Lambda to forward each real measurement via `bridge/aws_lambda_forwarder.py`.
- AWS historical `/publish` is NOT the live streaming source.
- Optional private GPU LLM: see `llm-service/` in the full LoRA package (not in the lightweight ZIP).
- Never commit secrets to GitHub; no safety or chemical constituent claims should be derived from this experimental CNN.
