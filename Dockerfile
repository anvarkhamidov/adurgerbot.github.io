FROM denoland/deno:bin AS deno

FROM python:3.12-slim

# ffmpeg merges video+audio; deno is the JS runtime yt-dlp needs for YouTube.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno

RUN useradd --create-home --uid 1000 app \
    && python -m venv /opt/venv \
    && mkdir -p /data/downloads \
    && chown -R app:app /opt/venv /data
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ entrypoint.sh ./

USER app
ENTRYPOINT ["tini", "--", "sh", "/app/entrypoint.sh"]
CMD ["python", "bot.py"]
