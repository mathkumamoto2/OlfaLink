FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/srv/hf_cache
WORKDIR /srv
COPY requirements.txt .
# GPU-enabled PyTorch wheels are installed from PyPI. Cloud Run supplies the L4 driver.
RUN pip install --no-cache-dir 'torch>=2.6,<3' \
 && pip install --no-cache-dir -r requirements.txt \
 && useradd --create-home --uid 10001 appuser
# Download model weights during build, not on a user request. This makes
# the image large, but avoids runtime remote fetch on each cold start.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='unsloth/Qwen3.5-2B', local_dir='/srv/base', allow_patterns=['*.json', '*.safetensors', '*.model', '*.jinja', '*.txt'])"
COPY adapter ./adapter
COPY src ./src
COPY service.py .
RUN chown -R appuser:appuser /srv
USER appuser
EXPOSE 8080
CMD ["sh","-c","exec uvicorn service:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
