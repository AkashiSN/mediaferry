-- **終わったジョブに「いま何をしているか」は無い。**
--
-- 進捗（`progress_json`）を落とすのは終了の 1 回きりなので、落とし忘れた版で
-- 終わった行は誰も直さない。実機では、直す前の版で完了した結合ジョブに
-- 「結合中 …」が残り続けていた（正常終了は `finish` ではなく `finish_claimed`
-- を通るので、片方だけ直しても効かなかった）。
--
-- **走っているものには触らない。**
UPDATE job SET progress_json = NULL
 WHERE progress_json IS NOT NULL
   AND status NOT IN ('queued', 'running', 'cancelling');
