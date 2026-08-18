# Graph Report - mediaferry  (2026-08-18)

## Corpus Check
- 168 files · ~160,288 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3041 nodes · 7319 edges · 151 communities (137 shown, 14 thin omitted)
- Extraction: 88% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 841 edges (avg confidence: 0.55)
- Token cost: 91,778 input · 0 output

## Community Hubs (Navigation)
- ffmpeg Merge Runner
- ffprobe & Merge Process
- DB Migration Schemas
- Volume Manifest & Sources
- Destinations/Uploads API Tests
- Volume Service Tests
- Artifact Publisher
- Broker Client
- Immich Client & Leaks
- Destination Repo & Preflight
- Merge Repository
- Profile Matching
- Uploader Tests
- Lease Pulse & Job Store
- Publisher Crash Tests
- Merge Group Detection
- Importer
- Immich Adapter Tests
- Jobs Wiring & Crypto
- DB Connection & Migration Tests
- Preflight Cache & Epoch
- Immich Spikes
- Protocol Errors & Server Tests
- Cancellation Points
- Upload Pair Model
- Job Store
- Device Enumeration
- Upload Claim
- Selection Service
- mountd Server Tests
- Protocol Messages
- Merge Output Naming
- App Startup & Settings
- Timestamp Resolution
- Reconciler Fixtures
- Publish Protocol Constants
- Upload Decisions
- Credential Store
- Schema Constraint Tests
- Clock & Job Leases
- Destination Identity
- Upload Selection Rules
- mountd Entrypoint & Server
- Mount Tests
- Merges REST API
- Group Detector Job
- Repo Conventions & Compose
- Merge & Profile Repositories
- Publisher Lease Tests
- FastAPI App & Migrator
- Clock, Merges & IDs
- Quick Fingerprint
- Profile Model Parsing
- Settings Resolution
- Test Fixtures (conftest)
- API Integration Tests
- Upload Recheck Tests
- Settled Handoff Contracts
- Immich Errors & Uploader
- Destinations REST API
- DJI Osmo Profile
- Upload Approval
- Merges API Tests
- App State & Deps
- Merge Output Path
- Job Runner Loop
- Endpoint URL Validation
- Profile Definition Tests
- Profile Registry
- Upload State Transitions
- Preflight Check
- Privilege Spike CLI
- Publish Protocol Handoff
- Merge Input Digest
- Immediate Transactions & Purge
- Fake Immich Server
- Artifact & Source Schema Tests
- Uploads REST API
- Grouping Boundary Tests
- Phase Plan Documents
- Path & Secret Rules
- Selection Invalidation
- Scanner Tests
- agmsg Review Route
- Namespace Mount Tests
- Filesystem Scan
- Claim & Approval Service
- Namespace Mount Syscalls
- Profile Refs & Detection
- Design: Crypto & Cancellation
- Environment Quirks
- System REST API
- Design: Merge Grouping
- Job World Assembly
- Mount Execution
- Design: Immich Upload
- Mount Manager Lifecycle
- Builtin Profile Loading
- Live Immich Tests
- Merge End-to-End
- Upload Claim CAS
- Design: Claim & Library
- Design: Crash Consistency
- Selection Queries
- Design: Publisher & Privilege
- Design: Device Profiles
- Same-Filesystem Guard
- Dirfd Tree Reader
- Open Beneath Guard
- Design: Privilege Split
- Design: Scanner Model
- Handoff: DB & Mutation Rules
- Handoff: Merge Verification
- Fake Mount System
- Media REST API
- Upload End-to-End
- Scan Job
- Design: Merge Verification
- Design: Profile Revisions
- Phase 3 Plan Index
- Upload Recheck
- Fake Mount Manager
- Mutation Testing Discipline
- Design: Repo & Test Strategy
- Phase 1: Job Store
- Backup & Secret Rules
- Phase 0: Upload URL Findings
- Workspace Packages
- Verification Pass Flag
- App Package Root
- Handoff: Selection Rule
- Phase 0 Deviations
- Backup Recoverability
- Phase 3 Mutation Records
- Protocol Package Root
- Argv Array Rule
- Single DB Connection Rule
- Ruff Excludes docs
- Orphan Files Reporting
- USB Serial Caveat
- Workspace Root

## God Nodes (most connected - your core abstractions)
1. `ProfileRegistry` - 128 edges
2. `JobStore` - 105 edges
3. `now_iso()` - 100 edges
4. `a_media_file()` - 86 edges
5. `ImmichClient` - 74 edges
6. `MergeRepository` - 72 edges
7. `UploadRepository` - 69 edges
8. `ArtifactPublisher` - 58 edges
9. `DestinationRepository` - 58 edges
10. `JobContext` - 56 edges

## Surprising Connections (you probably didn't know these)
- `PreflightCache` --semantically_similar_to--> `prepare_side_effect（副作用の直前の所有権・リース・適格性の再確認）`  [INFERRED] [semantically similar]
  app/src/mediaferry/jobs/preflight.py → docs/phase3-plan.md
- `core / adapters / db / jobs / api の層分け` --conceptually_related_to--> `ArtifactPublisher`  [INFERRED]
  docs/phase1-plan.md → app/src/mediaferry/adapters/publisher.py
- `ストリームの選択はパートごとに作る` --rationale_for--> `MergeRunner`  [EXTRACTED]
  docs/phase2-plan.md → app/src/mediaferry/adapters/ffmpeg.py
- `test_immich_live.py（needs_immich。タグと日時更新を実機で確かめる）` --references--> `ImmichClient`  [EXTRACTED]
  docs/phase3-plan.md → app/src/mediaferry/adapters/immich.py
- `UTC ISO-8601 保存と captured_at の例外` --rationale_for--> `now_iso()`  [EXTRACTED]
  docs/phase1-plan.md → app/src/mediaferry/clock.py

## Import Cycles
- 3-file cycle: `app/src/mediaferry/api/app.py -> app/src/mediaferry/api/routes_destinations.py -> app/src/mediaferry/api/deps.py -> app/src/mediaferry/api/app.py`
- 3-file cycle: `app/src/mediaferry/api/app.py -> app/src/mediaferry/api/routes_devices.py -> app/src/mediaferry/api/deps.py -> app/src/mediaferry/api/app.py`
- 3-file cycle: `app/src/mediaferry/api/app.py -> app/src/mediaferry/api/routes_media.py -> app/src/mediaferry/api/deps.py -> app/src/mediaferry/api/app.py`
- 3-file cycle: `app/src/mediaferry/api/app.py -> app/src/mediaferry/api/routes_merges.py -> app/src/mediaferry/api/deps.py -> app/src/mediaferry/api/app.py`
- 3-file cycle: `app/src/mediaferry/api/app.py -> app/src/mediaferry/api/routes_system.py -> app/src/mediaferry/api/deps.py -> app/src/mediaferry/api/app.py`
- 3-file cycle: `app/src/mediaferry/api/app.py -> app/src/mediaferry/api/routes_uploads.py -> app/src/mediaferry/api/deps.py -> app/src/mediaferry/api/app.py`

