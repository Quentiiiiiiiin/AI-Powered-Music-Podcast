## 版本 v4.2（迭代二十：串词→下一首歌 crossfade 与 intro 对齐）

基于 PRD v4.2 与 ARCHITECTURE 中 **AD-2026-04-v4.2**：在阶段二混音的 **「串词 → 紧随其后的下一首本地曲目」** 边界，用 **librosa** 估计该曲 **intro（前奏）** 区间，并据此参与 **串词→歌** crossfade 的**重叠长度与对齐意图**（长串词尾部尽早、适度叠入 intro，收尾尽量靠近 intro 将尽、主歌将起；允许工程容差）。**歌→歌** crossfade **语义与实现必须与 v3.9.1 一致**，不得被 intro 逻辑改写。

失败路径：**必须**回退到与 v3.9.1 兼容的默认 `voice_music_crossfade_seconds`（及文档化上限），打日志，禁止静默错位。librosa 缺失或分析异常时降级并给出可读提示。

---

### Task 01 - intro 估计契约与混音接入点设计（Mixer）
- **Task name**: v4.2 - intro 对齐数据流与 API 草图
- **目标**: 明确「每条串词之后的第一首本地文件」如何与当前 `_build_ordered_blocks_from_tracks_and_voiceovers` + `_concat_ordered_blocks` 衔接；定义纯函数输入输出（例如：音频路径 → intro 时长秒数或「叠入建议」+ 置信度/是否回退），不写过度抽象层。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 对照 PRD 技术路线：RMS 包络、onset、节拍/强拍等启发式组合；阈值、窗口、回退条件在实现文件顶部或函数 docstring 中写清。
  - 确认仅影响 **voice → music** 拼接点；**music → music** 仍仅用 `crossfade_seconds`。
  - 若下一曲音乐块由多首 `crossfade_concat` 组成，intro 估计仅针对 **该块内时间顺序第一首** 的 `file_path`。
- **Input**: 当前 `mixer.py` 块模型、PRD v4.2 五条验收
- **Output**: 可编码的函数签名与 `_concat_ordered_blocks` 所需额外参数（如「下一首首曲路径」或预计算的 `vm_ms`）决策
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - （新建，可选命名）`src/podcast_ai/modules/mixing/intro_align.py`
- **Estimated complexity**: S（1 小时）

---

### Task 02 - 使用 librosa 实现 intro 估计（含回退）
- **Task name**: v4.2 - `estimate_track_intro_seconds` 纯函数
- **目标**: 在 `modules/mixing` 内实现单一模块（建议 `intro_align.py`），对本地音频文件估计 intro 结束相对时间（或等价：建议的「串词尾与音乐前窗」重叠上界），低置信度/异常时返回「使用默认」标志，**不抛异常中断整期**（由调用方打日志并回退）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 使用 librosa 加载与分析；综合 RMS、onset、节拍相关特征，具体权重与回退写在注释中。
  - 极短音频、静音占比异常、全曲无明显主歌抬升等：走回退分支。
  - 依赖项：`requirements.txt` / `pyproject.toml` 中确保 librosa 可安装；若 import 失败，模块级可由调用方捕获并降级（满足 PRD 验收 5）。
- **Input**: `Path` 到音频文件
- **Output**: 数值 + 是否采用估计结果（或 Optional[float]）
- **Files involved**:
  - `src/podcast_ai/modules/mixing/intro_align.py`（新建）
  - 项目依赖清单（若尚未声明 librosa）
- **Estimated complexity**: L（3–5 小时）

---

### Task 03 - 时间线构建阶段携带「voice 后首曲」元数据
- **Task name**: v4.2 - 块序列附带下一首首曲路径
- **目标**: 在生成 `(music|voice)` 块序列时，对每个 **voice** 块记录其紧随其后的 **music** 块中「第一首 `SelectedTrack.track.file_path`」`Path | None`（无曲目则 None），供 `_concat_ordered_blocks` 在 **voice→music** 时计算动态 `vm_ms`。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 可扩展块类型为 `(BlockKind, AudioSegment, meta)` 或并行列表，**避免**为 intro 再扫一遍大块音频重解耦路径。
  - 不改变 v3.9.1 锚点校验与 flush 规则。
