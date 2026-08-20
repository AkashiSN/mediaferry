# 診断ツール

**mediaferry が前提にしていることを、実機で確かめるための道具です。** 3 つとも
**判定を終了ステータスに返す**ので、「実行できた」ことと「前提が成り立った」ことを
取り違えません。

どれもリポジトリの根から実行します。

| 道具 | いつ回すか |
| --- | --- |
| [`immich_probe.py`](immich_probe.py) | **Immich の版を上げたとき** |
| [`large_upload.py`](large_upload.py) | **大きな動画が送れないとき**、リバースプロキシを挟んだとき |
| [`compose.broker-check.yaml`](compose.broker-check.yaml) | 新しいホストへ導入したとき |

---

## `immich_probe.py` — Immich の版を上げたら回す

mediaferry が対象にしている Immich は **v3.1.0** です。それより新しい版へ上げる
なら、**先にこれを回してください。** 次の 4 点を実際に叩いて確かめます。

1. **転送先を安定して同定できるか**（サーバ識別子と認証ユーザ ID）
   —— 取れないと、API キーを入れ替えただけで全件を送り直すことになります
2. **`bulk-upload-check` が、既にある資産の ID を返すか**
   —— 返らないと「サーバは成功したがこちらに記録が無い」状態から再開できません
3. **上げた資産の `deviceAssetId` を後から読めるか**
   —— 読めないと「自分が上げたもの」と「無関係な重複」を区別できません
4. **checksum の encoding**（hex か base64 か）

```bash
export IMMICH_URL=<url>
export IMMICH_API_KEY=<key>
uv run tools/immich_probe.py --write --cleanup
```

**`--write` を付けると実際に資産を作ります**（`--cleanup` で消します）。捨ててよい
インスタンス、または消してよいアカウントで回してください。

サーバ識別子が再起動をまたいで安定するかも見られます。

```bash
uv run tools/immich_probe.py --identity-out /tmp/identity.json
# Immich を再起動してから
uv run tools/immich_probe.py --identity-baseline /tmp/identity.json
```

**失敗したら、その項目が mediaferry の前提を崩しています。** 何が壊れるかは
上の 1〜4 に書いてあります（詳しくは [`../docs/decisions.md`](../docs/decisions.md)
の「Immich への送信」）。

## `large_upload.py` — 大きな動画が送れないとき

結合後の MP4 は 30 GiB を超えることがあります。**送信が途中で切れる・502 が返る**
ときは、これでどこまで通るかを測れます。

```bash
export IMMICH_URL=<url>
export IMMICH_API_KEY=<key>
uv run tools/large_upload.py --file <大きな MP4> \
  --header-checksum-encoding base64 --bulk-checksum-encoding base64 --cleanup
```

確かめるのは次の 4 点です。

- ファイル全体を実際に転送できるか
- **クライアント側のメモリがファイルサイズに比例しないか**（ストリーミングできているか）
- **リバースプロキシの body size 上限に当たらないか**
- 上げた後にサーバ側で資産として成立しているか

> **公開 URL と内部 URL で結果が変わります。** 実測では、CDN を挟んだ公開 URL は
> 622 MiB で 502 を返し、内部経路では 28.36 GiB が 84.5 秒で完走しました。
> mediaferry が転送先に「接続先 URL」と「表示用 URL」を分けて持つのはこのためです。
> **大きな動画を送るなら、接続先には内部経路を指定してください。**

## `compose.broker-check.yaml` — 新しいホストで権限を確かめる

2 つのコンテナはソケット 1 本でつながり、**接続元の UID を見て拒否します**。
その仕掛けがこのホストで効くかを、**本番と同じ権限条件で**確かめます。

```bash
docker compose -f tools/compose.broker-check.yaml up --build -d mountd
docker compose -f tools/compose.broker-check.yaml run --rm app            # 通るべき
docker compose -f tools/compose.broker-check.yaml run --rm app-wrong-uid  # 弾かれるべき
docker compose -f tools/compose.broker-check.yaml down -v
```

**`app-wrong-uid` が通ってしまったら、UID による拒否が効いていません。** 弾かれれば
`unauthorized: peer uid is not allowed` が出ます。

**`app` の側は、実際に USB が挿さっているホストで回してください。** ボリュームが
1 つも無いと「読めたファイルが 0 件」で FAIL になります（拒否の確認だけなら
`app-wrong-uid` で足ります）。

**app を root で動かして確かめないでください。** root だと `SO_PEERCRED` も
ソケットの GID も検証されず、本番でだけ接続できない状態を見逃します。

---

これらは実装前の実測（Phase 0）に使ったものです。そのときの測定値は
[`../docs/history/phase0-findings.md`](../docs/history/phase0-findings.md) に
残っています。
