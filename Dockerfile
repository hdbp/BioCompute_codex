FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir '.[postgres]' && \
    groupadd --gid 10001 biocompute && useradd --uid 10001 --gid 10001 --create-home biocompute && \
    mkdir -p /app/state /data && chown -R biocompute:biocompute /app/state /data
USER biocompute
EXPOSE 8000
CMD ["uvicorn", "biocompute.app:app", "--host", "0.0.0.0", "--port", "8000"]