- **Input**: 现有 `_build_ordered_blocks_from_tracks_and_voiceovers`
- **Output**: 拼接阶段可拿到「下一首首曲」路径
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: M（2 小时）

---

### Task 04 - 在 `_concat_ordered_blocks` 中接入动态串词→歌 crossfade
- **Task name**: v4.2 - voice→music 使用 intro 驱动 vm_ms
- **目标**: 在 **voice → music** 边界：若存在首曲路径且 intro 估计成功，用估计结果与配置上限计算本次 `_join_voice_to_music_fade_music_only` 的 `vm_ms`；否则使用 `config.voice_music_crossfade_seconds`（与 v3.9.1 一致），并 `logger.info/warning` 说明原因。
- **类型**: backend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - `vm_ms` 必须满足：`0 < vm_ms <= min(len(voice), len(music), 上限)`，上限来自配置（见 Task 05）或 `voice_music_crossfade_seconds` 的明确倍数/取 min，在注释中写清「与 v3.9.1 兼容」的含义。
  - **禁止**修改 `_join_music_to_voice_hard`、**禁止**改变 **music→music** 的 `crossfade_concat` 调用方式。
  - 串词→串词仍为硬拼，不受影响。
- **Input**: 带元数据的块序列 + `AudioRenderConfig`
- **Output**: 混音输出符合 PRD 验收 1、3、4
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 05 - 最小配置项（可选开关与上限）
- **Task name**: v4.2 - 串词→歌 intro 对齐配置
- **目标**: 在 `infra/config.py`（`AudioConfig`）与 `core/models.py`（`AudioRenderConfig`，若需透传）中增加**最少**字段：例如「是否启用 intro 对齐」「串词→歌叠化上限秒」；默认值保持与当前体验接近，关闭时完全等同 v3.9.1 固定 `voice_music_crossfade_seconds`。
- **类型**: backend
- **依赖关系**: 可与 Task 04 并行，但集成需在 Task 04 完成前合并字段
- **Description**:
  - 避免引入大量 YAML 嵌套；`init-config` / README 一行说明即可。
  - `pipeline.create_episode` 构造 `AudioRenderConfig` 时传入新字段。
- **Input**: AD-2026-04-v4.2「仅增加与 v4.2 相关的上限/开关」
- **Output**: 可配置、可关闭、可验收回退
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `src/podcast_ai/core/models.py`
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/cli.py`（仅当需 CLI 覆盖时再增加 Option；非必须则跳过）
- **Estimated complexity**: S（1 小时）

---

### Task 06 - 单测与回归（歌→歌不变 + intro 回退）
- **Task name**: v4.2 - Mixer / intro_align 测试
- **目标**: 用短合成音频或 mock `estimate_track_intro_*` 验证：① voice→music 在成功估计时使用非默认 `vm_ms`；② 失败时 `vm_ms` 与固定配置一致且打日志；③ music→music 路径与 v3.9.1 断言一致（可复用或扩展现有 `test_mixing`）。
- **类型**: backend
- **依赖关系**: Task 02, Task 04（Task 05 若改签名则一并依赖）
- **Description**:
  - 不强制真实 librosa 重分析 CI 大文件；优先 mock 估计返回值测拼接分支。
  - 可选：一条集成测试使用极简 wav + librosa（若 CI 环境允许）。
- **Input**: `tests/test_mixing.py` 等
- **Output**: `pytest` 通过，锁定 PRD 验收 2、3
- **Files involved**:
  - `tests/test_mixing.py`
  - `tests/test_intro_align.py`（可新建）
- **Estimated complexity**: M（2 小时）
