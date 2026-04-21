## 版本 v3.9.1（迭代十八：Mixer 串词 / 歌曲转场与 snapshot 对齐）

基于 PRD v3.9.1：阶段二混音**不再**采用「先把全部歌曲 `crossfade_concat` 串完，再按 `insert_time` 往音乐时间线里插串词」的顺序（该顺序会导致「下一首已起一小段串词才接上」）；改为以 `<episode_id>.json` snapshot 与阶段二已生成的 `VoiceoverSegment` 为**单一时间线来源**，按「串词 ↔ 曲目」真实顺序编排，并统一三类转场：

1. **歌 → 串词**：无 crossfade（硬切 / 自然结束接人声，串词开头清晰）。
2. **串词 → 歌**：crossfade（串词尾与下一首进入重叠过渡，沿用 `voice_music_crossfade_seconds` 语义）。
3. **歌 → 歌**：crossfade（沿用 `crossfade_seconds`）。

段内 `between_tracks` 与段首 `segment_intro` 均走同一套相邻边界规则。与 v3.9 输入契约兼容：仍以 `<episode_id>.json` 为主输入，不新增计划文件格式。

---

### Task 01 - 现状审计与单一编排路径设计（Mixer）
- **Task name**: v3.9.1 - Mixer 编排路径收敛设计
- **目标**: 梳理 `src/podcast_ai/modules/mixing/mixer.py` 内现有分支（`plan` 分段拼接、`_insert_voiceovers_on_music_timeline`、无主持全曲 crossfade），明确 v3.9.1 后**唯一主路径**与可删除/合并的冗余分支。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 对照 PRD 示例时间片顺序，写出目标状态机：相邻块类型 `(music|voice)` → 应用哪条转场规则。
  - 确认与 `pipeline.create_episode` 当前调用方式一致：`build_mix(..., plan=None)` 且 `voiceovers` 已带 `insert_time_in_episode`（由 `VoiceoverService.generate_voiceovers_from_snapshot` 产出）。
  - 输出一页内可执行的伪代码/步骤列表（不落文档文件也可，写在 `Mixer.build_mix` docstring 内即可）。
- **Input**: 当前 `mixer.py` + PRD v3.9.1
- **Output**: 明确「删哪些分支、新主路径如何拼」
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - （只读）`src/podcast_ai/core/pipeline.py`
- **Estimated complexity**: S（1 小时）

---

### Task 02 - 从「曲目时间线 + 串词时间点」构造有序音频块序列
- **Task name**: v3.9.1 - 时间线展开为 music/voice 块列表
- **目标**: 将 `SelectedTrack`（全曲顺序）与按 `insert_time_in_episode` 排序的 `VoiceoverSegment` 合并为**严格时间递增**的块序列，每块为已 `load_audio`+`simple_normalize` 的 `AudioSegment`，并携带块类型（music/voice）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 在 `mixer.py` 内新增小函数（示例）：`_build_ordered_blocks_from_tracks_and_voiceovers(selected_tracks, voiceovers) -> list[tuple[Literal[\"music\",\"voice\"], AudioSegment]]`。
  - 规则：按 `insert_time` 将 voice 插入**全局曲目时间线**的正确位置（与 snapshot 语义一致：串词不应落在「下一首已开始」之后）。
  - 若 `insert_time` 冲突或越界，抛 `ValueError`/`PodcastAIError` 并带 segment_id / insert_time。
- **Input**: `selected_tracks`、`voiceovers`
- **Output**: 有序块列表
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - 重构 `Mixer.build_mix`：按相邻块类型统一施加三条转场规则
- **Task name**: v3.9.1 - 单一 `build_mix` 主路径
- **目标**: 用 Task 02 的块序列，自左向右拼接；对每一对相邻块 `(prev, next)` 仅按类型选择：`music+voice` 硬接、`voice+music` 用现有 `_join_voice_to_music_fade_music_only`、`music+music` 用 `crossfade_concat` 的两段等价拼接（或抽 2 段专用 helper，避免整轨先拼）。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - **删除/停用**「先 `crossfade_concat(track_audios)` 再 `_insert_voiceovers_on_music_timeline`」分支（PRD 验收 1 明确禁止该顺序）。
  - `voice_music_crossfade_seconds` 仅用于 **voice→music**；`crossfade_seconds` 仅用于 **music→music**；**music→voice** 禁止叠化。
  - 无 `voiceovers` 时保留「全曲 music→music crossfade」短路径（可复用现有逻辑）。
- **Input**: 有序块序列 + `AudioRenderConfig`
- **Output**: 最终 `AudioSegment` 混音结果
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - `src/podcast_ai/infra/audio_backend.py`（仅复用 `crossfade_concat` / `export_audio` 等，尽量不扩展）
- **Estimated complexity**: L（3 小时）

---

### Task 04 - 冗余清理与接口收敛（保持简洁）
- **Task name**: v3.9.1 - 删除旧编排分支与死代码
- **目标**: 在 Task 03 主路径稳定后，移除不再被 snapshot 阶段二调用的冗余实现，降低维护成本。
- **类型**: backend
- **依赖关系**: Task 03
- **Description**:
  - 删除或内联：`_insert_voiceovers_on_music_timeline`、`_concat_parts_with_voice_music_crossfade`、`_split_tracks_into_groups` 等若已无任何调用路径。
  - 移除 `Mixer.build_mix` 的 `plan: Optional[EpisodePlan]` 参数（若全局已无调用方）；或保留但标记弃用并确保 `pipeline` 不再传入——**优先删除**以符合「去除冗余」。
  - 清理 `from podcast_ai.modules.selection.selector import split_tracks_by_plan` 等未使用 import。
  - 同步更新 `pipeline.create_episode` 中对 `build_mix` 的调用签名（若 Task 04 删除参数）。
- **Input**: 全仓库 rg 引用检查
- **Output**: `mixer.py` 更短、单一路径
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - `src/podcast_ai/core/pipeline.py`
  - `tests/test_mixing.py`（若签名变更）
- **Estimated complexity**: S（1-2 小时）

---

### Task 05 - 单测：锁定三类转场与「禁止先拼全曲再插词」
- **Task name**: v3.9.1 - Mixer 行为回归测试
- **目标**: 用可控 stub（静音/短音频或 mock `load_audio`）验证：相邻边界应用正确；**不再**出现「整段音乐先 crossfade 再插入串词」的代码路径（可通过 monkeypatch `crossfade_concat` 调用次数/顺序断言）。
- **类型**: backend
- **依赖关系**: Task 03（Task 04 后若有签名变更则依赖 Task 04）
- **Description**:
  - 覆盖最小场景：
    1) `music → voice → music`：第一段 music 与 voice 硬接；voice 与第二段 music 发生 voice→music crossfade。
    2) `music → music`（无中间 voice）：两段 music 走 song crossfade。
    3) 多 `insert_time` 的 voice 交错插入，顺序与 `insert_time` 一致。
  - 可选：段内两条 `between_tracks` 的简化 snapshot fixture（不必跑完整 ffmpeg，若现有测试已 mock）。
- **Input**: stub segments + voiceovers
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_mixing.py`
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: M（2 小时）
