# --- フロントエンドのビルド -------------------------------------------------
# **実行イメージに node を残さない。** 成果物だけを次の段へ渡す（§16）。
FROM node:24-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json /build/
RUN npm ci
COPY web/ /build/
RUN npm run build

# --- 実行イメージ -----------------------------------------------------------
FROM python:3.12-slim-bookworm

# ffprobe は公開前のメタデータ確定に、ffmpeg は Phase 2 の結合と
# Phase 4 のサムネイル生成に使う。
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY protocol/ /srv/protocol/
COPY app/ /srv/app/
RUN pip install --no-cache-dir /srv/protocol \
    && pip install --no-cache-dir /srv/app

# ビルド済みの画面。`MEDIAFERRY_WEB_ROOT` の既定値がここを指す。
COPY --from=web /build/dist/ /srv/web/

ENV MEDIAFERRY_BROKER_SOCKET=/run/mediaferry/broker.sock

CMD ["python", "-m", "mediaferry"]
