## 版本 v4.5（迭代二十三：新增阶段三支持人工微调最终 crossfade）

基于 PRD v4.5 + ARCHITECTURE 中 **AD-2026-04-v4.5**：将当前“两阶段（阶段一规划 + 阶段二直接混音导出）”扩展为“三阶段（阶段二生成可编辑参数、阶段三最终混音导出）”。

本迭代的核心契约是：
- **阶段二**：继续负责 TTS 串词文件生成，并生成“混音之前的重要参数 JSON”（含 `tracks`、`voiceovers`/tts 文件路径与串词文本、以及两段音频之间的转场参数如 `form=crossfade`、`intro/vm_candidate` 等），落盘并返回路径；阶段二不产出最终音频。
- **阶段三**：读取用户微调后的参数 JSON，进行必要校验后完成最终混音与导出（`wav` + `mp3`），并保持与 v3.9.1 的转场语义一致：**歌→串词无 crossfade；串词→歌 crossfade；歌→歌 crossfade；段内 `between_tracks` 串词参与同一规则**。

失败策略：
- 阶段三入口对 JSON 做严格校验；非法编辑值必须明确报错并中止，不静默回退到旧参数。

---

### Task 01 - 定义 MixParamsJSON（可编辑混音参数 JSON）与严格校验
- **Task name**: v4.5 - MixParamsJSON schema & validators
- **目标**: 在 `src/podcast_ai/core/models.py` 新增 `MixParamsJSON`（含 `meta/schema_version`、`tracks`、`voiceovers`、`transitions`），并提供“阶段三严格校验”所需的最小校验规则（非法值中止）。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 设计 JSON 字段尽量复用现有模型：`SelectedTrack`、`VoiceoverSegment`（可序列化为 JSON），并在 `transitions` 中描述 voice→music 与 music→music 边界的可编辑 crossfade 参数。
  - 明确约束：
    - `transitions` 中的边界条目应能与 `voiceovers`（按 `segment_id`）或对应 timeline 位置稳定映射；
    - 允许用户覆盖的字段包括（至少）`vm_candidate`/`intro`/（必要时的）`crossfade_seconds`；
    - 阶段三校验时对非法数值（负数、NaN、超过允许重叠上限或不满足约束的 crossfade）直接抛错并终止。
- **Input**: PRD v4.5、ARCHITECTURE 中对字段与校验要求
- **Output**: 可被阶段二写入、阶段三读取且可严格校验的 schema 模型
- **Files involved**:
  - `src/podcast_ai/core/models.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 02 - Mixer 支持“生成转场参数”与“从参数渲染最终混音”
- **Task name**: v4.5 - mixer mix-params apply
- **目标**: 将当前 `modules/mixing/mixer.py` 从“只给 selected_tracks/voiceovers + config 直接渲染”扩展为：
  1) 能基于当前算法生成 voice→music / music→music 的转场参数（阶段二用）；
  2) 能从 MixParamsJSON 读取用户覆盖的转场参数并应用到最终渲染（阶段三用）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 不改变 v3.9.1 转场语义分支：music→voice 硬切不变；voice→music vm 由 JSON 覆盖时采用覆盖值，否则用 v4.2/v4.3 估计值；
  - 确保对 voice→music 的 crossfade 仍满足 v4.3 的“合法重叠区间”语义：阶段三负责“校验并拒绝非法编辑值”，不在阶段三静默回退；
  - 阶段二只需输出参数，不做最终叠加渲染。
- **Input**: selected_tracks、voiceovers、以及（阶段二）默认估计值或（阶段三）来自 MixParamsJSON 的覆盖值
- **Output**: 两个接口（或一个接口拆分为两个模式）：
  - `build_mix_params(...) -> transitions`
  - `render_final_mix_from_mix_params(...) -> AudioSegment / Path`
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - （可选）`src/podcast_ai/modules/mixing/intro_align.py`（仅复用现有估计）
- **Estimated complexity**: L（3-4 小时）

---

### Task 03 - pipeline 阶段二：生成并落盘可编辑混音参数 JSON
- **Task name**: v4.5 - create_episode_stage2
- **目标**: 在 `src/podcast_ai/core/pipeline.py` 新增阶段二接口 `create_episode_stage2(...)`：
  - 扫描/选曲；
  - 生成 TTS 串词文件；
  - 计算并落盘 MixParamsJSON；
  - 返回 MixParamsJSON 路径（以及可选的 episode_root 便于 CLI 打印路径），不执行最终 mastering/export。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 复用现有 `create_episode` 中的库扫描/选曲/segment 边界计算/voiceovers 生成逻辑；
  - 在“混音之前”插入 MixParamsJSON 生成与落盘；
  - 保持旧 `create_episode` 的行为不回退：它可以内部调用 stage2+stage3 组合，给 CLI 仍可用的一键路径。
- **Input**: snapshot_path（`<episode_id>.json`）、music_dir、topic/language（TTS）
- **Output**: MixParamsJSON path
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/infra/storage/paths.py`（新增落盘/读取 mix_params json 的路径函数）
- **Estimated complexity**: M（2-3 小时）

