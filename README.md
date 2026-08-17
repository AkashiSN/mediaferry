# mediaferry

カメラの SD カードを NAS へ取り込み、分割動画を結合して Immich へ同期する
TrueNAS カスタムアプリ。設計は `docs/superpowers/specs/2026-08-17-mediaferry-design.md`。

現在 **Phase 0（スパイク）**。配布可能なリリースではない。

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
cd docker/mediaferry
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
```

## 設定

環境固有の値はリポジトリに含めない。`compose.yaml` の `<...>` は
デプロイ時に置き換える。
