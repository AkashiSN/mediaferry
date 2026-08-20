# 導入手順

TrueNAS SCALE の **Apps → Custom App** に置いて動かします。イメージは公開して
あるので、ビルドは要りません。

所要時間は 15〜30 分ほどです。

- [1. 用意するもの](#1-用意するもの)
- [2. データセットを用意する](#2-データセットを用意する)
- [3. ソケット用のディレクトリを作る](#3-ソケット用のディレクトリを作る)
- [4. 鍵を作る](#4-鍵を作る)
- [5. Custom App として置く](#5-custom-app-として置く)
- [6. 起動を確かめる](#6-起動を確かめる)
- [7. LAN から使えるようにする](#7-lan-から使えるようにする)
- [8. 更新する](#8-更新する)
- [9. バックアップ](#9-バックアップ)

---

## 1. 用意するもの

| | |
| --- | --- |
| TrueNAS SCALE | Apps → Custom App が使えること |
| データセット | 取り込み先。**動画を扱うので容量に余裕を** |
| Immich | **v3.1.0** で確認しています。API キーを 1 つ発行しておきます |
| カードリーダー | USB マスストレージとして見えるもの（カメラ本体の USB 接続でも可） |

Immich の API キーは、Immich の画面で
**アカウント設定 → API Keys → New API Key** から作れます。

## 2. データセットを用意する

取り込み先のデータセットを作り、**その中に 5 つのディレクトリを作ります。**

```bash
# <dataset> は実際のパスに置き換える（例: /mnt/tank/mediaferry）
mkdir -p <dataset>/{library,derived,staging,work,var}
```

| ディレクトリ | 中身 |
| --- | --- |
| `library/` | 取り込んだ元ファイル。**カード上の並びをそのまま保ちます** |
| `derived/` | 結合してできた動画などの派生物 |
| `staging/` | 公開前の一時領域 |
| `work/` | 結合の中間生成物 |
| `var/` | データベース、サムネイル、ログ |

**5 つとも同じデータセットの中に置いてください。** 公開はハードリンクの張り替えで
行うので、`staging/` が `library/` と別のファイルシステムにあると動きません
（起動時に確かめて、違っていればその場で止まります）。

**所有者をアプリの UID に合わせます。** 次の手順で決める UID/GID を使います。

```bash
chown -R <app-uid>:<app-gid> <dataset>
```

## 3. ソケット用のディレクトリを作る

2 つのコンテナはソケット 1 本でつながります。**データセットとは別の場所**に、
ソケット専用のディレクトリを作ってください。

```bash
mkdir -p <sock-path>          # 例: /mnt/tank/apps/mediaferry-sock
chown <app-uid>:<app-gid> <sock-path>
```

UID/GID は既存のユーザに合わせても、専用に作っても構いません。以降の
`<app-uid>` / `<app-gid>` はこの値です（例: `3000:3000`）。

## 4. 鍵を作る

Immich の API キーを暗号化して保存するための鍵です。**転送先を 1 つでも作るなら
必須**で、これを失うと保存済みの API キーは復号できません。

```bash
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

出力を控えておきます（`<base64-32bytes>`）。

## 5. Custom App として置く

[`compose.yaml`](../compose.yaml) を **Apps → Custom App → Install via YAML** に
貼り付け、`<...>` を置き換えます。

| 置き換える箇所 | 入れる値 |
| --- | --- |
| `<host-sock-path>` | 手順 3 で作ったディレクトリ |
| `<host-dataset-path>` | 手順 2 のデータセット |
| `<app-uid>` / `<app-gid>` | 手順 3 で決めた UID/GID |
| `<iana-tz>` | 撮影地のタイムゾーン（例: `Asia/Tokyo`） |
| `<base64-32bytes>` | 手順 4 で作った鍵 |

**`MEDIAFERRY_DEFAULT_TIMEZONE` は DJI のカードを扱うなら必須です。** DJI は動画の
時刻を UTC で書き、撮影地の情報を残さないため、これが無いと**正しい時刻を決められ
ません**。未設定のままだと、誤った時刻で取り込まないように起動時点で止まります。

イメージは既定で `:latest` を指しています。**`latest` はリリースのときだけ動く**
ので、先行版を掴むことはありません。版を固定したい場合は `:1.2.3` のように
書き換えてください。

## 6. 起動を確かめる

コンテナが 2 つとも上がったら、TrueNAS のシェルから確認します。

```bash
curl http://127.0.0.1:8080/api/health
# {"status":"ok","schema_version":16}
```

**この時点ではまだ本体からしか見えません**（`MEDIAFERRY_BIND_HOST` の既定が
`127.0.0.1` のため）。画面を開くには次へ進みます。

うまくいかないときは、まずログを見てください。

| ログに出るもの | 意味 |
| --- | --- |
| `No such file or directory: '/data/staging'` | 手順 2 のディレクトリが足りません |
| `broker.sock` に繋がらない | ソケットのパスか、UID/GID の食い違い（手順 3・5） |
| `DEFAULT_TIMEZONE` が要る旨 | 手順 5 のタイムゾーンが未設定です |
| `MEDIAFERRY_SECRET_KEY が未設定` | 転送先を作ろうとしています。手順 4 の鍵を入れてください |

## 7. LAN から使えるようにする

**パスワードを先に決めてください。** 認証を入れないまま LAN へ出すと、同じネット
ワークの誰でも操作できます（その状態だと起動ログと画面に警告が出ます）。

`compose.yaml` の `app` を次のように変えて、アプリを作り直します。

```yaml
    environment:
      MEDIAFERRY_BIND_HOST: 0.0.0.0          # 127.0.0.1 から変える
      MEDIAFERRY_AUTH_PASSWORD: <password>   # 追加する
    ports:
      - "<host-ip>:<host-port>:8080"         # 追加する
```

**3 つを同時に変えます。** `ports` だけを足しても、コンテナが `127.0.0.1` で
待ち受けている限り届きません。

ブラウザで `http://<host-ip>:<host-port>/` を開くとログイン画面が出ます。

**ホスト名で開きたい場合**は `MEDIAFERRY_TRUSTED_HOSTS` にその名前を入れてください
（IP と `localhost` は最初から通ります）。名前を許可制にしているのは、外部のサイトに
仕込まれた名前から内部のアプリを叩かせる攻撃を防ぐためです。

**権限まわりを確かめたい場合**は、[`tools/compose.broker-check.yaml`](../tools/README.md#composebroker-checkyaml--新しいホストで権限を確かめる)
で「正しい UID なら通り、違う UID なら弾かれる」ことをこのホストで検証できます。

以降の操作は[**使い方ガイド**](user-guide.md)へ。

## 8. 更新する

`:latest` を使っている場合は、イメージを取り直してアプリを作り直します。

```bash
docker compose pull && docker compose up -d
```

**データベースの移行は起動時に自動で走ります。** 版を戻す（ダウングレードする）
経路は用意していないので、**更新の前にバックアップを取ってください**。

**Immich の側を上げるときは、先に前提を確かめてください。**
mediaferry が対象にしているのは v3.1.0 です。

```bash
export IMMICH_URL=<url> IMMICH_API_KEY=<key>
uv run tools/immich_probe.py --write --cleanup
```

何を見ているかは [`tools/README.md`](../tools/README.md) にあります。

## 9. バックアップ

`var/mediaferry.sqlite3` が失われると「どのファイルをどこへ送ったか」の記録が
消えます。ファイル自体は `library/` と `derived/` に残るので、取り込み直しは
できますが、Immich への送信状況は分からなくなります。

**取るもの・復元できる範囲・再構築できるもの**は
[`backup.md`](backup.md) にまとめてあります。
