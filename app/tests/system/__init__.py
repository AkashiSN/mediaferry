"""E2E（Playwright）と手動確認のための土台.

**mock ではなく実物を立てる。** `fetch` を差し替えた E2E は、静的配信・Cookie・
SSE・ジョブの worker を通らない。ここでは本物の FastAPI をサブプロセスで起動し、
ブローカーは実ソケットで待ち受け（マウント部分だけ fake）、Immich も
`app/tests/fake_immich.py` をそのまま 2 台立てる。
"""