## Hyperedges (group relationships)
- **2 コンテナ間の特権境界** — readme_mountd_package, readme_app_package, readme_detached_mount, compose_broker_socket, compose_mountd_allowed_uids, compose_mountd_socket_gid, docs_phase0_findings_dotdot_escape, docs_phase0_findings_so_peercred_rejection [EXTRACTED 1.00]
- **アーティファクト公開の crash protocol に参加する要素** — docs_design_artifactpublisher, docs_design_publish_protocol, docs_design_table_artifact_staging, docs_design_reconciler, docs_design_no_clobber_link, docs_design_deterministic_collision_naming, docs_design_job_lease [EXTRACTED 1.00]
- **宛先の同一性と履歴の引き継ぎを決める要素** — docs_design_table_upload_destination, docs_design_table_destination_revision, docs_design_target_epoch, docs_design_remote_user_id, docs_design_preflight, docs_design_cross_table_fk_invariants, docs_design_remote_user_id_fingerprint [EXTRACTED 1.00]
- **選択から claim・送信までの判定フロー** — docs_design_selection_rules, docs_design_three_layer_predicates, docs_design_selection_rule_field, docs_design_claim_cas, docs_design_invalidated_flag, docs_design_upload_state_machine, docs_design_post_uploads_semantics [EXTRACTED 1.00]
- **§9.3 公開プロトコルに参加するコンポーネント** — app_src_mediaferry_adapters_publisher_artifactpublisher, app_src_mediaferry_jobs_importer_importer, app_src_mediaferry_jobs_reconcile_reconciler, app_src_mediaferry_db_jobs_jobcontext, app_tests_crash_child, docs_phase1_plan_publish_protocol [EXTRACTED 1.00]
- **ボリューム同定と presence 反映の流れ** — app_src_mediaferry_jobs_volumes_volumeservice, app_src_mediaferry_db_sources_upsert_device, app_src_mediaferry_db_sources_resolve_volume_instance, app_src_mediaferry_db_sources_sync_presence, app_src_mediaferry_db_sources_detach_absent, app_src_mediaferry_jobs_volumes_volumeobservation, app_src_mediaferry_core_manifest_content_manifest_digest [EXTRACTED 1.00]
- **プロファイル定義・判定・リビジョン解決** — app_src_mediaferry_core_profiles_model_profiledefinition, app_src_mediaferry_core_profiles_matching_resolve_profile, app_src_mediaferry_db_profiles_profileregistry, app_src_mediaferry_core_profiles_builtin_dji_osmo, app_src_mediaferry_core_profiles_matching_volumefacts, app_src_mediaferry_core_profiles_matching_sourcetree [EXTRACTED 1.00]
- **結合ジョブの順序（claim → probe → merge → verify → record → publish → mark）** — app_src_mediaferry_jobs_merger_merger, app_src_mediaferry_db_merges_claim_for_merge, app_src_mediaferry_adapters_ffmpeg_mergerunner, app_src_mediaferry_core_merge_verify_verify, app_src_mediaferry_db_merges_record_verification, app_src_mediaferry_adapters_publisher_publish_prepared, app_src_mediaferry_db_merges_mark_merged [EXTRACTED 1.00]
- **core/merge の純粋な判断（境界・ダイジェスト・ストリーム・出力名・検証）** — app_src_mediaferry_core_merge_grouping_detect_groups, app_src_mediaferry_core_merge_digest_input_digest, app_src_mediaferry_core_merge_streams_selected_streams, app_src_mediaferry_core_merge_output_merged_rel_path, app_src_mediaferry_core_merge_verify_verify [EXTRACTED 1.00]
- **リースを失わずに公開しきる仕組み** — docs_phase2_plan_lease_pulse, app_src_mediaferry_adapters_publisher__with_lease_pulse, app_src_mediaferry_adapters_publisher__materialise_link, app_src_mediaferry_adapters_publisher__publish, app_src_mediaferry_adapters_publisher_publishcancelled [EXTRACTED 1.00]
- **外部副作用に所有権を要求する一連の guard** — app_src_mediaferry_db_uploads_claim_next, app_src_mediaferry_db_uploads_prepare_side_effect, app_src_mediaferry_jobs_preflight_preflightcache, app_src_mediaferry_db_uploads_advance_owned, app_src_mediaferry_core_lease_pulse_with_lease_pulse [EXTRACTED 1.00]
- **宛先リビジョンと target_epoch の不変条件** — app_src_mediaferry_db_destinations_destinationrepository, docs_phase3_plan_target_epoch, app_src_mediaferry_db_uploads_invalidate_old_epoch, docs_phase3_plan_recheck_current_epoch_only, docs_phase3_plan_credential_purge [EXTRACTED 1.00]
- **API キーを外へ出さないための多層の防御** — app_src_mediaferry_db_credentials_credentialstore, app_src_mediaferry_core_destinations_identity_fingerprint, docs_phase3_plan_no_response_body_in_errors, docs_phase3_plan_api_key_never_returned, docs_phase3_plan_no_redirect_follow [EXTRACTED 1.00]
- **長い処理の間リースを失わないための仕組み一式** — docs_handoff_with_lease_pulse, docs_handoff_publish_prepared, docs_handoff_artifactpublisher, docs_handoff_prepare_side_effect, docs_handoff_lease_staged_txn, docs_handoff_fsync_dir_gap [EXTRACTED 1.00]
- **API キーの平文流出を防ぐ多層の判断** — docs_handoff_remote_user_id_fingerprint, docs_handoff_adapter_identifier_check, docs_handoff_no_redirect_follow, docs_handoff_no_secrets_in_messages, docs_handoff_api_key_aead, docs_handoff_migration_0005 [EXTRACTED 1.00]
- **キュー投入時の判断を実行時まで固定する版固定の仕組み** — docs_handoff_destination_revision, docs_handoff_pinned_profile_revision, docs_handoff_job_presence_params, docs_handoff_selection_rule_immutable, docs_handoff_create_pairs, docs_handoff_claim_rationale_check [INFERRED 0.85]

## Communities (151 total, 14 thin omitted)

### Community 0 - "ffmpeg Merge Runner"
Cohesion: 0.05
Nodes (98): _audio_bitstream(), _escape(), MergeFailed, MergeOutcome, Any, Path, RuntimeError, ffmpeg による結合（§9.8）. concat demuxer を試し、失敗したら TS 経由へ落とす。**保持するストリームの選択は… (+90 more)

### Community 1 - "ffprobe & Merge Process"
Cohesion: 0.05
Nodes (58): _topology_matches, _ts_layout, MergeRunner, プロセスグループ単位で送り、子プロセスを取り残さない., MediaProbe, ProbeResult, Path, メディアの種別と duration の確定. 公開前にメタデータを確定させるため（§9.3 手順 5）、ここで得た結果が そのまま media_file… (+50 more)

### Community 2 - "DB Migration Schemas"
Cohesion: 0.06
Nodes (43): purge_superseded_credentials, app_setting, job, job_event, device_profile, profile_revision, source_device, source_entry (+35 more)

### Community 3 - "Volume Manifest & Sources"
Cohesion: 0.07
Nodes (36): exists_beneath(), dirfd の下にそのパスがあるか. 開けるかどうかで判定する., content_manifest_digest(), ボリュームの中身の軽い要約. 「前回と同じカードか」を推測するために使う。フォーマット直後や別カードへの…, 名前の集合から決定的なダイジェストを作る. 順序に依存しないよう並べ替える。ディレクトリを走査する順序は ファイルシステムによって変わる。, detach_absent(), Connection, ソース側のレコードの upsert. デバイスの同定は (vendor, product_id, product, serial) の組で行う。serial… (+28 more)

### Community 4 - "Destinations/Uploads API Tests"
Cohesion: 0.06
Nodes (43): a_body(), api_db(), fixture, 検証に失敗した編集は、どの欄も反映しない（§12.3）., epoch が進んだら、旧 epoch の未 claim 項目は理由付きで破棄する（§8）., `SECRET_KEY` が無ければ作らせない（§12.3）., 知らない欄を黙って捨てない（利用者は反映されたと思う）., アプリは差し替え無しで fake へ接続する（`base_url` がループバックの実 URL）. (+35 more)

### Community 5 - "Volume Service Tests"
Cohesion: 0.06
Nodes (51): a_swapped_card(), 中身が DJI のファイルであることは「同じカードだ」の証明にならない., 対象を確かめたら dirfd を握り続けない. 明示的に開くまでは閉じておく., release でその場で閉じる. 次のジョブのために取っておかない., observation は媒体の同一性を保証しないので、黙って共有しない., 抜き差しで /dev/sdX が再利用され、別のカードが同じノードに来る., generation は mountd の再起動で 0 に戻る. epoch が無いと偶然一致する., 実行中のワーカーの fd を、API の別スレッドから閉じない. (+43 more)

### Community 6 - "Artifact Publisher"
Cohesion: 0.08
Nodes (34): fsync_dir(), ディレクトリエントリを永続化する. これを怠ると電源断で公開が失われる., ArtifactPublisher, ArtifactRequest, _collision_stamp(), HashingWriter, _is_same_content(), PublishAborted (+26 more)

### Community 7 - "Broker Client"
Cohesion: 0.06
Nodes (29): BrokerClient, BrokerError, BaseException, Exception, Path, socket, mountd との通信. app でソケットを触るのはここだけ. 上位のコードは VolumeHandle の dirfd だけを見る。マウントのパスも…, ソケットと、開いたままのボリューム fd をすべて閉じる. ソケットだけ閉じると、例外で context manager を抜けなかった… (+21 more)

### Community 8 - "Immich Client & Leaks"
Cohesion: 0.07
Nodes (32): _as_object(), ImmichClient, ImmichProtocolError, Any, Path, 向き先の同定に使う（§8）. preflight もこれを叩く., multipart で送る. ファイルはストリーミングで読む., 相手から受け取った識別子を、保存・URL へ使う前に検めた上で返す. **相手は「こちらが読む値」を選べる。** 侵害された Immich は、受け取った… (+24 more)

### Community 9 - "Destination Repo & Preflight"
Cohesion: 0.07
Nodes (30): DestinationNotFound, DestinationRepository, _endpoints(), EpochDecisionRequired, _host_of(), IdentityUnknown, _next_epoch(), Connection (+22 more)

### Community 10 - "Merge Repository"
Cohesion: 0.09
Nodes (35): GroupNotClaimable, MergeRepository, Connection, Row, RuntimeError, **出力と検証結果が揃っていることを DB 側で確かめる。** 揃っていない `merged` 行を作ると、選択肢の側が黙って隠すので 異常が静かに残る。, merging → detected. キャンセルと中断の後始末に使う., 検証不合格の派生物を、中身を見た上で採用する（§10）. (+27 more)

### Community 11 - "Profile Matching"
Cohesion: 0.12
Nodes (35): _count_matching_files(), hint_score(), MatchOutcome, Protocol, ボリュームごとのプロファイル判定. `hints` は候補の順位付けにのみ使い、単独では確定させない。確定は必ず マウント先の中身が `require`…, プロファイル判定の結果. **ボリュームの同定確度 (`identity_confidence`) はここに含めない。**…, 一致した hint の数. 0 なら順位付けに寄与しない., 中身の検証を通った最初のプロファイルを採用する. (+27 more)

### Community 12 - "Uploader Tests"
Cohesion: 0.07
Nodes (40): _an_existing_asset(), _claim_with(), プロファイルの `tag_pre_existing` だけを差し替える., 既定の DJI プロファイルは `tag_pre_existing: true`（design §6）., 自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めた タグを付けない（§9.10）., preflight は claim の後だが、**リモートに触る前**なので pending へ戻す., 送信は成功しても、キャンセル後の commit は通さない（§8）. 通すと、画面はキャンセル済みなのにタグと日時まで進む。, **アップロード成功後の後処理が失敗しても `created_by_us` を降格させない。** 再開時の `bulk-upload-check`… (+32 more)

