## 版本 v3.9（迭代十七：阶段二适配 `<episode_id>.json` 输入）

基于你补充的实现意图：阶段二**完全弃用 `EpisodePlan`**，后续执行链路（选曲、串词编排、混音）直接消费 `<episode_id>.json`（Snapshot 子集）。
这意味着不只是读取层适配，而是阶段二核心模块的数据契约整体切换。

结论：**需要重写 v3.9 任务列表，不是只改 Task 02。**

---

### Task 01 - 定义阶段二 Snapshot 领域模型（替代 EpisodePlan 入参）
- **Task name**: v3.9 - Stage2Snapshot 契约模型化
- **目标**: 为阶段二建立专用输入模型，覆盖 `<episode_id>.json` 的必需结构，并作为后续模块唯一输入对象。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 在 `src/podcast_ai/core/models.py` 新增（示例）：
    - `Stage2SnapshotMeta`：`request_id/theme/language/target_duration_seconds`
    - `Stage2PlaylistItem`：`track/artist`（按 sample）
    - `Stage2Script`：`segment_intro/between_tracks[]`
    - `Stage2Segment`：`segment_id/name/target_duration_seconds/playlists/script`
    - `Stage2Snapshot`：`schema/meta/segments`
  - 定义解析入口：`Stage2Snapshot.model_validate_json(...)` 或等价方法。
  - 明确字段最小必需约束（缺失即失败，不静默填充）。
- **Input**: `<episode_id>.json`
- **Output**: 强类型的 `Stage2Snapshot`
- **Files involved**:
  - `src/podcast_ai/core/models.py`
  - 参考：`Sample_EpisodePlan_NEW.json`
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 阶段二输入加载切换：`create_episode` 直接读 Snapshot
- **Task name**: v3.9 - pipeline 输入主路径切换
- **目标**: `create_episode` 默认并仅以 `<episode_id>.json` 为主输入，移除内部 `load_episode_plan` 依赖。
- **类型**: api
- **依赖关系**: Task 01
- **Description**:
  - 修改 `src/podcast_ai/core/pipeline.py::create_episode`：
    - 入参语义从 `plan_path` 调整为 `snapshot_path`（可保持参数名但注释/错误信息必须改清晰）
    - 加载 `Stage2Snapshot` 并进行结构校验
    - 失败时返回可定位错误（字段路径/段索引）
  - 不再调用 `load_episode_plan(...)` 作为主路径。
  - `episode_root` 继续从 `<episode_id>.json` 路径推导（`.../episodes/<episode_id>/plans/<episode_id>.json`）。
- **Input**: Snapshot 文件路径
- **Output**: 阶段二启动后持有 `Stage2Snapshot`，不再依赖 `EpisodePlan`
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/infra/storage/paths.py`（新增 snapshot 读取 helper 可选）
- **Estimated complexity**: S（1-2 小时）

---

### Task 03 - 选曲模块签名迁移：`selector` 直接消费 Snapshot.playlists
- **Task name**: v3.9 - select_tracks_by_snapshot
- **目标**: 让选曲逻辑直接基于 `segments[*].playlists` 工作，去除对 `EpisodePlan.target_playlist` 的依赖。
- **类型**: backend
- **依赖关系**: Task 01、Task 02
- **Description**:
  - 在 `src/podcast_ai/modules/selection/selector.py` 新增并迁移：
    - `select_tracks_by_snapshot(snapshot, library, crossfade_seconds)`
    - `compute_segment_boundaries_from_snapshot(snapshot, selected_tracks, crossfade_seconds)`
    - `split_tracks_by_snapshot(snapshot, selected_tracks)`
  - 映射规则：
    - 每个 segment 的曲目顺序直接来自 `playlists` 数组顺序
    - 匹配优先用 `track + artist`，其次降级单字段匹配
  - 保留旧 `select_tracks_by_plan` 可短期兼容（非主路径），但 pipeline 改为新函数。
- **Input**: `Stage2Snapshot`
- **Output**: 与当前流程兼容的 `SelectedTrack[]` + 边界结果
- **Files involved**:
  - `src/podcast_ai/modules/selection/selector.py`
  - `src/podcast_ai/core/pipeline.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 04 - 串词编排重构：支持 `between_tracks`（段内插点）
