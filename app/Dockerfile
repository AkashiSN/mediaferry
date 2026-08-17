FROM python:3.12-slim-bookworm

# ffprobe は公開前のメタデータ確定に、ffmpeg は Phase 2 の結合に使う。
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY protocol/ /srv/protocol/
COPY app/ /srv/app/
RUN pip install --no-cache-dir /srv/protocol \
    && pip install --no-cache-dir /srv/app

ENV MEDIAFERRY_BROKER_SOCKET=/run/mediaferry/broker.sock

CMD ["python", "-m", "mediaferry"]