### Community 13 - "Lease Pulse & Job Store"
Cohesion: 0.09
Nodes (25): BaseException, 中断できない長い処理の間、リースを延ばし続ける. `os.fsync`（30 GiB の直後は数十秒）、ffprobe（timeout がリースと同値）、…, `work` を待ちながら heartbeat を打つ. `also` を渡すと、heartbeat のたびに一緒に呼ぶ（アップロードでは…, with_lease_pulse(), JobContext, Any, 外部への副作用の直前に呼ぶ. 延長はしない., 撮影日時を書き戻してから `complete` にする. **claim を取ってから外部へ触る。** 取らないと、同時に走った却下が `complete`… (+17 more)

### Community 14 - "Publisher Crash Tests"
Cohesion: 0.11
Nodes (41): a_request(), _die_after(), SD をフォーマットして連番が再利用されたケース. 既存は絶対に動かさない., キャンセル済みと表示した後に公開されることを防ぐ., cancelling でも extend_lease が通ってしまうと、この境界が破れる., 確認と遷移が同じ BEGIN IMMEDIATE の中にあることを、書き込みロックで確かめる. 分けると、その隙間に別接続の cancel が commit…, staged 以降は reconciliation が完遂する. 呼び出し元が failed に倒すと二重取り込みになる., 手順 10 まで進んで落ちた行は staged のまま. os.link を試すと必ず失敗する. (+33 more)

### Community 15 - "Merge Group Detection"
Cohesion: 0.08
Nodes (31): _flush(), _gap_seconds(), GroupCandidate, MergePart, 分割録画のグループ検出（§9.7）. 同一録画と判定する条件は 2 つ。直前ファイルの終端（開始時刻 + duration）と 次ファイルの開始時刻の差が…, 2 件以上のパートからなるグループ候補. `gaps` は継ぎ目ごとの差（秒）で、`members[i]` の終端と `members[i+1]` の…, 直前の終端から次の開始までの差. `previous.duration_seconds` は非 None., MergeRepository.save_detected (+23 more)

### Community 16 - "Importer"
Cohesion: 0.07
Nodes (28): CopyCancelled, Importer, ImportFailed, ImportOutcome, NotEnoughSpace, Connection, Path, Row (+20 more)

### Community 17 - "Immich Adapter Tests"
Cohesion: 0.08
Nodes (32): DB は hex で持ち、Immich へは base64 で送る., to_base64_checksum(), a_file(), an_upload(), client(), fixture, 本文を伴う要求は追わない. ファイルは 1 回目で EOF に達している., 件数が合わない応答を黙って読み飛ばさない. (+24 more)

### Community 18 - "Jobs Wiring & Crypto"
Cohesion: 0.10
Nodes (26): ジョブ 1 件ぶんの世界を組み立てる. ジョブごとに DB 接続を開き、JobStore と ArtifactPublisher の両方をそれに 束ねる。手順…, RuntimeError, 転送先 API キーの保存形式. Immich API は可逆な値を要求するのでハッシュ化できない。マスター鍵による AEAD…, 暗号文が別のマスター鍵で作られている. 復号の失敗と区別する。区別しないと、鍵を取り違えた状態で 「壊れた credential」として上書きしてしまう。, 形式が壊れているか、AAD が一致しない., 暗号文に束縛する文脈. 行を別の宛先・別の版へ差し替える攻撃を復号時に検出する。, SecretAad, SecretBox (+18 more)

### Community 19 - "DB Connection & Migration Tests"
Cohesion: 0.08
Nodes (30): Database, Connection, Path, DB ファイルの場所と、そこへの接続の作り方. 接続を保持しない。呼び出し側が自分のスコープで開いて閉じる。, 毎回直す. 緩い権限で作られた既存 DB をそのまま運用しない. WAL と SHM は SQLite が DB ファイルの権限を写して作るが、既に存在する…, _a_destination_revision(), trigger は BEGIN ... END; と書く. スキーマの大半が trigger を使う., 適用済みの版を書き換えると、環境ごとにスキーマが食い違う. (+22 more)

### Community 20 - "Preflight Cache & Epoch"
Cohesion: 0.08
Nodes (31): 接続の検証で観測した向き先. 同一性ではない. **`remote_user_id` は指紋であって観測値そのものではない**…, RemoteIdentity, invalidate_old_epoch, prepare_side_effect（副作用の直前の所有権・リース・適格性の再確認）, stamp_remote / stamp_many（再確認の結果を 1 トランザクションで書く）, PreflightCache, Row, Rechecker (+23 more)

### Community 21 - "Immich Spikes"
Cohesion: 0.10
Nodes (30): IO, bulk_check(), check(), collect_identity(), main(), make_unique_png(), probe_identity(), probe_upload_cycle() (+22 more)

### Community 22 - "Protocol Errors & Server Tests"
Cohesion: 0.11
Nodes (32): イベント配信は専用接続と決めたが Phase 0 では未実装., test_list_volumes_returns_the_volumes(), test_listed_volumes_carry_the_broker_epoch(), test_missing_type_is_rejected(), test_requests_carrying_fds_are_rejected(), test_subscribe_is_not_accepted_in_phase_0(), test_unknown_request_type_is_rejected(), test_unknown_volume_key_is_rejected() (+24 more)

### Community 23 - "Cancellation Points"
Cohesion: 0.10
Nodes (27): MergeCancelled, キャンセル要求を観測して外部プロセスを刈った., PublishCancelled, staged へ進む前にキャンセル要求を観測した. `PublishAborted` の派生にしてあるので、既存の呼び出し側は今までどおり 「durable…, JobWorld.run_merge, work_rel_path(), CapturedAt, MergeRepository.claim_for_merge (+19 more)

### Community 24 - "Upload Pair Model"
Cohesion: 0.11
Nodes (31): 要求そのものが成立しない. **何も作らずに全体を拒否する。**, UploadRequestInvalid, a_destination(), destinations(), profile(), fixture, epoch を進めた後に作った pair は、新しい epoch に属する（§8）., **epoch の読み出しは pair の INSERT と同じトランザクションで行う**（§8）. 外で読むと、読んだ後・書く前に他の書き手が epoch… (+23 more)

### Community 25 - "Job Store"
Cohesion: 0.09
Nodes (28): JobStore, Connection, Row, 「キャンセル済み」と表示した後に公開されることを防ぐ境界., 失効したジョブは reap されて interrupted になる. 復活させない., BEGIN IMMEDIATE の中で確認してから同じ transaction で進める., 期限を見ずに reap すると、正常に走っているジョブを毎周期で殺す., SQLite に行ロックは無い。BEGIN IMMEDIATE の中の条件付き UPDATE で所有権を取る. (+20 more)

### Community 26 - "Device Enumeration"
Cohesion: 0.13
Nodes (31): BlkidProbe, USB product による機体同定（serial は機種既定値）, blkid_probe(), enumerate_volumes(), _is_usb(), _make_volume(), Path, sysfs と blkid から候補ボリュームを列挙する. マウントはしない。この層は読み取りだけを行う。 USB かどうかは sysfs の実パスに… (+23 more)

### Community 27 - "Upload Claim"
Cohesion: 0.11
Nodes (31): ClaimLost, 自分の claim_token では、その行を動かせない. キャンセルされた古いジョブが、新しいジョブの状態を上書きするのを防ぐ。, a_job_row(), a_pending(), fixture, `upload_record.claim_job_id` は job(id) への外部キー. このタスクのテストは job…, `refuse` は state も pending へ戻す. 進行中のまま claim を外せない., 旧 epoch の記録は監査履歴として残す（§8）. (+23 more)

### Community 28 - "Selection Service"
Cohesion: 0.17
Nodes (30): SelectionService, **既定でない根拠**を持つレコードで確かめる. `default` のままだと、`selection_rule` を書き換える変異が「同じ値で上書き」に…, test_a_failed_record_can_be_retried(), a_group(), a_pair(), ids(), supersede の判定は digest の一致とは別に要る. member を持つグループなら trigger が active を落とすので…, `bool("false")` は真になる. `passed` は真の bool のときだけ合格. (+22 more)

### Community 29 - "mountd Server Tests"
Cohesion: 0.13
Nodes (26): a_volume(), expect_from_listed(), FakeMountManager, make_server(), _open_first_volume(), fixture, mountd 再起動をまたいだ古い expect を弾けるようにするため., 世代が呼び出しごとに進むと同一性チェックが常に失敗し、 クライアント任せだと常に成立してしまう。集合の変化に紐づくことを確かめる。 (+18 more)

### Community 30 - "Protocol Messages"
Cohesion: 0.16
Nodes (28): ProtocolError, Exception, expect_from_wire(), _optional(), Any, ブローカープロトコルの要求・応答の型. サーバとクライアントの双方がこのモジュールだけを見る。ここに無い形の メッセージは受け付けない。, open_volume 時にクライアントが「これのはずだ」と主張する内容. サーバはマウントの直前と直後にこれを検証する。デバイスノードは…, _require() (+20 more)

### Community 31 - "Merge Output Naming"
Cohesion: 0.11
Nodes (27): 結合結果の名前と置き場所. `output_name` はプロファイルの値で、置換するのは `{ts}` `{first_seq}` `{last_seq}`…, _render(), candidate_paths(), library_rel_path(), ValueError, ライブラリ内のパスの決め方. デバイス上の相対パスを保つ。この鏡写しの構造は意図的な設計価値で、ユーザが NAS…, `..`・絶対パス・空の構成要素を含むパス., カード上の相対パスを検証して正規形で返す. (+19 more)

