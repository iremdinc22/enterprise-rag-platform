FROM python:3.13.3-slim-bookworm@sha256:56a11364ffe0fee3bd60af6d6d5209eba8a99c2c16dc4c7c5861dc06261503cc

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/home/app/.cache/huggingface
WORKDIR /app
COPY requirements.txt requirements-docker.txt ./
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /home/app/.cache/huggingface /demo/uploads \
    && chown -R app:app /home/app/.cache /demo
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.0+cpu \
    && pip install --no-cache-dir -r requirements.txt -r requirements-docker.txt \
    && pip check
COPY --chown=app:app src/ ./src/
COPY --chown=app:app experiments/ ./experiments/
COPY --chown=app:app evaluation/ ./evaluation/
COPY --chown=app:app tests/ ./tests/
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
