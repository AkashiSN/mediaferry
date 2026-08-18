# mediaferry

カメラの SD カードを NAS へ取り込み、分割動画を結合して Immich へ同期する
TrueNAS カスタムアプリ。

| ドキュメント | 内容 |
| --- | --- |
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | **まずここ。** 現在地、確定した判断、環境の癖、次にやること |
| [`CLAUDE.md`](CLAUDE.md) | このリポジトリの規約（コメントと docs の棲み分け、テストの作法） |
| [`docs/design.md`](docs/design.md) | 設計仕様書（正本） |
| [`docs/phase0-findings.md`](docs/phase0-findings.md) | Phase 0 の実測結果 |
| [`docs/phase1-plan.md`](docs/phase1-plan.md) | Phase 1 の実装計画（実行済み） |
| [`docs/phase1-backup.md`](docs/phase1-backup.md) | バックアップとリストア、再構築できる範囲 |
| [`docs/phase1-manual-checklist.md`](docs/phase1-manual-checklist.md) | 実 USB での確認手順 |
| [`docs/phase2-plan.md`](docs/phase2-plan.md) | Phase 2（結合）の実装計画（実行済み） |
| [`docs/phase3-plan.md`](docs/phase3-plan.md) | Phase 3（Immich 同期）の実装計画（実行済み） |

**Phase 3（Immich 同期）まで完了。** 配布可能なリリースではない
（認証と CSRF が入る Phase 4 より前に LAN へ公開しない）。

**対象 Immich は v3.1.0**（Phase 0 で実測した版）。API の形が変わっていたら
`app/src/mediaferry/adapters/immich.py` だけを直せば済むように、呼び出し側は
`ImmichClient` のメソッドしか触らない。

## 構成

| パッケージ | 役割 |
| --- | --- |
| `protocol/` | 2 コンテナが共有するソケットプロトコル |
| `mountd/` | 特権側。USB を read-only でマウントし dirfd を渡す |
| `app/` | 非特権側。取り込み・結合・アップロード |

`mountd` はマウントしたファイルシステムを `open_tree(OPEN_TREE_CLONE)` で
切り離してから元の取り付けを外す。app へ渡すのは切り離されたツリー由来の
dirfd だけで、その `..` はボリュームルートに固定される。通常のマウントでは
`openat(dirfd, "..")` がマウントポイントの親へ抜けてしまい、侵害された app が
mountd の名前空間（ホストから bind した `/dev` を含む）へ到達できる。

## 開発

```bash
uv sync --all-packages   # ワークスペースメンバーを全て入れる
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

`--all-packages` が要るのは、根の `mediaferry-workspace` がメンバーに
依存していないため。素の `uv sync` ではメンバーが venv に入らず、
テストが import に失敗する。

root を要するテストは `-m needs_root`、実 Immich を要するテストは
`-m needs_immich` が付いており、既定では実行されない。

```bash
uv run pytest -m needs_root     # ユーザ名前空間が使える環境でのみ通る

# 実 Immich に当てる。書き込むので、捨ててよいインスタンスを使うこと
MEDIAFERRY_TEST_IMMICH_URL=... MEDIAFERRY_TEST_IMMICH_KEY=... \
  uv run pytest -m needs_immich
```

## 設定

環境固有の値はリポジトリに含めない。`compose.yaml` の `<...>` は
デプロイ時に置き換える。