### Community 32 - "App Startup & Settings"
Cohesion: 0.11
Nodes (22): _assert_master_key(), Connection, 鍵が無いまま起動すると、資格情報を復号できないジョブが走る., _is_loopback(), _port(), _positive_int(), Connection, ValueError (+14 more)

### Community 33 - "Timestamp Resolution"
Cohesion: 0.14
Nodes (27): _attach_offset(), datetime, RuntimeError, 撮影日時の解決. `force_offset` は、ファイル名または mtime から得た**壁時計**にプロファイルの オフセットを付与する。DJI が…, force_offset なのに TZ がプロファイルにも既定値にも無い., 壁時計にオフセットを付ける. DST の境界は決め打ちで解決し、記録する., resolve_captured_at(), TimezoneUnresolved (+19 more)

### Community 34 - "Reconciler Fixtures"
Cohesion: 0.12
Nodes (26): _Crash, fixture, RuntimeError, setup(), StubProbe, _a_profile(), _an_upload_record(), fixture (+18 more)

### Community 35 - "Publish Protocol Constants"
Cohesion: 0.08
Nodes (28): ArtifactPublisher._materialise_link, ArtifactPublisher._materialise_write, ArtifactPublisher._publish, _with_lease_pulse, ArtifactPublisher.publish_prepared, _size_check, BITRATE_SPREAD_LIMIT, ESTIMABLE_TYPES (+20 more)

### Community 36 - "Upload Decisions"
Cohesion: 0.15
Nodes (26): ImmichRule, datetime_plan(), DatetimePlan, origin_after_upload(), アップロードの判断（§9.10）. HTTP も DB も知らない。**「自分が作った資産か」を証明できるかどうかで、 既存資産を書き換えてよいかが決まる。**, 撮影日時の補正案. `automatic` が偽なら `awaiting_datetime_approval` へ進み、ユーザの明示承認を…, 付けるタグ. **追加操作だけ**で、既存タグは消さない. 自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めた…, `POST /api/assets` の応答から origin を決める. `created` が返れば自分が作ったと確定する。`duplicate`… (+18 more)

### Community 37 - "Credential Store"
Cohesion: 0.11
Nodes (23): CredentialStore, CredentialUnusable, Connection, RuntimeError, 復号できない、または既に破棄されている. **秘密そのものは絶対に含めない。** 画面にも API 応答にも出る。, 新しい版として保存し、その id を返す., **呼び出し側が開いたトランザクションの中で使う。** 宛先の作成・編集は 1 トランザクションで反映する必要がある（§8）ので、 リポジトリ側の…, 送信の直前にだけ呼ぶ. 戻り値をログにも DB にも書かない. (+15 more)

### Community 38 - "Schema Constraint Tests"
Cohesion: 0.17
Nodes (27): a_media_file(), a_destination(), an_upload(), another_revision(), 向き先を変えて epoch を進めた新しいリビジョン., 複合 FK は destination_revision_id が NULL だと効かない., 書き換えられると、INSERT 時の epoch guard も複合 FK も迂回できる., 未来の期限だけが残ると、明示操作しても期限まで claim できなくなる. (+19 more)

### Community 39 - "Clock & Job Leases"
Cohesion: 0.13
Nodes (15): iso(), datetime, UTC の ISO-8601 文字列にする. DB の時刻表現はこれだけ., utcnow(), LeaseLost, RuntimeError, ジョブの永続化と所有権. SQLite に行ロックは無いので、所有権は `BEGIN IMMEDIATE` の中の条件付き…, heartbeat. 期限切れのリースは復活させない. (+7 more)

### Community 40 - "Destination Identity"
Cohesion: 0.11
Nodes (24): fingerprint(), 転送先の向き先を表す**指紋**（§12.3 / §14）. `remote_user_id` は「同じ Immich…, 観測した識別子を指紋にする. 観測できていなければ None のまま., a_destination(), 向き先が分からない設定は保存しない. 保存すると preflight が必ず失敗する宛先ができ、しかも epoch は進んでいる。, 1 回の編集は 1 トランザクション. 継ぎ目で落ちても中途半端にしない., DB を複製・復元した別ライブラリかもしれない. 自動判定しない., test_a_changed_host_with_the_same_user_needs_an_answer() (+16 more)

### Community 41 - "Upload Selection Rules"
Cohesion: 0.15
Nodes (11): _Choice, PairResult, Connection, Row, §10 (b)(c) のどの根拠で選べるかを決める., §10 (a) と、`selection_rule` に対応する (c) を**今の状態で**評価する. claim…, 根拠が成立しなくなった未完了のレコードを無効化する（§10 の多重防御）., グループに紐づく根拠だけを見る. 宛先の有効・無効は claim 時に見る. (+3 more)

### Community 42 - "mountd Entrypoint & Server"
Cohesion: 0.14
Nodes (14): Lister, _allowed_uids(), main(), _socket_gid(), BrokerServer, _error(), MountManagerLike, _peer_uid() (+6 more)

### Community 43 - "Mount Tests"
Cohesion: 0.22
Nodes (26): build(), expect(), クローンを作ったら元の取り付けは即座に外す。名前空間に残さない., 切り離しが効いていない dirfd を app に渡さない., mountd 再起動をまたいだ古い expect を弾く., mount が成立したか不明なときは mountinfo で確かめて後始末する., 外し損ねた取り付けを抱えたまま成功を返さない. クローンが固定されていても、特権側の名前空間に名前付きマウントが残る。…, 列挙できないことを「取り付いていない」と解釈しない. 後条件（元の取り付けを外した）を確認できないまま成功を返さない。 (+18 more)

### Community 44 - "Merges REST API"
Cohesion: 0.13
Nodes (25): _profile_ref, detect(), _found(), get_group(), _group(), list_groups(), _output_or_none(), patch_group() (+17 more)

### Community 45 - "Group Detector Job"
Cohesion: 0.17
Nodes (24): JobWorld.run_detect_groups, GroupDetector, Connection, a_part(), ctx(), profile(), fixture, 写真は候補の列に入れない. 入れると duration を持たないので境界になり、その前後の分割録画が 検出されなくなる。 (+16 more)

### Community 46 - "Repo Conventions & Compose"
Cohesion: 0.10
Nodes (26): コメントと docs の棲み分け, Conventional Commits + 日本語本文, ソース側のパス解決は単一構成要素のみ（O_NOFOLLOW）, compose: app サービス, MEDIAFERRY_BIND_HOST を loopback に固定し ports を書かない, broker.sock（ブローカーソケット）, MOUNTD_ALLOWED_UIDS（SO_PEERCRED による UID 制限）, compose: mountd サービス (+18 more)

### Community 47 - "Merge & Profile Repositories"
Cohesion: 0.12
Nodes (22): 作れたら group_id、既に同じものがあれば None., 現行と違えば新リビジョンを作る. 作ったら True., new_id(), destination(), a_staging(), reconciliation はパスを推測しない。staged になった時点で final_rel_path / content_sha1 /…, test_source_entry_cannot_point_at_a_missing_media_file(), test_staged_rows_must_carry_everything_needed_to_resume() (+14 more)

### Community 48 - "Publisher Lease Tests"
Cohesion: 0.19
Nodes (24): a_merge_request(), a_prepared(), リースより長い走査でも、手順 7 で失効しない., ffprobe の timeout はリースと同値. 囲まないと手順 7 で失効する., 囲んだ処理の例外は、そのまま呼び出し側へ渡す., リースを失っても、走っている処理の完了を待ってから送出する. 待たずに抜けると、残ったスレッドが後から staging へ書き込む。, 手順 4。実体と記録が食い違ったまま staged へ進ませない., test_a_cancelled_hash_scan_leaves_nothing_durable() (+16 more)

### Community 49 - "FastAPI App & Migrator"
Cohesion: 0.14
Nodes (21): create_app(), FastAPI の組み立てと起動時の手順. 起動時に必ず行うこと: 1. マイグレーション適用 2. ビルトインプロファイルの同期 3.…, apply_migrations(), _apply_one(), MigrationError, Connection, Path, RuntimeError (+13 more)

### Community 50 - "Clock, Merges & IDs"
Cohesion: 0.15
Nodes (19): now_iso(), 現在時刻の単一の出所. DB に入る時刻はすべてここを通す。テストは `freeze` で固定した値を使い、 「1…, 結合グループの保存と状態遷移. `status` は detected → merging → merged / failed、および detected /…, ID の採番. uuid4 の hex を使う。ハイフン無しなのは、パスやログに出したときに 選択・コピーしやすいため。, ソースボリュームのスキャン（§9.5）. dirfd 起点で scan.roots 配下を列挙し、既知の source_entry と照合する。…, a_job(), 片方だけ残ると、期限切れ判定が「期限なし」に化ける., SSE は id の昇順で再開するので、ジョブをまたいで単調でなければならない. (+11 more)

### Community 51 - "Quick Fingerprint"
Cohesion: 0.13
Nodes (22): BinaryIO, quick_fingerprint(), スキャン時の同一性判定に使う軽量な指紋. (rel_path, size, mtime) だけだと、SD を再フォーマットして連番が再利用され、…, 読む窓の先頭オフセットを決定的に算出する. 1MiB 以下ならファイル全体を 1 窓として読む。範囲が重なる場合は 重複を除いて昇順で返す。, ドメイン分離子と固定幅のサイズを含めて連結の曖昧さを排除する., window_offsets(), a_file(), 仕様の式 sha1(b"mfq" + u8(version) + u64le(size) + windows) と一致する. (+14 more)

