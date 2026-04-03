## 版本 v3.2（迭代十：Agent 输出 JSON 修复与标准化）

基于 PRD v3.2：在每个 Agent 的 `run` 返回后、进入 `json.loads` 之前，先对 raw 文本进行 JSON 修复与标准化；若仍无法合法解析，则明确报错并中断（不进入后续阶段）。

---

### Task 01 - 新增共享 JSON 修复与标准化工具
- **Task name**: v3.2 - `repair_and_standardize_json(raw)`
- **目标**: 实现一个最小可用的 JSON 修复工具，用于将常见的 LLM raw 输出（多余前后文本、代码块包裹、智能引号、尾随逗号等）修复成“可被 `json.loads` 解析”的 JSON 字符串。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 新增文件：`src/podcast_ai/modules/theme/json_repair.py`
  - 提供函数（示例命名）：
    - `standardize_llm_json_text(raw: str) -> str`（统一去 code fence、替换智能引号、清理前后文本）
    - `repair_and_standardize_json(raw: str) -> str`（在 standardize 基础上增加“提取 JSON 对象/数组子串、去尾随逗号”等修复步骤）
  - 修复策略至少覆盖 PRD 提到的常见问题类别：
    - 代码块/前后文本夹杂
    - 引号不完整/智能引号（可做替换）
    - 尾随逗号（可做正则移除）
  - 当修复无法达到“可解析 JSON”时，返回修复后的文本并让调用方触发失败（不要静默吞错）。
- **Input**: Agent 返回的 `raw: str`
- **Output**: 修复后的 JSON `str`（调用方将继续执行 `json.loads`）
- **Files involved**:
  - `src/podcast_ai/modules/theme/json_repair.py`（新增）
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 四个 Agent 接入 JSON 修复（run 级别，失败显式报错）
- **Task name**: v3.2 - Planner/Curator/Writer/Critic JSON 修复集成
- **目标**: 在每个 Agent `run` 中，raw 返回后、`json.loads` 之前执行 JSON 修复与标准化；修复后仍无法解析则抛出明确 `AIServiceError`（并建议保存 debug raw 供定位）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 修改文件（四个 Agent）：
    - `src/podcast_ai/modules/theme/planner_agent.py`
    - `src/podcast_ai/modules/theme/music_curator_agent.py`
    - `src/podcast_ai/modules/theme/script_writer_agent.py`
    - `src/podcast_ai/modules/theme/critic_agent.py`
  - 在各自 `run` 中替换/增强现有逻辑：
    - raw -> `repair_and_standardize_json(raw)` -> `json.loads(...)`
    - 对于脚本写入错误日志与 debug dump：统一到“所有 agent 同策略保存 raw + 修复前后文本/异常类型”，便于对齐排查（ScriptWriter 目前已有 dump，可把同样能力补齐到其他 agent）。
  - 保持现有“越权字段 sanitize / patch 合法性校验”逻辑不变；JSON 修复只影响 json.loads 前的输入。
- **Input**: LLM raw 文本
- **Output**:
  - 修复成功：继续进入 patch sanitize + merge + schema 校验
  - 修复失败：抛出清晰错误并阻断 plan_episode
- **Files involved**:
  - `src/podcast_ai/modules/theme/{planner_agent,music_curator_agent,script_writer_agent,critic_agent}.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - 单测：覆盖 JSON 修复成功/失败与 Agent 解析路径
- **Task name**: v3.2 - JSON 修复单测 + Agent 回归
- **目标**: 用可控 stub LLM 覆盖两类情况：
  1) raw 有常见格式问题但能被修复并成功进入后续解析与 schema 校验；
  2) raw 修复后仍不合法时，Agent 抛出 `AIServiceError` 且错误信息可定位。
- **类型**: backend
- **依赖关系**: Task 01、Task 02
- **Description**:
  - 新增/更新测试文件：
    - `tests/test_json_repair.py`：直接测试 `repair_and_standardize_json`
    - `tests/test_theme_agents_json_repair.py`（或追加到现有主题测试文件）：
      - 为每个 agent 准备最小 state（可复用 `initialize_plan_state`）
      - 使用 stub LLM 返回“带前后文本 + code fence + 尾随逗号/智能引号”等坏 JSON
      - 断言：成功路径不会抛错；失败路径抛 `AIServiceError`
  - 测试断言尽量聚焦：
    - `json.loads` 是否能成功（通过后续 state 合并结果来间接验证）
    - 失败错误消息包含 agent 名称或“JSON 修复/解析失败”的关键字
- **Input**: stub LLM 返回的 malformed JSON 示例
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_json_repair.py`（新增）
  - `tests/test_theme_agents_json_repair.py`（新增或追加）
  - 可能复用：`tests/test_theme_state.py` 的 fixtures/工具
- **Estimated complexity**: M（2-3 小时）

---

### Task 04（可选）- ThemePlanner single_agent 路径复用 JSON 修复工具
- **Task name**: v3.2（可选）- `ThemePlanner.generate_plan` 也做 JSON 修复
- **目标**: 保证 single_agent 模式下也能更鲁棒地处理 LLM raw 的 code fence/前后文本等格式问题（不改变契约与 schema）。
- **类型**: backend
- **依赖关系**: Task 01（复用工具）
- **Description**:
  - 在 `src/podcast_ai/modules/theme/llm_planner.py`：
    - raw -> `repair_and_standardize_json(raw)` -> `json.loads`
  - 若不需要额外回归可略过此 task。
- **Input**: ThemePlanner raw 文本
- **Output**: 更稳定的 JSON 解析
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
- **Estimated complexity**: XS（0.5-1 小时）