- **Task name**: v3.9 - voiceover timeline from snapshot script
- **目标**: 串词生成从“每段 1 条 host_script”升级为“segment_intro + between_tracks 多插点”，直接消费 Snapshot.script。
- **类型**: backend
- **依赖关系**: Task 01、Task 03
- **Description**:
  - 修改 `src/podcast_ai/modules/voiceover/tts_service.py`：
    - 新增 `generate_voiceovers_from_snapshot(snapshot, selected_tracks/segment_boundaries, ...)`
  - 时间线规则（最小可执行版本）：
    - `segment_intro`：插在该 segment 第一首歌前
    - `between_tracks[k].after_track_index`：插在 segment 内对应歌曲后边界
  - 对 `text=null` 保持跳过（不生成 TTS），`text` 非空则生成独立 `VoiceoverSegment`
  - 越界索引/结构异常要抛明确错误，阻断流程。
- **Input**: Snapshot.script + 已选曲目边界
- **Output**: 含段内多插点的 `VoiceoverSegment[]`
- **Files involved**:
  - `src/podcast_ai/modules/voiceover/tts_service.py`
  - `src/podcast_ai/core/pipeline.py`
- **Estimated complexity**: L（3 小时）

---

### Task 05 - 混音对齐改造：从“每段一串词”到“按时间点插入多串词”
- **Task name**: v3.9 - mixer supports multi-voiceover timeline
- **目标**: 混音层不再假设“voiceover 数量 == segment 数量”，改为基于 `insert_time_in_episode` 的通用串词插入。
- **类型**: backend
- **依赖关系**: Task 03、Task 04
- **Description**:
  - 修改 `src/podcast_ai/modules/mixing/mixer.py`：
    - 当前 `串词_i → 组_i` 拼接逻辑改为“先构建音乐主时间线，再按时间点插入/叠加串词事件”
  - 保持既有 v2.1 边界原则不倒退：
    - 音乐→串词硬切
    - 串词→音乐仅音乐淡入
    - 歌曲-歌曲 crossfade 仍按原配置
  - 处理多串词同段场景，确保顺序与 Snapshot 脚本一致。
- **Input**: `SelectedTrack[]` + 多插点 `VoiceoverSegment[]`
- **Output**: 正确对齐的最终混音
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - `src/podcast_ai/core/pipeline.py`
- **Estimated complexity**: L（3 小时）

---

### Task 06 - CLI / 文档契约同步（阶段二输入）
- **Task name**: v3.9 - create-episode 输入说明更新
- **目标**: CLI 与 README 明确阶段二输入是 `<episode_id>.json`，避免继续传旧 EpisodePlan 文件。
- **类型**: api
- **依赖关系**: Task 02
- **Description**:
  - 更新 `src/podcast_ai/cli.py`：
    - `create-episode` 参数 help 改为“阶段一产出的 `<episode_id>.json`”
    - 错误提示聚焦 snapshot 字段契约
  - 更新 `README.md` 示例命令与文件路径说明。
- **Input**: 用户命令输入
- **Output**: 用户可按新契约直接跑通阶段二
- **Files involved**:
  - `src/podcast_ai/cli.py`
  - `README.md`
- **Estimated complexity**: XS（0.5-1 小时）

---

### Task 07 - 回归测试：Snapshot 主路径 + between_tracks 行为
- **Task name**: v3.9 - stage2 snapshot e2e regression
- **目标**: 锁定 v3.9 新契约并防止回退。
- **类型**: backend
- **依赖关系**: Task 01-06
- **Description**:
  - 更新 `tests/test_pipeline.py` 与相关模块测试：
    1) `create_episode` 以 `<episode_id>.json` 输入可跑通
    2) 缺失关键字段时明确失败（非静默）
    3) `between_tracks` 有文本时会生成额外串词并参与混音
    4) `between_tracks.text=null` 不生成语音但流程不报错
  - 适当新增：
    - `tests/test_stage2_snapshot_input.py`
    - `tests/test_voiceover_snapshot.py`（可选）
- **Input**: 合法/非法 snapshot 样本
- **Output**: `pytest` 通过，阶段二新输入契约受控
- **Files involved**:
  - `tests/test_pipeline.py`
  - `tests/test_voiceover.py`
  - `tests/test_mixing.py`
  - （可选）新增测试文件
- **Estimated complexity**: M（2-3 小时）
