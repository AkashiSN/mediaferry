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
- [8. リバースプロキシの後ろに置く](#8-リバースプロキシの後ろに置く)
- [9. アプリ画面に Web UI ボタンを出す](#9-アプリ画面に-web-ui-ボタンを出す)
- [10. 更新する](#10-更新する)
- [11. バックアップ](#11-バックアップ)

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

取り込み先のデータセットを作り、**所有者をアプリの UID に合わせます。** UID/GID は
次の手順で決める値です（例: `3000:3000`）。

```bash
# <dataset> は実際のパスに置き換える（例: /mnt/tank/mediaferry）
chown <app-uid>:<app-gid> <dataset>
```

**中のディレクトリは起動時にアプリが作ります。**

| ディレクトリ | 中身 |
| --- | --- |
| `library/` | 取り込んだ元ファイル。**カード上の並びをそのまま保ちます** |
| `derived/` | 結合してできた動画などの派生物 |
| `staging/` | 公開前の一時領域 |
| `work/` | 結合の中間生成物 |
| `var/` | データベースと、起動の錠（`mediaferry.lock`） |

**5 つとも同じデータセットの中に置いてください。** 公開はハードリンクの張り替えで
行うので、`staging/` が `library/` と別のファイルシステムにあると動きません
（起動時に確かめて、違っていればその場で止まります）。**この 5 つを別々の
データセットに分けないでください** —— アプリが作るのはディレクトリなので、
そのまま従えば分かれません。

所有者を変え忘れると、起動時に**何を実行すればよいかを言って止まります**
（`chown <uid>:<gid> <dataset>`）。所有者の付与だけはアプリにはできません。

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

イメージは既定で `:latest` を指しています。**`latest` は `main` の先頭**です。
版を固定したい場合は `:sha-1234567` のようにコミットのタグへ書き換えてください
（ghcr の Packages で確かめられます）。

**アプリ画面に「Web UI」ボタンを出すなら、貼り付ける前に `x-portals` を入れて
ください**（[手順 9](#9-アプリ画面に-web-ui-ボタンを出す)）。あとから YAML を編集
しても反映されません。

## 6. 起動を確かめる

コンテナが 2 つとも上がったら、TrueNAS のシェルから確認します。

```bash
curl http://127.0.0.1:8080/api/health
# {"status":"ok","schema_version":1}
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
| `起動しない: 同じデータの置き場所（…）を別のプロセスが使っている` | **同じデータセットを見るアプリが既に動いています。** 入れ替えのときは、古い方が完全に止まってから新しい方を起動してください（1 つだけ動かします） |

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
仕込まれた名前から内部のアプリを叩かせる攻撃を防ぐためです。**リバースプロキシを
挟むなら、これだけでは足りません**（[手順 8](#8-リバースプロキシの後ろに置く)）。

**権限まわりを確かめたい場合**は、[`tools/compose.broker-check.yaml`](../tools/README.md#composebroker-checkyaml--新しいホストで権限を確かめる)
で「正しい UID なら通り、違う UID なら弾かれる」ことをこのホストで検証できます。

## 8. リバースプロキシの後ろに置く

`https://<app-fqdn>/` のように名前で開きたい場合は、手順 7 に加えて **2 つ**要ります。
**片方でも欠けると、画面は開くのに操作だけが通らなくなります。**

```yaml
    environment:
      MEDIAFERRY_TRUSTED_HOSTS: <app-fqdn>
      FORWARDED_ALLOW_IPS: <docker-network-cidr>
```

| | |
| --- | --- |
| `MEDIAFERRY_TRUSTED_HOSTS` | 既定で通るのは IP そのものと `localhost` だけ。名前を入れないと **421** で断ります |
| `FORWARDED_ALLOW_IPS` | uvicorn の設定。既定は `127.0.0.1`。合っていないと `X-Forwarded-Proto: https` を無視して scheme を `http` と見なし、ブラウザが送る `https://` の Origin と食い違って **403** |

403 の方は GET が素通りするので、**「画面は出るのにボタンだけ効かない」**という形で
現れます。

### `FORWARDED_ALLOW_IPS` の値は環境ごとに違う

**プロキシのアドレスとは限りません。** ホストの公開ポートへ回り込む経路（プロキシが
同じホストにいる、名前がホストの IP に解決される等）では、Docker が送信元をブリッジの
gateway に付け替えるため、コンテナからは gateway の IP に見えます。

**実測してください。** アプリのアクセスログに、信頼判定を通す前の生の接続元が出ます。

```bash
docker logs -f <container>
```

を流しながら、失敗する操作を 1 回だけ行います。

```
INFO:  10.x.x.1:60220 - "POST /api/auth/login HTTP/1.1" 403 Forbidden
       ^^^^^^^^^ ここに出た IP がそのまま値
```

Docker のアドレスプールごと許可しておくと、アプリを入れ直してサブネットが変わっても
追従します。**プールは TrueNAS 側の設定なので、値は環境ごとに違います。** Docker の
既定から変えてあることもあり、10 系のプールを大きく取っているホストも、172 系の
既定のままのホストもあります。**例をそのまま写さず、必ず自分のホストで確かめて
ください。**

**CIDR は厳密な形で書きます。** ホストビットが立っていると（例: `10.11.0.0/12`）
uvicorn は `ValueError` を握りつぶして文字列のまま登録するので、**どの IP とも
一致しないまま、警告も出さずに効かなくなります**。`ipaddress.ip_network()` が通る形
（この例なら `10.0.0.0/12`）に直してください。

```bash
python3 -c "import ipaddress; print(ipaddress.ip_network('<cidr>'))"
```

プール全体を許可すると、**同じホストの他のコンテナからも `X-Forwarded-For` と
`X-Forwarded-Proto` を詐称できます**。実害はログインの試行回数制限（60 秒に 10 回）を
回避されることです。気になる場合は gateway の IP 1 つに絞り、アプリを入れ直すたびに
見直してください。

### プロキシ側

nginx はどちらのヘッダも**既定では送りません**。

```nginx
location / {
    proxy_pass http://<host-ip>:<host-port>;

    proxy_set_header Host              $host;      # 既定は $proxy_host（＝上流の IP）
    proxy_set_header X-Forwarded-Proto $scheme;    # 既定は送らない
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;

    proxy_buffering off;        # /api/events は SSE。切らないと進捗が止まる
    proxy_read_timeout 1h;
    proxy_http_version 1.1;
}
```

`X-Forwarded-Host` は uvicorn が見ないので、名前は素の `Host` で渡してください。

### つまずいたとき

403 の種類は応答の本文（`{"error":{"code": ...}}`）で分かります。

| 症状 | 見るところ |
| --- | --- |
| 421 `untrusted_host` | `MEDIAFERRY_TRUSTED_HOSTS` に名前が無い。またはプロキシが `Host` を書き換えている |
| 403 `cross_site_request` | `FORWARDED_ALLOW_IPS` が実際の接続元と合っていない。またはプロキシが `X-Forwarded-Proto` を送っていない |
| 403 `csrf_failed` | Cookie 側の問題。画面を再読み込みしても直らなければ Cookie を確認 |
| 進捗が止まる | プロキシのバッファリングと read timeout |

**効いたことの確かめ方**: 直るとアクセスログの IP がブラウザの実 IP に変わります
（`X-Forwarded-For` が信頼された証拠です）。

## 9. アプリ画面に Web UI ボタンを出す

TrueNAS の Apps に出る「Web UI」ボタンは、compose の**トップレベル**の `x-portals`
から作られます。Install via YAML で入れた Custom App でも効きます。

```yaml
x-portals:
  - name: Web UI
    scheme: https
    host: <app-fqdn>
    port: 443
    path: /
```

- 手順 7 のまま（プロキシ無し）なら `scheme: http` / `host: <host-ip>` /
  `port: <host-port>`
- `port` は**ホスト側**の番号です。コンテナの 8080 ではありません
- `host` に `0.0.0.0` は書けません。そのまま URL に組み立てられるだけです
- キーは `name` / `scheme` / `host` / `port` / `path` の 5 つだけ。増やすと検証に
  落ちて、**portals ごと黙って捨てられます**（エラーは出ません）

`x-notes` を書くとアプリ画面に注記が、`x-action-required` で「要対応」の印が出ます。

### 手順 5 の時点で入れてください

**`x-portals` が読まれるのは新規インストールのときだけです。** あとから YAML を編集
して保存しても、**ボタンは出ません**（実機で確認しました）。TrueNAS の更新経路は、
custom app に対して portals の読み直しを飛ばすためです。

```python
# middleware の crud.py: update_internal()
update_app_config(app_name, version, new_values, custom_app=app.custom_app)
if app.custom_app is False:
    # TODO: Eventually we would want this to be executed for custom apps as well
    update_app_metadata_for_portals(app_name, version)
```

custom app のメタデータに portals が書かれるのは、アプリを作るときの一度きりです。

### あとから足すなら

**入れ直すのが確実です。** 状態はすべてバインドマウント先のデータセット（手順 2）に
あるので、**同じ名前・同じパスで入れ直せば失われません**。`app-tmp` はコンテナの
`/tmp` なので捨てて構いません。

入れ直したくない場合は、メタデータを直接書きます。**`portals` は「名前 → URL」の
辞書**で、compose の形とは違うことに注意してください。

```bash
vi /mnt/.ix-apps/app_configs/<app-name>/metadata.yaml
```

```yaml
portals:
  Web UI: "https://<app-fqdn>/"
```

```bash
midclt call -j app.metadata_generate
```

この手当ては**更新では上書きされません**（上書きするのは作成の経路だけ）が、**入れ
直すと消えます**。

> TrueNAS の実装には「Custom App でも portals を取るようにしたい」という TODO と
> 「Custom App では取らないようにすべき」という TODO が両方残っています。挙動は将来
> 変わり得ます。

以降の操作は[**使い方ガイド**](user-guide.md)へ。

## 10. 更新する

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

## 11. バックアップ

`var/mediaferry.sqlite3` が失われると「どのファイルをどこへ送ったか」の記録が
消えます。ファイル自体は `library/` と `derived/` に残るので、取り込み直しは
できますが、Immich への送信状況は分からなくなります。

**取るもの・復元できる範囲・再構築できるもの**は
[`backup.md`](backup.md) にまとめてあります。
