FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /srv
COPY requirements-firestore.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-firestore.txt && useradd --create-home --uid 10001 appuser
COPY app ./app
COPY web ./web
USER appuser
EXPOSE 8080
# Cloud Run injects PORT. Shell expands it at runtime; bind on every interface.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
