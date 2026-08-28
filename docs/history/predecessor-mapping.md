# 前身スクリプト（`dji_workflow.py`）との対応

mediaferry の前に使っていた `dot_local/bin/executable_dji_workflow.py`
（PEP 723 / `uv run --script`）の各オプションが、mediaferry のどこへ移ったかの表。
**現在の仕様は [`../design.md`](../design.md) が正です。**

| 現行 | mediaferry での扱い |
| --- | --- |
| `--sd-mount` | 不要。自動検出 |
| `--dest-base` | `MEDIAFERRY_DATA_ROOT` |
| `--since` | 不要。取り込み済みかは DB で判定する。画面のフィルタとしては残る |
| `--immich-server` / `IMMICH_SERVER` | 転送先プロファイルの `base_url`（§12.3） |
| `IMMICH_API_KEY` | 転送先プロファイルの API キー |
| `--immich-client-timeout` | `MEDIAFERRY_UPLOAD_TIMEOUT_SECONDS` |
| `--immich-concurrency` | **対応なし。** 送信は宛先ごとに 1 本で直列（§9.10） |
| `--device-tag` / `--tag` | プロファイルの `immich.tags` |
| `--split-tolerance` | プロファイルの `merge.tolerance_seconds` |
| `--split-min-size-gib` | プロファイルの `merge.min_part_size_gib` |
| `--ext` | プロファイルの `scan.extensions` |
| `--tz` | `MEDIAFERRY_DEFAULT_TIMEZONE`。プロファイルの `timestamp.timezone` で上書き可 |
| `--dry-run` | 結合プレビューとアップロード確認画面が役割を引き継ぐ |
| `--skip-copy` | 不要。各工程を独立に起動できる |
| `--eject` | 取り込み完了後に自動で dirfd 解放・アンマウント。`POST /volumes/{id}/close` は API に残るが、**画面には操作を置かない**（§13。抜いていいかは文で出す） |
| `--fix-timezone` | プロファイルの `timezone_policy: force_offset` として自動化。ただし既存アセットへの適用は承認制（§9.10） |
| `--yes` | 不要 |
| `upload/` | 廃止。§10 の選択肢の提示規則 |
| `failed_merges/` | 廃止。`merge_group.status = failed` |
| `.rsync-partial` | 廃止。staging + no-clobber 公開 |
| `same_file()` の size + mtime 判定 | `quick_fingerprint` で強化 |

現行スクリプト `dot_local/bin/executable_dji_workflow.py` と
`docs/dji-cheatsheet.md` は、mediaferry が実運用に入るまで残す。

