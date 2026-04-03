## 版本 v3.5（迭代十三：代码可读性与冗余校验清理）

基于 PRD v3.5：清理“可维护性/可读性”问题，重点包括：
1) 从四个 Agent 的主路径移除 `json repair`（仅在明确的可选兜底场景下启用，并有清晰注释/开关）。
2) 精简 JSON/Schema 校验逻辑：尽量做到“解析一次、校验一次”，并把解析/校验职责集中到统一入口。
3) 降低散落的 try/except、日志与 dump 的重复代码量；保证职责边界更清晰。
4) 行为不回退：plan 生成成功率与稳定性不显著下降；在关键解析与校验工具函数上补充/完善少量单测。

---

### Task 01 - 统一 JSON 解析入口（主路径禁用 repair）
- **Task name**: v3.5 - `parse_agent_json_response(...)`
- **目标**: 新增一个小而清晰的解析工具函数，统一处理四个 Agent 的 raw -> JSON -> dict，并把“structured 主路径禁用 repair / 非 structured 可选 repair 兜底”的策略集中在一个地方。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 新增文件（建议）：`src/podcast_ai/modules/theme/agent_json_parser.py`
  - 导出一个函数（示例签名）：
    - `parse_agent_json_response(*, agent_label: str, raw: str, state: PlanState, structured: bool, allow_repair_fallback: bool) -> dict[str, Any]`
  - 解析策略（与 v3.5 验收对齐）：
    - `structured=True`（OpenRouter strict 主路径）：只做“轻量标准化”（例如 `.strip()`、必要的 code fence 去除/智能引号替换若仍需要），然后 **直接 `json.loads`**。
    - `structured=False`：先直接 `json.loads`；仅在失败且 `allow_repair_fallback=True` 时才调用 `repair_and_standardize_json` 再解析。
    - 若仍失败：抛 `AIServiceError`，错误信息包含 `agent_label` + `request_id`(来自 `state.meta`) + `iteration`(来自 `state.control`) + 失败原因；debug dump（raw 与 repaired 视情况）仍可保留，但只在兜底发生或解析完全失败时产出（避免 noise）。
  - 让解析返回值保证：
    - JSON 顶层必须是 `dict`（否则抛错）
    - 不做 agent-specific 的字段 sanitize（由调用方继续做）
  - 在函数内部给出明确注释：为什么 structured 主路径禁用 repair（v3.5 PRD）。
- **Input**: raw + structured 标记 + state
- **Output**: 解析后的 `dict`
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_json_parser.py`（新增）
  - 复用 `src/podcast_ai/modules/theme/json_repair.py` 中的标准化/repair/dump 能力
- **Estimated complexity**: M（2-3 小时）

---

### Task 02 - 四个 Agent 接入统一解析入口，去冗余（去掉 repair 主路径调用）
- **Task name**: v3.5 - Agent 解析逻辑精简
- **目标**: 修改四个 Agent，使它们不再在主路径中直接调用 `repair_and_standardize_json` / `dump_json_repair_debug`；改为统一调用 Task 01 的解析入口，从而减少重复 try/except/log/dump 并满足 v3.5 第 1 条验收。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 修改文件：
    - `src/podcast_ai/modules/theme/planner_agent.py`
    - `src/podcast_ai/modules/theme/music_curator_agent.py`
    - `src/podcast_ai/modules/theme/script_writer_agent.py`
    - `src/podcast_ai/modules/theme/critic_agent.py`
  - 每个 Agent 的 `run` 解析段替换为：
    - `structured = should_use_structured_output(self._settings.llm)`
    - `data = parse_agent_json_response(... structured=structured, allow_repair_fallback=not structured)`
    - 后续保持现有 `sanitize_*_patch` + `merge_plan_state` 流程不动（避免语义回退）
  - 把四个 Agent 中高度重复的：
    - `try: repaired = ...; data = json.loads(...) except ... dump/log raise`
    - `request_id/iteration/so_note` 拼装
    统一收敛到 Task 01 的解析入口里。
  - 仅在 parser 触发兜底/失败时写 debug dump（避免所有失败都落太多噪音文件）。
- **Input**: LLM raw 文本
- **Output**: 更新后的状态（由 sanitize/merge 决定）
- **Files involved**:
  - 四个 agent 文件
  - 新增的 `agent_json_parser.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - 去掉 Agent 内重复的 `PlanState` schema 校验（由 orchestrator 集中）
- **Task name**: v3.5 - 校验职责集中到 orchestrator
- **目标**: 减少冗余校验次数与代码噪音。因为当前 Agents 只由 `PlanOrchestrator` 调用且 orchestrator 在每个迭代轮次后会 `assert_plan_state_valid`，因此可移除 Agents 内部的重复 `assert_plan_state_valid(state)` / `assert_plan_state_valid(next_state)`，让职责更清晰。
- **类型**: backend
- **依赖关系**: Task 02（确保解析/合并后逻辑仍可靠）
- **Description**:
  - 修改四个 Agent：删除（或至少移除）这些调用：
    - `assert_plan_state_valid(state)`（run 起始处）
    - `assert_plan_state_valid(next_state)`（run 结束前）
  - 在 `src/podcast_ai/modules/theme/orchestrator.py` 保持当前的：
    - while 循环中每轮结束后的 `assert_plan_state_valid(state)`（以及 AIServiceError 情况下的状态合并后校验）
  - 保留 agent-specific 的必要校验（例如 segments_count==0 等语义检查、sanitize 白名单校验、语言一致性启发式校验等），避免“只靠 orchestrator 兜底”导致语义缺失。
- **Input**: PlanState
- **Output**: 合并后的 PlanState（由 orchestrator 进行统一校验）
- **Files involved**:
  - 四个 agent 文件
  - `src/podcast_ai/modules/theme/orchestrator.py`（只读确认）
- **Estimated complexity**: S（1-2 小时）

---

### Task 04 - 单测：验证 structured 主路径不调用 repair，失败路径可定位
- **Task name**: v3.5 - 解析器行为与回归测试
- **目标**: 覆盖 v3.5 验收点中“解析/校验职责集中 + 不回退”的关键行为。
- **类型**: backend
- **依赖关系**: Task 01、Task 02
- **Description**:
  - 新增/更新测试（建议新增一份小而聚焦的测试文件，例如 `tests/test_agent_json_parser.py`）：
    1. `structured=True` 时：
       - raw 是合法 JSON：应成功返回 dict，且不触发 `repair_and_standardize_json`（可用 monkeypatch 断言调用次数为 0）。
       - raw 是非法 JSON：应抛 `AIServiceError`，错误信息包含 agent_label + request_id + iteration；并且同样断言 repair 未被调用。
    2. `structured=False` 时：
       - raw 是可由 `repair_and_standardize_json` 修复的 malformed JSON：应成功解析（验证兜底仍可用但不是主路径）。
  - 现有的 `tests/test_theme_agents_json_repair.py` 可以保留其意义（大概率 structured=False 场景仍会走兜底），但需根据 Task 03 移除/调整 Agents 内部 assert 是否影响期望。
  - 关键断言尽量聚焦在：
    - 是否调用 repair（或不调用）
    - 是否抛出正确异常与可定位字段
- **Input**: 构造 raw + stub LLM
- **Output**: `pytest` 通过
- **Files involved**:
  - 新增/更新 `tests/test_agent_json_parser.py`
  - 必要时更新 `tests/test_theme_agents_json_repair.py`
  - 可能复用 `tests/test_llm_structured_output.py` 的 stub/mock transport
- **Estimated complexity**: M（2-3 小时）

