## 版本 v2.2（迭代七：ThemePlanner prompt 强约束升级）

基于 PRD v2.2：通过更大篇幅、更强约束的 prompt 提升 LLM 返回 EpisodePlan 的结构正确性与可执行性；验收重点是 `ThemePlanner` 返回内容能被稳定解析为严格 JSON，并且字段结构（snake_case/层级/可空字段）与代码期望一致。

---

### Task 01 - 强约束 prompt 与代码期望 schema 对齐
- **Task name**: v2.2 - 更新 `build_theme_planner_messages` 约束与示例
- **目标**: 让 `src/podcast_ai/modules/theme/prompts.py` 中的 prompt 明确约束 EpisodePlan 的 JSON 字段结构与 `ThemePlanner.generate_plan` 的解析期望一致，减少“字段缺失/类型不符”导致的不可执行 episode plan。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 更新 `build_theme_planner_messages` 的 system/user 文本：
    - 明确 `segments[*].host_script` 必须给出（且使用 request.language）
    - 明确 `target_playlist[*].recommended_tracks` 的类型（建议仍为 `string[]`，并与代码解析一致）
    - 如 prompt 引入 `host_script_between_songs`，需明确其为可选/可空字段（且不影响当前代码只消费 `host_script` 的事实）
    - 如果 PRD 要求 `search_hints` 层级一致，prompt 中加入 `search_hints` 的最小结构（允许 `{}`）
  - 同步修正 prompt 中的 JSON 示例，使其与当前 `llm_planner.py` 的字段名与嵌套层级一致（减少解析歧义）。
- **Input**:
  - `PRD.md` v2.2 约束要点
  - `src/podcast_ai/modules/theme/prompts.py` 当前 prompt
  - `src/podcast_ai/modules/theme/llm_planner.py` 当前解析逻辑
- **Output**:
  - prompt 内容升级完成
  - 约束与示例 JSON 与解析期望一致
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts.py`
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 为 v2.2 解析稳定性补齐单测（严格 JSON + 字段结构）
- **Task name**: v2.2 - ThemePlanner.parse 回归测试
- **目标**: 给 `ThemePlanner.generate_plan` 增加单测覆盖 v2.2 验收点，确保在 prompt 强约束后，LLM 返回严格 JSON 仍能被稳定解析；同时校验字段结构（snake_case、必要字段存在、可空字段为 null）。
- **类型**: backend
- **依赖关系**: Task 01（prompt 对齐后更容易生成符合 schema 的 JSON；但测试本身可独立）
- **Description**:
  - 新增 `tests/test_theme_planner.py`（或在现有测试文件中追加）：
    - 用 mock LLM 返回一段“符合 v2.2 强约束”的 JSON（包含至少 2 个 segment、每段至少 1 条 target_playlist；可包含 `overall_bpm_range=null`、`bpm_range=null`、`host_script_between_songs=null` 等）
    - 断言 `ThemePlanner.generate_plan` 输出的 `EpisodePlan`：
      - `segments` 不为空
      - 每个 segment 有 `host_script`（非空字符串）
      - `target_playlist` 正确解析为 `PlaylistItem[]`
      - `search_hints` 在缺失/为空时能稳定得到 `{}`（不抛异常）
      - 总时长 `sum(segments[*].target_duration_seconds)` 在允许浮动范围内（按 prompt 设定的 ±10% 规则做断言）
- **Input**:
  - 目标 JSON 样例（mock LLM 输出）
  - 期望的语言字段（request.language）
- **Output**:
  - `pytest` 通过
  - 单测覆盖 v2.2 的关键解析稳定性验收点
- **Files involved**:
  - `tests/test_theme_planner.py`（新增）
  - `src/podcast_ai/modules/theme/llm_planner.py`（若仅需调整测试用 mock 注入方式则相关）
- **Estimated complexity**: S（1-2 小时）

---

### Task 03 -（可选）轻量解析校验与错误信息增强，便于定位 prompt 偏差
- **Task name**: v2.2 - 解析失败的可定位错误信息
- **目标**: 若强约束 prompt 仍可能偶发输出不符合 schema，尽量让 `ThemePlanner.generate_plan` 在解析/校验阶段给出可定位原因，而不是仅抛“JSON 不是有效对象”。
- **类型**: backend
- **依赖关系**: Task 02（由单测暴露的失败模式反推需要增强的点）
- **Description**:
  - 在 `ThemePlanner.generate_plan` 解析逻辑中做极小幅度校验/报错增强（例如当 `segments` 缺失、`target_playlist` 不是 list、或必填字段缺失时抛更明确的 `AIServiceError`，并附带索引定位）
  - 保持容错策略不过度收紧，避免引入新的不稳定失败点。
- **Input**:
  - 单测/实际运行中出现的解析失败样本
  - PRD/验收需要的失败提示粒度
- **Output**:
  - 错误信息更清晰
  - 不破坏现有解析容错
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - （可选）`src/podcast_ai/core/exceptions.py`
- **Estimated complexity**: XS（0.5-1 小时）

