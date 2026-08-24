-- 「そのカードを数え終えたか」を、数えた結果からではなく事実として持つ（§11）。
--
-- **一致するファイルが無いカードは `source_entry` を 1 行も作らない。** だから
-- 「行の最大 observed_at」で代用すると、スキャンが完全に成功しても
-- `scanned_at` が NULL のままになり、ホームは「中身を数えています。」から
-- 永久に出られない（DJI は内蔵ストレージと SD カードを同時に見せるので、
-- 撮影前の内蔵ストレージで実際に起きる）。
ALTER TABLE volume_instance ADD COLUMN scanned_at TEXT;

-- **既存の DB を埋め戻す。** 列を足すだけだと、これまで数え終えていたカードも
-- 更新した瞬間に「まだ数えていない」に見える。それまでの導出（行の最大
-- observed_at）をそのまま写す。行が無いカードは埋め戻せないが、次に挿したとき
-- に数え直される（`volume_presence.auto_scan_at` は presence ごとの印）。
UPDATE volume_instance
   SET scanned_at = (SELECT max(observed_at) FROM source_entry
                      WHERE source_entry.volume_instance_id = volume_instance.id)
 WHERE scanned_at IS NULL;