### Community 52 - "Profile Model Parsing"
Cohesion: 0.29
Nodes (23): _bool(), Hints, _mapping(), _parse_hints(), _parse_immich(), _parse_keep_streams(), _parse_merge(), _parse_require() (+15 more)

### Community 53 - "Settings Resolution"
Cohesion: 0.14
Nodes (23): RuntimeError, env で固定されているか、DB へ保存してはいけない項目を書こうとした., SettingLocked, API 応答にもログにも値そのものを出さない., TrueNAS のアプリ設定画面が常に事実と一致するようにするため、env が勝つ., 暗号文と復号鍵が同じバックアップに入ると、暗号化が何も守らなくなる., 書けないだけでなく読みもしない. set() は BOOTSTRAP を弾くが、旧版・手動編集・将来の不具合で行が紛れ込むと、 読む側が拾った瞬間に「鍵は…, UI で TZ を設定した直後の取り込みが古い値を見ないこと. (+15 more)

### Community 54 - "Test Fixtures (conftest)"
Cohesion: 0.12
Nodes (19): anyio_backend(), broker(), client(), data_root(), database(), db(), fake_card(), FakeMountManager (+11 more)

### Community 55 - "API Integration Tests"
Cohesion: 0.09
Nodes (12): _await_job(), 実行中のワーカーの fd を、API の別スレッドから閉じさせない., to_thread のハンドラは task の cancel では止まらない. 待たずに接続と dirfd を閉じると、まだコピー中のスレッドから見て資源が…, volume_instance_id だけだと、抜き差し後に別のカードを取り込みうる（§9.2）., 暗号文と復号鍵が同じバックアップに入ると、暗号化が何も守らなくなる., test_a_job_carries_the_presence_it_was_queued_against(), test_closing_a_volume_a_job_is_holding_is_a_conflict(), test_closing_a_volume_releases_the_handle() (+4 more)

### Community 56 - "Upload Recheck Tests"
Cohesion: 0.12
Nodes (22): _box_of(), 旧 epoch は別ライブラリへ送った履歴. 現行の資格情報で照合しない., **キャンセル済みなら 1 要求も出さない。** 件数だけを見ていると、`users/me` を投げてから止まる実装を見逃す。鍵付きの…, 照合の最中にキャンセルされたら、結果を書かずに降りる. 書くと「キャンセルした」と表示しながらリモートの観測を反映したことになる。, **キャンセルだけでなくリースの失効も見る。** `ctx.cancelled()` はジョブの `status` しか見ない。リースが切れた…, **batch の合間にもキャンセルを見る。** 1 回の照合が 500 件ずつに割れるので、adapter に任せきりにすると、最初の batch…, 進行中の行には所有者がいる. claim を持たない経路では触らない., `stamp_remote` は `complete` の行だけを触る. 再確認の選択側でも絞っているが、**このメソッドは公開されている**ので、… (+14 more)

### Community 57 - "Settled Handoff Contracts"
Cohesion: 0.10
Nodes (24): 承認はジョブ、却下は同期, checksum は base64 に統一, origin = created_by_us の判定, origin が created_by_us でなければ日時補正は承認待ち, detached マウント（open_tree(OPEN_TREE_CLONE) + MNT_DETACH）, 空の DCIM でも正当なボリューム, fake Immich はループバックで実際に listen させる, ImmichClient / adapters/immich.py (+16 more)

### Community 58 - "Immich Errors & Uploader"
Cohesion: 0.13
Nodes (19): CheckOutcome, ImmichAuthFailed, ImmichError, ImmichRedirected, ImmichRejected, ImmichUnavailable, _parsed_check(), RuntimeError (+11 more)

### Community 59 - "Destinations REST API"
Cohesion: 0.21
Nodes (22): archive_destination(), _checked(), create_destination(), edit_destination(), _enqueue(), _fields(), _found(), list_destinations() (+14 more)

### Community 60 - "DJI Osmo Profile"
Cohesion: 0.10
Nodes (23): hints（usb_ids / volume_labels）, immich（tags / tag_pre_existing / fix_datetime_after_upload）, keep_streams（video primary / audio all / timecode true / data false）, merge（tolerance / min_part_size_gib / sequence_pattern）, dji-osmo プロファイル, require（roots / filename_pattern / min_matching_files）, scan.extensions（MP4 / JPG のみ）, timestamp（filename ソース + mtime fallback） (+15 more)

### Community 61 - "Upload Approval"
Cohesion: 0.15
Nodes (21): ApprovalNotPossible, RuntimeError, 承認待ちの解消（§9.10「承認待ちの解消」）. `pre_existing` / `unknown` の資産は、別経路で既にアップロードされ、ユーザが…, 却下はリモートに触らないので、向き先が変わっていても消せる., 却下が先に complete を commit したら、承認はリモートに触らない., 書き換えたか分からないまま complete にしない., キャンセル済みの承認は、向き先の再確認すら投げない（§14）., PUT 中にキャンセルされたら、complete を書かずに承認待ちへ戻す. リモートは変わったかもしれない（そこは止められない）が、**「承認済み」と… (+13 more)

### Community 62 - "Merges API Tests"
Cohesion: 0.10
Nodes (18): api_db(), _bump_revision(), fixture, 破棄は公開済みの media_file を取り残す. supersede が入る Phase 4 で足す., API と同じ DB ファイルを、テスト用の別接続で開く. **接続は共有しない**（トランザクションは接続に属する）。`client` に依存…, `archived` ではなく `merge.enabled = false` で確かめる. archive は `registry.active()`…, プロファイルを編集した状態を作る（新しいリビジョンが現行になる）., **編集してから投入しても、グループが検出されたときの規則で結合する。** 現行を読み直すと、確認画面で見た構成と違う規則で結合される。 (+10 more)

### Community 63 - "App State & Deps"
Cohesion: 0.15
Nodes (19): AppState, conn(), Connection, リクエストからアプリの状態と、そのリクエスト専用の DB 接続を取り出す., リクエストごとに接続を開いて閉じる. トランザクションは接続に属するので、ワーカーと共有するとお互いの トランザクションに入り込む。, マスター鍵から `SecretBox` を作る. 未設定なら 400 で断る（§12.3）. 引数名を `conn` にしないのは、このモジュールの…, secret_box(), state() (+11 more)

### Community 64 - "Merge Output Path"
Cohesion: 0.25
Nodes (19): merged_rel_path(), MergeOutputUndefined, ValueError, 出力名を決められない（連番が読めない、未知のプレースホルダ、範囲外のパス）., _sequence(), _source_parent(), a_part(), a_rule() (+11 more)

### Community 65 - "Job Runner Loop"
Cohesion: 0.17
Nodes (14): JobRunner, 単一の asyncio ワーカー. SQLite の書き込みを 1 本に絞るため、同時に走るジョブは 1 つだけにする。…, 降りるよう伝える. 実際に終わるのは `run_forever()` の完了時. 走っているジョブにはキャンセルを要求する。要求しないと、ハンドラは…, anyio, claim を待っている間に停止要求が来ると、_current がまだ None なので stop() は cancel を打てない.…, to_thread のハンドラは cancel では止まらない. 待たずに資源を閉じない., ハンドラの接続がワーカーの poller と同じだと、claim と publish の トランザクションが混ざる., test_a_cancelled_job_ends_as_cancelled() (+6 more)

### Community 66 - "Endpoint URL Validation"
Cohesion: 0.22
Nodes (17): EndpointRejected, normalize_endpoint(), ValueError, 転送先の接続エンドポイントの検証（§12.4）. `base_url` は mediaferry が実際に接続する先で、`public_url`…, スキーム・userinfo・fragment・ホストのいずれかが要件を満たさない., 受理した URL を正規形で返す. 受理できなければ送出する., parametrize, test_a_fragment_is_refused() (+9 more)

### Community 67 - "Profile Definition Tests"
Cohesion: 0.24
Nodes (18): parse_definition(), a_definition(), parametrize, 綴りを間違えた設定が黙って無視されると、効いていない設定に気づけない., リビジョンの差分検出に使うので、順序は内容だけで決まる必要がある. dataclass のフィールドを並べ替えただけで JSON が変わると、中身が同じ…, マウントルートの外へ抜ける経路を定義から作らせない., test_a_complete_definition_parses(), test_a_filename_source_needs_a_pattern_and_a_format() (+10 more)

### Community 68 - "Profile Registry"
Cohesion: 0.15
Nodes (16): ProfileRegistry, Connection, expected_digest(), Connection, 現行の構成・設定・リビジョンから計算し直した digest. グループが無ければ None。**保存値との比較は呼び出し側が行う。**, 過去データの解釈が変わらないよう、旧リビジョンは残す., test_a_changed_builtin_creates_a_new_revision_and_keeps_the_old_one(), test_archived_profiles_are_not_active() (+8 more)

### Community 69 - "Upload State Transitions"
Cohesion: 0.12
Nodes (10): ValueError, 外部副作用の結果を commit する. **リースも同じ取引の中で確かめる。**, 終端へ倒して claim を外す. **リースも同じ取引の中で確かめる。**, 進行中の状態へ進める. claim は保ったまま. **`expect_state` を必ず渡す。** 外部副作用の結果を commit する時点でも、…, 終端（complete / failed / awaiting）へ倒し、claim を外す. **未来の期限を残したまま終端にしない。**…, 再び claim できる状態へ戻し、claim を外す., claim してから条件を満たさないと分かった行を無効化する（§10 の多重防御）. **`state` も `pending` へ戻す。** `0004`…, 承認の途中で降りる. 承認待ちへ戻して人に見せる. (+2 more)

