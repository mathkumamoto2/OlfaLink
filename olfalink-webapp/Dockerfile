FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /srv
COPY requirements.txt .
# CPU-only PyTorch avoids several GB of unnecessary CUDA dependencies in the web container.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.4,<3' \
 && pip install --no-cache-dir -r requirements.txt \
 && useradd --create-home --uid 10001 appuser
COPY app ./app
COPY ml ./ml
COPY models ./models
COPY web ./web
RUN chmod -R a+rX /srv/app /srv/ml /srv/models /srv/web
USER appuser
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
