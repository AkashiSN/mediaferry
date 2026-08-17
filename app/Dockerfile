FROM python:3.12-slim-bookworm

WORKDIR /srv
COPY protocol/ /srv/protocol/
COPY app/ /srv/app/
RUN pip install --no-cache-dir /srv/protocol \
    && pip install --no-cache-dir /srv/app

ENV MEDIAFERRY_BROKER_SOCKET=/run/mediaferry/broker.sock

CMD ["python", "-m", "mediaferry.spike_cli"]