### Community 70 - "Preflight Check"
Cohesion: 0.15
Nodes (15): PreflightFailed, RuntimeError, 送信前の向き先の再確認（§10）. `destination_revision.remote_user_id` は登録・編集の時点の観測値にすぎない。…, 向き先が変わっている、または確認できない. **そのリビジョンの pair は 1 バイトも送らない。**, _opener(), 長いジョブでは、途中で向き先が差し替わりうる., **相手も id を返さない場合が危ない。** 記録が無いだけなら「観測値と一致しない」で弾けるが、相手が id を返さない 実装だと `None ==…, test_a_different_user_stops_the_revision() (+7 more)

### Community 71 - "Privilege Spike CLI"
Cohesion: 0.20
Nodes (18): _check(), check_dotdot_is_pinned(), check_existing_file_is_read_only(), check_process_privileges(), check_socket_cannot_be_replaced(), check_source_is_read_only(), main(), _proc_status_field() (+10 more)

### Community 72 - "Publish Protocol Handoff"
Cohesion: 0.12
Nodes (19): ArtifactPublisher（公開の 11 手順）, 計画にコードを全部書くとレビューで実際の穴が出る, os._exit による電源断相当の回収試験（11 段）, record_verification / mark_merged は成立条件を DB 側で確かめる, _publish の外の fsync_dir（残っている穴）, JobRunner / _run_one（例外をすべて failed にする）, リース確認と staged 遷移を 1 つの BEGIN IMMEDIATE に入れる, merging のまま残ったグループの回収（merged / detected へ倒す） (+11 more)

### Community 73 - "Merge Input Digest"
Cohesion: 0.23
Nodes (15): DIGEST_VERSION, input_digest(), 結合グループの入力ダイジェスト. 構成ファイルの順序付きの id と sha1、結合設定、プロファイルリビジョンから…, `members` は `(media_file_id, sha1)` を position の順に並べたもの., MergeRule, SelectionService._matching_digests, 結合グループの検出（§9.7 / `detect_groups` ジョブ）. 公開時に確定した `media_file.duration_seconds`…, a_rule() (+7 more)

### Community 74 - "Immediate Transactions & Purge"
Cohesion: 0.11
Nodes (10): immediate(), `BEGIN IMMEDIATE` で書き込みトランザクションを開く. 既定の遅延開始だと、読んでから書きに昇格する時点で他の接続と衝突し、…, 1 件の暗号文を消す. 既に消えていれば 0 を返す., どのリビジョンからも参照されていない版の暗号文を消す. 版管理したまま旧 API キーを持ち続けると、ローテートしても漏洩面が 減らない。監査のために…, 接続に関わらない編集. **リビジョンを増やさない**（§8 の「編集」ではない）., グループが変わったときに、未完了のレコードをまとめて無効化する., epoch を進めた宛先の、旧 epoch の未完了レコードを破棄する（§8）. **`complete` は残す。** 旧 epoch…, 再確認の結果を**まとめて 1 つのトランザクションで**書く. 1 行ずつ commit すると、途中でキャンセルやリースの失効が起きたときに… (+2 more)

### Community 75 - "Fake Immich Server"
Cohesion: 0.14
Nodes (10): FakeImmich, _handler_for(), _parse_multipart(), Any, テスト用の Immich（ループバックで実際に listen する HTTP サーバ）. **実物の httpx で、実物のソケットに対して叩く。**…, multipart/form-data の最小パーサ. 名前と中身だけを取り出す., 状態を持つ最小の Immich. `assets` はチェックサム（base64）から資産 ID への写像。, Phase 3 の完了条件（§20） (+2 more)

### Community 76 - "Artifact & Source Schema Tests"
Cohesion: 0.19
Nodes (17): 旧グループの再構成で active member が復活すると、候補の除外が誤る., active な member の親を付け替えると trigger を迂回できてしまう., active は親の状態の写しなので、片方だけずらせない., 公開前にメタデータを確定させる（§9.3 手順 5）ので、 probe に成功した動画が duration 無しで残ることはない., test_a_group_cannot_supersede_itself(), test_a_media_file_belongs_to_at_most_one_active_group(), test_a_member_cannot_be_moved_into_a_superseded_group(), test_a_superseded_group_cannot_gain_active_members() (+9 more)

### Community 77 - "Uploads REST API"
Cohesion: 0.21
Nodes (16): approve_upload(), create_uploads(), list_uploads(), Any, get, post, **承認はジョブとして実行する**（外部への副作用に所有権が要る。Task 11）., **却下はリモートに触らない**ので同期で終える（Task 11）. (+8 more)

### Community 78 - "Grouping Boundary Tests"
Cohesion: 0.33
Nodes (16): detect_groups(), `parts` は開始時刻の昇順であること. 並べ替えは呼び出し側の責務., a_part(), a_rule(), test_a_boundary_does_not_stop_the_scan(), test_a_disabled_rule_detects_nothing(), test_a_failed_probe_is_a_boundary(), test_a_gap_beyond_the_tolerance_splits_the_group() (+8 more)

### Community 79 - "Phase Plan Documents"
Cohesion: 0.14
Nodes (17): _is_thumbnail, docs/design.md（設計の正本）, docs/phase0-findings.md（実測で確定した事項）, phase1-manual-checklist.md（実 USB 手動確認 11 項目）, mediaferry Phase 1 実装計画, Phase 1 の完了条件（§20）, crash consistency テスト一式（22 ケース）, Phase 1 でやらないこと（意図的な除外） (+9 more)

### Community 80 - "Path & Secret Rules"
Cohesion: 0.13
Nodes (17): timezone_policy: force_offset, 環境固有の値をリポジトリに含めない, 時刻は UTC ISO-8601 で保存（mediaferry.clock）, 転送先を環境変数で設定しない, 相手から受け取った識別子は adapter の境界で検める, API キーは AEAD で暗号化（envelope とは呼ばない）, create_pairs は現行リビジョンを INSERT と同じトランザクションで読む, destination_revision / target_epoch で宛先の版を固定する (+9 more)

### Community 81 - "Selection Invalidation"
Cohesion: 0.15
Nodes (17): group_is_current(), §10 (a) の derived 条件. claim 時と一覧の両方がこれを使う. supersede されておらず、その media_file…, derived の生成元が現行と一致しなくなったレコードを止める., 送信済みの履歴は無効化しない（監査に要る）., test_a_completed_record_keeps_its_history_even_if_the_group_changed(), test_a_record_whose_grounds_are_gone_is_invalidated(), a_derived(), 一覧の判定と claim 側の判定が食い違わない. (+9 more)

### Community 82 - "Scanner Tests"
Cohesion: 0.12
Nodes (12): fixture, スキャンしただけの行を「取り込み済み」と報告すると、永久に取り込まれない., 16GiB のカードは 1 スキャンがリース (60 秒) より長くなりうる. heartbeat を打たないと、途中で失効して reap…, SD をフォーマットして連番が再利用されたケース., 版を上げる意味は「前の版の判定を信用しない」こと. 指紋の文字列が一致しているかどうかで版の検査を代用すると、算出方法を…, scanning(), test_a_reused_filename_with_different_content_is_new_again(), test_an_entry_that_was_never_imported_is_still_new() (+4 more)

### Community 83 - "agmsg Review Route"
Cohesion: 0.14
Nodes (17): agmsg（codex への経路）, 2026-08-18 の agmsg リセット（旧レビュー履歴は消失）, 認証を既定 off のままにする, codex-bridge-launcher.sh（bridge の寿命管理）, codex によるレビューの巡回, delivery.sh status / status=forced は片付けの証明にならない, docs/design.md（設計仕様書・正本）, despawn.sh --force を最初から付ける (+9 more)

### Community 84 - "Namespace Mount Tests"
Cohesion: 0.12
Nodes (11): 実際にマウントして、切り離したクローンで `..` が固定されることを確かめる. ユーザ名前空間を切って動かすので、root でなくても実行できる環境が多い。…, 通常のディレクトリは親へ抜けられる。これが塞ぐべき穴。, 検証できなかったことを「固定されている」と解釈しない. fail-open だと、一時的な I/O エラーで最終ガードと実機試験が同時に…, 読めなかったことを「取り付いていない」と解釈しない., /run/mediaferry-other が /run/mediaferry の配下と誤認されないこと., test_detached_clone_pins_dotdot_and_survives_detach(), test_dotdot_check_fails_closed_when_stat_errors(), test_mountpoints_under_does_not_match_sibling_prefixes() (+3 more)

### Community 85 - "Filesystem Scan"
Cohesion: 0.19
Nodes (14): _extension(), FoundFile, iter_media_files(), dirfd を起点にした読み取り. パス解決には常に単一のパス構成要素だけを使い、`..`・絶対パス・シンボリック…, scan.roots の下から scan.extensions に一致するファイルを列挙する., _walk(), fixture, カード上の名前と、呼び出し側が渡す拡張子の両方で大小文字を問わない. (+6 more)

### Community 86 - "Claim & Approval Service"
Cohesion: 0.15
Nodes (12): advance_owned / finish_owned（commit 時に ctx.assert_lease）, claim_next（BEGIN IMMEDIATE の CAS で所有権を取る）, release_interrupted（中断した claim を needs_recheck へ解放）, ApprovalService, Connection, Row, claim を持たない行なので、状態だけを動かす., **リモートを一切変更せずに** `complete` にする. (+4 more)

### Community 87 - "Namespace Mount Syscalls"
Cohesion: 0.15
Nodes (15): dirfd_from_tree(), dotdot_is_pinned(), _errno_error(), mountpoints_under(), open_tree_clone(), Path, マウントを名前空間から切り離して扱うための syscall ラッパ. 通常どおりマウントしたディレクトリの dirfd を渡すと、`openat(dirfd,…, `path` のマウントを複製し、どこにも接続されていないツリーの fd を返す. (+7 more)

### Community 88 - "Profile Refs & Detection"
Cohesion: 0.20
Nodes (8): ProfileRef, Row, _to_ref(), UnknownProfile, DetectOutcome, 閾値を変えたときの候補. **保存しない**（§11 の `/merge-groups/preview`）., アクティブな member を境界にして、連続した並びの断片に分ける., LookupError

### Community 89 - "Design: Crypto & Cancellation"
Cohesion: 0.16
Nodes (15): マスター鍵による AEAD 暗号化（API キーの保存）, 協調的キャンセル, 古い資格情報の破棄 (purged_at), テーブル間の不変条件を DB 制約で守る, 転送先プロファイル, JobRunner, 秘密として扱う範囲, 設定の優先順位 env > DB > 既定値 (+7 more)

### Community 90 - "Environment Quirks"
Cohesion: 0.15
Nodes (15): docs/HANDOFF.md, 検出は「アクティブな member」を境界として扱う, disposition.attached_pic（埋め込みサムネイルの判別）, 開発コンテナ（入れ子の非特権 LXC）と AppArmor, /dev を汚さない（loop デバイス 104 万個の事故）, docs/phase0-findings.md（実測結果と設計への反映）, Phase 1: 基盤 + 取り込み, docs/phase1-backup.md（バックアップとリストア） (+7 more)

### Community 91 - "System REST API"
Cohesion: 0.29
Nodes (13): cancel_job(), get_job(), health(), _job(), job_events(), list_jobs(), list_profiles(), list_settings() (+5 more)

### Community 92 - "Design: Merge Grouping"
Cohesion: 0.15
Nodes (14): dji_workflow.py (現行スクリプト), GroupDetector, Immich v3.1.0（対象バージョン）, input_digest, invalidated_at / invalidated_reason（直交フラグ）, mediaferry, 結合グループの検出条件, POST /uploads の pair 意味論 (+6 more)

### Community 93 - "Job World Assembly"
Cohesion: 0.29
Nodes (6): _fixed_profile(), JobWorld, _profile_ref(), Any, Connection, params に固定したリビジョンを読む. 現行リビジョンを読み直すと、キューで待っている間にプロファイルを 編集しただけで、確認画面と違う規則で処理される。

### Community 94 - "Mount Execution"
Cohesion: 0.21
Nodes (11): CompletedProcess, _matches(), _Mounted, MountFailed, MountRejected, Exception, allowlist と expect 検証を伴う detached read-only マウント.…, 検証してからマウントし、(handle, dirfd) を返す. `verify` はマウント直後にもう一度ボリュームを観測して返す呼び出し可能物。… (+3 more)

### Community 95 - "Design: Immich Upload"
Cohesion: 0.17
Nodes (13): awaiting_datetime_approval（承認待ち）, POST /api/assets/bulk-upload-check による重複判定, チェックサム encoding を base64 に統一, ImmichClient, origin 判別 (created_by_us / pre_existing / unknown), 孤立ファイルを自動削除しない, Rechecker (状態の再確認), remote_user_id は指紋 (SHA-256) で保存する (+5 more)

### Community 96 - "Mount Manager Lifecycle"
Cohesion: 0.22
Nodes (7): MountManager, Path, dirfd を閉じる。取り付けは既に外れているので失敗しない., target が取り付けられていれば外し、ディレクトリを消す. 列挙も detach も失敗を伝播する。成功経路ではこれを使う。, 例外処理の途中で呼ぶ後始末。元の例外を隠さないよう握り潰す. 握り潰した場合も残骸として記録し、次回起動の reap_stale で回収する。, 前のプロセスが残した取り付けとディレクトリを回収する. 列挙も detach も**失敗したら例外を送出する**。回収できないまま…, Runner

### Community 97 - "Builtin Profile Loading"
Cohesion: 0.21
Nodes (9): dji-osmo.yaml（ビルトインプロファイル定義）, definition_to_json(), load_builtin_definitions(), ProfileDefinition, DB へ入れる正規形. 差分検出に使うのでキー順を固定する., プロファイルとリビジョンの解決. 編集は既存定義を書き換えず新しいリビジョンを作る。取り込み・結合・アップロードの 各レコードが使用したリビジョン ID…, 定義が変わったビルトインの slug を返す., 地域固定の値をリポジトリに含めない。TZ は設定で与える（§12.2）. (+1 more)

### Community 98 - "Live Immich Tests"
Cohesion: 0.20
Nodes (9): _a_unique_jpeg(), _cleanup(), client(), fixture, 実 Immich に対する疎通確認. 環境変数 `MEDIAFERRY_TEST_IMMICH_URL` と…, 最小の有効な JPEG。中身は毎回変える（既存資産と重複させない）., 作った資産とタグを消す. **消せなければ送出する。**, **upload → 照合 → タグ → 日時 → 後片付け**を実機で通す. タグと日時のエンドポイントは Phase 0 で実測していない（Task… (+1 more)

### Community 99 - "Merge End-to-End"
Cohesion: 0.24
Nodes (11): a_merger(), library(), make_clip(), plenty_of_space(), fixture, 検出 → 結合 → 公開 → 採用 → 選択肢まで通す. **合成クリップではサイズ検査が必ず不合格になる。** lavfi の低ビットレートな MP4…, 空き容量の検査を通す. `library` は `min_part_size_gib` の判定を満たすために `size_bytes` へ 16 GiB…, 検出の閾値を満たすように、size_bytes には 16 GiB を書く. 実体は小さいクリップのまま。`min_part_size_gib` の判定は… (+3 more)

### Community 100 - "Upload Claim CAS"
Cohesion: 0.18
Nodes (7): _expiry(), NoLongerEligible, RuntimeError, CAS で 1 件だけ所有権を取る. 取れなければ None. **`SELECT ... FOR UPDATE` は無い。** 更新できた 1…, 外部への副作用の直前に呼ぶ（§8）. **リースと claim を 1 つの `BEGIN IMMEDIATE` の中で確かめる。** 分けると…, `awaiting_datetime_approval` → `fixing_datetime` を CAS で取る. 却下と競合したら 0 行になる（先に…, 送る直前に §10 の根拠が崩れていた. 送らずに見送る.

### Community 101 - "Design: Claim & Library"
Cohesion: 0.20
Nodes (11): claim — BEGIN IMMEDIATE + 条件付き UPDATE (CAS), claim の保持と解放（3 欄 all-null / all-non-null）, library/ の鏡写し構造（不変・追記のみ）, no-clobber 公開 (os.link), DB に絶対パスを保存しない, レビュー記録（codex 4 巡）, selection_rule（不変の選択根拠）, ローカル同一性への SHA-256 追加を採用しない (+3 more)

### Community 102 - "Design: Crash Consistency"
Cohesion: 0.22
Nodes (11): crash consistency と回収, 衝突時の決定的な別名系列, ジョブのリース (lease_token / lease_expires_at), with_lease_pulse（中断できない長い処理の心拍）, 公開前にメタデータを確定させる, needs_recheck 状態, アーティファクトの公開プロトコル（§9.3 の 11 手順）, Reconciler (+3 more)

### Community 103 - "Selection Queries"
Cohesion: 0.22
Nodes (7): Row, アップロードの選択肢（§10）. 「既定で選択肢に出す」条件をここ 1 か所に置く。画面・API・ワーカーが同じ 定義を使うためで、写しを作らない。…, 現行の構成・設定・リビジョンから計算し直し、一致した group を返す., `passed` が真の bool のときだけ合格. `bool(value)` にすると、`"passed": "false"` のような文字列まで合格に…, **返す件数に上限を置く。** 数万件の一覧を 1 応答に詰めない. 呼び出し側は `len(result) == limit`…, Selectable, _verification_passed()

### Community 104 - "Design: Publisher & Privilege"
Cohesion: 0.22
Nodes (10): ArtifactPublisher, base_url は直接到達できる内部アドレス, confused deputy の回避, dirfd 起点の単一構成要素パス解決 (O_NOFOLLOW), Importer, redirect を追わない（x-api-key の漏洩防止）, docs/phase0-findings.md, docs/phase1-backup.md (+2 more)

### Community 105 - "Design: Device Profiles"
Cohesion: 0.27
Nodes (10): AUTO_IMPORT=trusted と信頼登録, canon-eos ビルトインプロファイル, content_manifest_digest, デバイスプロファイル, DJI Osmo Pocket 4（2 ボリューム構成）, dji-osmo ビルトインプロファイル, generic-dcim フォールバックプロファイル, hints と require の分離（マッチ規則） (+2 more)

### Community 106 - "Same-Filesystem Guard"
Cohesion: 0.25
Nodes (9): assert_same_filesystem(), CrossDeviceLayout, Path, RuntimeError, staging と公開先が同じファイルシステムにあることを起動時に確かめる. 公開は `os.link` による原子的操作である必要がある。別デバイスにあると…, staging と公開先が別のファイルシステムにある., 別デバイスだと os.link が EXDEV で必ず失敗する. 起動時に気づく., test_same_filesystem_check_passes_for_one_dataset() (+1 more)

### Community 107 - "Dirfd Tree Reader"
Cohesion: 0.25
Nodes (5): DirfdTree, `resolve_profile` に渡す読み取り専用の窓., root 配下のファイル名を（サブディレクトリも辿って）返す. DJI は DCIM/DJI_001/ の下に置くので、直下だけを見ると 0 件になる。, DJI は DCIM/DJI_001/ の下にファイルを置く. 直下だけ見ると 0 件になる., test_the_tree_view_walks_into_subdirectories()

### Community 108 - "Open Beneath Guard"
Cohesion: 0.25
Nodes (9): EscapeAttempt, open_beneath(), ValueError, dirfd の下のファイルを開く. 中間ディレクトリも 1 段ずつ辿る., parametrize, test_open_beneath_reads_through_the_dirfd(), test_open_beneath_refuses_to_escape(), test_symlinks_are_not_followed() (+1 more)

### Community 109 - "Design: Privilege Split"
Cohesion: 0.28
Nodes (9): BrokerClient, TrueNAS Custom App デプロイ (compose.yaml), DeviceMonitor, MountBroker, mediaferry-mountd (特権コンテナ), 特権の分離, 残る攻撃面（exfat ドライバでのマウント）, TrueNAS は USB を自動マウントしない (+1 more)

### Community 110 - "Design: Scanner Model"
Cohesion: 0.28
Nodes (9): deep_verify ジョブ, quick_fingerprint, Scanner, media_file テーブル, source_device テーブル, source_entry テーブル, volume_instance テーブル, volume_presence テーブル (+1 more)

### Community 111 - "Handoff: DB & Mutation Rules"
Cohesion: 0.28
Nodes (9): BEGIN IMMEDIATE + 条件付き UPDATE による claim（CAS）, DB 接続はスコープごとに 1 本, mutate.py（変異試験ドライバ、リポジトリに無い）, 変異試験（mutation testing）の作法, 計画のテストも疑う（実在の確認を含む）, PYTHONDONTWRITEBYTECODE=1 が要る理由, 送信は宛先ごとに 1 本のジョブで 1 件ずつ直列, タスクの完結手順（失敗するテスト → 最小実装 → 変異試験 → コミット） (+1 more)

### Community 112 - "Handoff: Merge Verification"
Cohesion: 0.22
Nodes (9): concat demuxer は preflight してから使う, 期待サイズは bit_rate が取れた保持ストリームだけで組み立てる, lavfi の合成クリップ（testsrc + sine）でのテスト, map はパートごとに自身の ffprobe 結果から作る, 結合のテストは実 ffmpeg バイナリを使う, サイズ検査の許容誤差 2%（実機の大きさが前提）, 結合検証にファイルサイズの単純比較を使わない, TS フォールバック経路（mpegts） (+1 more)

### Community 113 - "Fake Mount System"
Cohesion: 0.22
Nodes (4): FakeSystem, mount / open_tree / umount の状態を一貫して模す. `_discard_target` は「本当に取り付けられているか」を…, test_reap_stale_detaches_leftovers_from_a_previous_process(), test_reap_stale_propagates_detach_failure()

### Community 114 - "Media REST API"
Cohesion: 0.50
Nodes (7): get_media(), list_media(), list_orphans(), _media(), Any, get, ライブラリの一覧と、reconciliation が見つけた齟齬.

### Community 115 - "Upload End-to-End"
Cohesion: 0.32
Nodes (6): アップロードの pair と状態遷移（§8 / §9.10 / §10）. `POST /uploads` は `media_ids ×…, _publisher(), `_settle_merges` → `_settle_uploads` の順序を、実際の効果で確かめる. `merging` のまま残ったグループは起動時に…, test_a_group_settled_at_startup_changes_what_can_be_sent(), test_a_record_whose_group_changed_is_never_sent(), test_an_interrupted_upload_is_recovered_at_startup()

### Community 116 - "Scan Job"
Cohesion: 0.36
Nodes (3): Connection, Scanner, ScanOutcome

### Community 117 - "Design: Merge Verification"
Cohesion: 0.32
Nodes (8): ffmpeg concat demuxer 経路, データトラック（dbgi 等）の脱落は許容する, サイズ検査の inconclusive 判定, keep_streams（保持ストリームの明示宣言）, 結合結果の検証, Merger, ThumbnailService, TS フォールバック経路

### Community 118 - "Design: Profile Revisions"
Cohesion: 0.25
Nodes (8): preflight（送信前の向き先再確認）, プロファイルのリビジョン, ProfileRegistry, remote_user_id（検出値であって同一性ではない）, device_profile テーブル, profile_revision テーブル, target_epoch（履歴を引き継いでよいかの境界）, TOCTOU 対策（device_node を信用しない）

### Community 119 - "Phase 3 Plan Index"
Cohesion: 0.29
Nodes (7): docs/design.md（正本。§8 / §9.10 / §10 / §11 / §12 / §14）, docs/HANDOFF.md, docs/phase0-findings.md ②（Immich の実測前提）, docs/phase2-plan.md（直前のフェーズ）, mediaferry Phase 3（Immich 同期）実装計画, レビュー記録（codex 4 巡）, 秘密の漏洩防御（指紋化・識別子検査・例外文の除去）

### Community 120 - "Upload Recheck"
Cohesion: 0.40
Nodes (4): _action_of(), 送信済みレコードの状態を確かめ直す（§9.10「ゴミ箱と消滅の追跡」）. `remote_is_trashed` は `checking`…, 応答に無い行は触らない（`_parsed_check` が全単射を保証するので通常は無い）., RecheckOutcome

### Community 122 - "Mutation Testing Discipline"
Cohesion: 0.33
Nodes (6): 変異試験を省かない, 変異試験に PYTHONDONTWRITEBYTECODE=1 を付ける, 失敗するテストを先に書く, 検出できない変異は記録に残す, MEDIAFERRY_EXPECT_VOLUMES, スパイクで見つけた 2 つの欠陥

### Community 123 - "Design: Repo & Test Strategy"
Cohesion: 0.33
Nodes (6): mediaferry app (非特権コンテナ), core/ 純粋ドメイン層, fake ブローカー（USB 実機なしの CI）, fake Immich は実 HTTP サーバにする, リポジトリ構成, テスト戦略

### Community 124 - "Phase 1: Job Store"
Cohesion: 0.67
Nodes (3): JobStore.claim_next, DB 接続はスコープごとに 1 本, リースと CAS による単一ワーカーの claim

### Community 125 - "Backup & Secret Rules"
Cohesion: 0.67
Nodes (3): 秘密をログ・params_json・API 応答・例外に出さない, スナップショットは DB の整合を保証しない, sqlite3 .backup / VACUUM INTO で整合した 1 ファイルを作る

### Community 126 - "Phase 0: Upload URL Findings"
Cohesion: 0.67
Nodes (3): 公開 URL 経由は 622 MiB で 502, 接続先 URL と表示用 URL を分ける（IMMICH_URL / IMMICH_PUBLIC_URL）, 28.36 GiB のストリーミングアップロードで RSS 増分 0

### Community 127 - "Workspace Packages"
Cohesion: 0.67
Nodes (3): mediaferry, mediaferry-protocol, mountd

## Ambiguous Edges - Review These
- `スパイクを本番と同じ権限条件で測る` → `compose: app サービス`  [AMBIGUOUS]
  compose.spike.yaml · relation: conceptually_related_to
- `MountBroker` → `残る攻撃面（exfat ドライバでのマウント）`  [AMBIGUOUS]
  docs/design.md · relation: references
- `マイグレーションは足さない（0004 に既にスキーマがある）` → `0005_fingerprint_remote_identity.sql（既存 DB の指紋化移行）`  [AMBIGUOUS]
  docs/phase3-plan.md · relation: conceptually_related_to
- `Phase 5: 汎用化（Canon / プロファイル編集 UI / 複数デバイス）` → `USB の serial を一意な識別子にしない`  [AMBIGUOUS]
  docs/HANDOFF.md · relation: conceptually_related_to
- `変異試験（mutation testing）の作法` → `os.listdir(-1) は EBADF にならずカレントディレクトリを返す`  [AMBIGUOUS]
  docs/HANDOFF.md · relation: conceptually_related_to

## Knowledge Gaps
- **60 isolated node(s):** `mediaferry`, `app_setting`, `SettingSpec`, `mountd`, `mediaferry-workspace` (+55 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `スパイクを本番と同じ権限条件で測る` and `compose: app サービス`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `MountBroker` and `残る攻撃面（exfat ドライバでのマウント）`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `マイグレーションは足さない（0004 に既にスキーマがある）` and `0005_fingerprint_remote_identity.sql（既存 DB の指紋化移行）`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Phase 5: 汎用化（Canon / プロファイル編集 UI / 複数デバイス）` and `USB の serial を一意な識別子にしない`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `変異試験（mutation testing）の作法` and `os.listdir(-1) は EBADF にならずカレントディレクトリを返す`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `mediaferry Phase 2（結合）実装計画` connect `Phase Plan Documents` to `Environment Quirks`, `Publish Protocol Constants`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Why does `docs/HANDOFF.md` connect `Environment Quirks` to `agmsg Review Route`, `Phase Plan Documents`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._