## 版本 v1.3（迭代三：主持串词严格按 plan 歌曲边界插入）

基于 PRD 迭代记录：主持串词插入点不再基于 `segment.target_duration_seconds` 预估，而是基于“阶段二按 plan 映射后的实际歌曲时间线边界”计算；顺序保持 `串词1-segment1-串词2-segment2-...-串词N-segmentN`。

---

### Task 01 - 产出 segment 实际边界（基于已映射歌曲）
- **Task name**: 在选曲结果中显式计算每个 segment 的实际起止边界
- **Description**: 在 plan 驱动选曲完成后，新增一个轻量边界计算函数（或结果结构），按实际映射歌曲时长+crossfade 计算每个 `segment_i` 的音乐起始时间（第一首歌开始）与结束边界，用于后续主持插入点计算。边界来源必须是“实际选中歌曲”，不能使用 `target_duration_seconds`。
- **Input**:
  - `EpisodePlan`（segment 顺序）
  - 已按 plan 保序的 `selected_tracks`
  - `crossfade_seconds`
- **Output**:
  - `segment_boundaries`（至少包含每个 segment 的 `music_start`）
  - 缺失映射时直接失败，不返回边界
- **Files involved**:
  - `src/podcast_ai/modules/selection/selector.py`
  - （如需要）`src/podcast_ai/core/models.py`（仅在必须新增轻量模型时）
- **Dependencies**: 依赖 v1.2 的 plan 映射逻辑已可用
- **Estimated complexity**: S（1-2 小时）
- **Type**: backend

---

### Task 02 - Voiceover 插入点改为消费实际边界
- **Task name**: `VoiceoverService` 基于 segment 实际边界生成插入时间
- **Description**: 调整主持语音生成接口，不再按 segment 目标时长累加 `insert_time_in_episode`；改为接收 Task 01 的 `segment_boundaries` 并将 `串词_i` 的插入时间设置为 `segment_i.music_start` 之前的边界点。保持顺序严格一一对应：`voiceover_i -> segment_i`。
- **Input**:
  - `EpisodePlan`
  - `segment_boundaries`（来自 Task 01）
  - `language/use_cache`
- **Output**:
  - `list[VoiceoverSegment]`，其中 `insert_time_in_episode` 严格对齐对应 segment 第一首歌边界
- **Files involved**:
  - `src/podcast_ai/modules/voiceover/tts_service.py`
  - `src/podcast_ai/core/pipeline.py`（传参与调用调整）
- **Dependencies**: Task 01
- **Estimated complexity**: S（1-2 小时）
- **Type**: backend

---

### Task 03 - 混音层按“边界插入”执行且不重叠
- **Task name**: `Mixer` 对齐边界插入并保持无背景串词段
- **Description**: 校正 `Mixer.build_mix(...)` 的边界映射逻辑，确保主持插入点以 Task 02 传入的实际边界为准；主持段与歌曲段不重叠、主持期间无背景音乐；crossfade 仅作用于相邻歌曲之间。
- **Input**:
  - `selected_tracks`（plan 顺序）
  - `voiceovers`（边界对齐后的 `insert_time_in_episode`）
  - `AudioRenderConfig`
- **Output**:
  - 最终 mix 时间线满足 `串词_i -> segment_i(歌曲组)` 顺序
  - 保持既有 crossfade 行为不回退
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Dependencies**: Task 02
- **Estimated complexity**: S（1-2 小时）
- **Type**: backend

---

### Task 04 - 流水线集成与失败阻断
- **Task name**: `create_episode` 串联“选曲边界 -> 主持 -> 混音”
- **Description**: 在 `create_episode` 中串联新流程：先按 plan 选曲并得到 segment 实际边界，再生成主持插入点，再混音。若任一 segment 映射失败或边界缺失，直接报错并阻断，不进入后续混音，避免错位输出。
- **Input**:
  - `plan_path`
  - `music_dir`
  - runtime `settings`
- **Output**:
  - 正常路径：按 v1.3 规则输出
  - 异常路径：缺失信息清晰报错并停止
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/selection/selector.py`
  - `src/podcast_ai/modules/voiceover/tts_service.py`
- **Dependencies**: Task 01, Task 02, Task 03
- **Estimated complexity**: S（1 小时）
- **Type**: backend

---

### Task 05 - 测试覆盖 v1.3 验收点 ✅
- **Task name**: 新增/更新单测验证“按实际边界插入”
- **Description**: 增加用例验证：1）`串词_i` 对齐 `segment_i` 第一首歌边界（非 target_duration 推导）；2）串词不与歌曲重叠；3）顺序严格为 `串词1-segment1-...`；4）segment 映射失败时流程阻断。
- **Input**:
  - 可控的 plan/library 样本
  - mock 或短音频文件
- **Output**:
  - `pytest` 通过，覆盖 v1.3 关键验收标准
- **Files involved**:
  - `tests/test_mixing.py`
  - `tests/test_pipeline.py`
  - （如需要）`tests/test_selection.py`
- **Dependencies**: Task 04
- **Estimated complexity**: M（2-3 小时）
- **Type**: backend

