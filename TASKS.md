## 版本 v1.2（迭代二：阶段二严格按 plan 混音）

基于 PRD 迭代记录：阶段二不再执行“BPM 过滤 + BPM 排序 + 贪心选曲”，而是严格按照阶段一输出的 `plan` 来决定歌曲播放顺序与 mix 顺序；plan 指定曲目无法映射到本地候选时应报错而不是自动替换。

---

### Task 01 - Plan 驱动的选曲映射（顺序保持）
- **Task name**: 实现 `select_tracks_by_plan`（plan->本地文件映射 + 保序）
- **Description**: 在 `modules/selection/selector.py` 增加一个“严格按 plan 顺序”的选择逻辑：遍历 `plan.segments[]` 与每段 `target_playlist[]` 的顺序，对每个 `PlaylistItem.recommended_tracks` 进行本地候选映射（按文件名/`mutagen` 提取的 `title/artist/genre` 做最简单匹配打分）；得到映射的歌曲后，按 plan 顺序生成 `SelectedTrack` 列表，并计算 `start_time_in_episode/end_time_in_episode/effective_duration`（crossfade 只用于时间线换算，不参与排序）。
- **Input**:
  - `plan: EpisodePlan`（包含 segments 与 target_playlist）
  - `library: list[TrackWithMetadata]`（本地候选库）
  - `crossfade_seconds: float`
- **Output**:
  - `selected_tracks: list[SelectedTrack]`，顺序严格与 plan 一致
  - 若某个 `PlaylistItem` 无法映射到本地候选：抛出清晰错误（包含 segment/playlist 索引与推荐曲目文本）
- **Files involved**:
  - `src/podcast_ai/modules/selection/selector.py`
  - （如需要）`src/podcast_ai/core/exceptions.py`（仅复用现有 `PodcastAIError` 类型则可不改）
- **Dependencies**: 无
- **Estimated complexity**: M（2-3 小时）
- **Type**: backend

---

### Task 02 - Pipeline 阶段二改为使用 plan 驱动选曲
- **Task name**: `create_episode` 替换 BPM 贪心选曲入口
- **Description**: 修改 `core/pipeline.py` 的 `create_episode`：将当前 `TrackSelector.select_tracks(...)` 调用替换为 Task 01 的 `select_tracks_by_plan(...)`；确保阶段二不再出现 BPM 过滤/排序/贪心逻辑。映射缺失错误向上抛出，让 CLI/调用方能看到“缺失提示”。
- **Input**:
  - `plan_path + music_dir`（来自 create_episode 入参）
  - 当前 `plan` 与扫描得到的 `library`
- **Output**:
  - 阶段二导出的 mix 歌曲顺序与 plan 顺序一致
  - 映射失败时给出明确错误提示
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/selection/selector.py`（Task 01 的新方法会被引用）
- **Dependencies**: Task 01
- **Estimated complexity**: S（1 小时）
- **Type**: backend

---

### Task 03 - 测试：Plan 顺序保序 + 缺失映射报错
- **Task name**: 新增选择器单测覆盖 v1.2 验收点
- **Description**: 在 `tests/test_selection.py` 新增用例：
  1) `selected_tracks` 顺序严格等于 plan 的顺序（构造 library 的 BPM/时长与 plan 顺序相反，用于验证不会触发二次排序）。
  2) plan 指定曲目无法映射时应抛错（断言错误类型与包含“segment/推荐曲目”信息的关键字）。
- **Input**:
  - 构造最小 `EpisodePlan`（含 2 段或更多、每段至少 1 个 `PlaylistItem`）
  - 构造 `sample_library` 风格的 `TrackWithMetadata` 列表（包含 title/artist 用于匹配）
- **Output**:
  - `pytest` 通过
  - 单测覆盖 v1.2 的关键验收标准（保序、不替换）
- **Files involved**:
  - `tests/test_selection.py`
- **Dependencies**: Task 01
- **Estimated complexity**: S（1-2 小时）
- **Type**: backend

