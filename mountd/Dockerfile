# 特権側。util-linux (mount, blkid) と exfat ユーティリティだけを入れる。
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends util-linux exfatprogs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY protocol/ /srv/protocol/
COPY mountd/ /srv/mountd/
# protocol を先に入れる。まとめて渡すと pip が mediaferry-protocol を
# PyPI から取ろうとすることがある。
RUN pip install --no-cache-dir /srv/protocol \
    && pip install --no-cache-dir /srv/mountd

ENV MOUNTD_SOCKET=/run/mediaferry/broker.sock \
    MOUNTD_MOUNT_ROOT=/run/mountd/mnt

CMD ["python", "-m", "mountd"]