---

### Task 04 - pipeline 阶段三：读取用户编辑参数并最终混音导出
- **Task name**: v4.5 - finalize_episode_stage3
- **目标**: 新增阶段三接口 `finalize_episode_stage3(mix_params_json_path, ...) -> EpisodeResult`：
  - 加载并严格校验 MixParamsJSON（非法编辑值中止）；
  - 仅依赖 JSON 执行最终时间线混音与导出（`wav` + `mp3` + Show Notes）；
  - 不依赖阶段二的中间运行时状态或 snapshot 内部变量。
- **类型**: backend
- **依赖关系**: Task 01, Task 02, Task 03
- **Description**:
  - 对 `transitions` 覆盖值做严格检查（数值合法性、范围约束、能量/时长约束、边界映射一致性）；
  - 映射到当前 mixer 的 voice/music 边界时，如检测到 JSON 与当前 tracks/voiceovers 不一致，直接报错；
  - 复用 `MasteringService` 与 `Exporter`（需要从 JSON 重建最小 `EpisodePlan` 或直接提供 exporter 可用字段）。
- **Input**: 用户编辑后的 mix_params json path
- **Output**: 最终 `EpisodeResult` 与导出音频路径
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/mixing/mixer.py`（渲染从参数出发）
  - `src/podcast_ai/infra/storage/paths.py`（最终音频/中间 wav 输出路径，复用现有函数）
- **Estimated complexity**: L（3-4 小时）

---

### Task 05 - CLI：阶段二导出 JSON + 阶段三最终导出
- **Task name**: v4.5 - CLI stage2/stage3 入口
- **目标**: 在 `src/podcast_ai/cli.py` 增加：
  - `create-episode-stage2`：生成 MixParamsJSON 并输出其路径；
  - `finalize-episode-stage3`：读取用户编辑后的 JSON 并导出最终音频。
- **类型**: api
- **依赖关系**: Task 03, Task 04
- **Description**:
  - 非法输入（JSON 不存在/非法编辑）要返回明确错误信息并退出；
  - 保留现有 `create-episode` 作为“一键 stage2+stage3”路径（内部组合），避免用户使用成本上升。
- **Input**: snapshot_path、music_dir（stage2）；mix_params_json_path（stage3）
- **Output**: JSON path / 最终音频 path
- **Files involved**:
  - `src/podcast_ai/cli.py`
- **Estimated complexity**: S（1-2 小时）

---

### Task 06 - 测试：阶段二 JSON 生成 + 阶段三校验与覆盖生效
- **Task name**: v4.5 - stage2/3 contract tests
- **目标**: 增加针对 v4.5 的关键回归测试，覆盖：
  - stage2 输出 JSON 的 schema 与关键字段存在性；
  - stage3 校验：非法 crossfade/overlap 值必须失败中止；
  - stage3 覆盖：修改 JSON 中某个 voice→music 的 vm_candidate/crossfade 参数，最终输出行为（至少 vm 或重叠时长相关的可观察结果）发生改变。
- **类型**: backend
- **依赖关系**: Task 03, Task 04
- **Description**:
  - 尽量 mock 或 stub 音频加载/渲染，避免真实 ffmpeg/大文件；
  - 关键是“校验与应用逻辑”的正确性与可追溯 reason。
- **Input**: 小样例 snapshot + stub 音频 / mock audio_backend
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_pipeline.py`（扩展 stage2/stage3 用例）
  - `tests/test_mixing.py`（如需要验证应用覆盖逻辑）
- **Estimated complexity**: M（2-3 小时）
