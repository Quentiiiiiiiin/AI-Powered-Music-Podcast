## 版本 v4.3（迭代二十一：串词时长约束下的 crossfade 限幅）

基于 PRD v4.3：在 v4.2「串词→下一首歌 intro 对齐」动态 crossfade 的基础上，新增**串词时长约束限幅**，确保任意 `voice -> music` 边界满足：
- 优先使用 v4.2 候选值（intro 估计 + 配置）；
- 受 `voice_music_crossfade_seconds`（最小）与 `voice_music_intro_align_max_seconds`（最大）约束；
- 且**最终必须满足** `crossfade_seconds_final <= voiceover_duration_seconds`。

目标是避免 `crossfade > voiceover_duration` 导致的异常重叠与听感不稳定；该约束仅作用于「串词→歌」，**不改动**「歌→歌」与「歌→串词」语义。

---

### Task 01 - 现状审计与限幅规则固化
- **Task name**: v4.3 - voice->music 限幅规则落点确认
- **目标**: 盘点当前 v4.2 实现（`_resolve_voice_music_vm_ms`、`_join_voice_to_music_fade_music_only`、intro 估计回退），明确 v4.3 的最终规则顺序与边界条件，形成单一规则函数，避免分散判断。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 对齐 PRD v4.3 三步规则：候选值 → 配置上下限 → 串词时长上限。
  - 明确“在可行前提下”的最小值语义：当串词时长小于最小重叠时，最终值允许降到串词时长（而非抛错）。
  - 明确仅作用于 `voice -> music`，其它边界逻辑保持不变。
- **Input**: PRD v4.3 + 当前 `mixer.py`/`intro_align.py`
- **Output**: 可编码的限幅规则（含极短串词、0 时长串词、配置异常）
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: S（0.5-1 小时）

---

### Task 02 - 实现统一限幅函数（可复用、可测试）
- **Task name**: v4.3 - crossfade clamp helper
- **目标**: 在 `mixer.py` 新增单一纯函数（示例：`_clamp_voice_music_crossfade_ms(...)`），集中处理配置下限/上限与串词时长上限，输出合法 `vm_ms`，杜绝负值或越界。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 输入至少包含：候选 `vm_ms`、`voice_len_ms`、`music_len_ms`、`min_ms`、`max_ms`。
  - 输出保证：`0 <= vm_ms <= min(voice_len_ms, music_len_ms)`，并在可行前提下满足最小值约束。
  - 对配置异常（如 `min > max`）做容错（交换、取交集或回退默认）并记录 debug 日志，避免崩溃。
- **Input**: 当前 `_resolve_voice_music_vm_ms` 逻辑
- **Output**: 单点限幅逻辑，供 `_resolve_voice_music_vm_ms` 调用
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: S（1 小时）

---

### Task 03 - 接入 v4.3 到 `voice->music` 决策路径
- **Task name**: v4.3 - `_resolve_voice_music_vm_ms` 接入限幅
- **目标**: 保留 v4.2 候选值来源（intro 估计成功/失败回退）不变，在最终返回前统一走 Task 02 限幅函数，确保不会出现 `crossfade > voiceover_duration`。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 保持 intro 估计失败时回退行为不变（兼容 v4.2/v3.9.1）。
  - 在日志中增加可追溯信息：原候选值、限幅后值、触发原因（例如 `cap_by_voice_duration`）。
  - 不修改 `music->music` 的 `crossfade_concat` 分支与 `music->voice` 硬切分支。
- **Input**: 现有 `_resolve_voice_music_vm_ms`
- **Output**: v4.3 约束生效且行为可追踪
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: S（1 小时）

---

### Task 04 - 配置兼容性与冗余清理
- **Task name**: v4.3 - 配置语义保持兼容
- **目标**: 不新增强制配置项；沿用现有 `voice_music_crossfade_seconds` 与 `voice_music_intro_align_max_seconds` 语义，确保旧配置可直接运行；同时删除重复限幅代码，保持实现简洁。
- **类型**: backend
- **依赖关系**: Task 03
- **Description**:
  - 核对 `infra/config.py`、`core/models.py`、`pipeline.py` 透传字段是否已满足 v4.3；若无需新字段则不改结构。
  - 清理 `_join_voice_to_music_fade_music_only` 与调用方重复做同类 `min(...)` 的冗余判断（保留必要防御）。
  - 补充注释说明“v4.3 约束顺序”，减少后续维护歧义。
- **Input**: 当前配置与 mix 调用链
- **Output**: 配置零破坏，代码路径更集中
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - `src/podcast_ai/infra/config.py`（仅在必要时）
  - `src/podcast_ai/core/models.py`（仅在必要时）
  - `src/podcast_ai/core/pipeline.py`（仅在必要时）
- **Estimated complexity**: S（0.5-1 小时）

---

### Task 05 - 单测覆盖极短串词与限幅触发场景
- **Task name**: v4.3 - crossfade 限幅回归测试
- **目标**: 新增/更新测试，验证最终 `vm_ms` 受三重约束，尤其是 `crossfade <= voice_duration`；覆盖极短串词与估计值过大场景，保证流程稳定不崩溃。
- **类型**: backend
- **依赖关系**: Task 03（Task 04 完成后同步调整断言）
- **Description**:
  - 核心场景：
    1) `candidate > voice_duration` 时被截断到串词时长；
    2) `voice_duration < min_crossfade` 时返回串词时长（可行前提）；
    3) intro 估计失败回退后仍满足限幅；
    4) `music->music` 行为不变（防回归）。
  - 使用 mock intro 估计，避免引入大音频分析成本。
- **Input**: `tests/test_mixing.py` 现有用例
- **Output**: `pytest` 通过，锁定 PRD 验收 1~4
- **Files involved**:
  - `tests/test_mixing.py`
- **Estimated complexity**: M（1.5-2 小时）

---

### Task 06 - 文档与验收口径对齐（最小变更）
- **Task name**: v4.3 - 规则文档化与示例更新
- **目标**: 将 v4.3 限幅规则补充到开发文档/注释中，明确“仅 voice->music 生效、旧配置兼容、不新增强制项”，避免误解为全局 crossfade 改写。
- **类型**: backend
- **依赖关系**: Task 03
- **Description**:
  - 更新 `README.md` 或 `mixer.py` 顶部注释中的转场说明（按仓库现有风格最小改动）。
  - 保留一条“调试日志示例字段”说明，便于后续定位限幅触发。
- **Input**: v4.3 最终实现
- **Output**: 文档与实现一致
- **Files involved**:
  - `README.md`（可选）
  - `src/podcast_ai/modules/mixing/mixer.py`
- **Estimated complexity**: S（0.5 小时）
