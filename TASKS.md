## 版本 v3.7（迭代十五：单 Agent 输出对齐 State Schema 子集）

基于 PRD v3.7：阶段一 `single_agent` 不再输出 EpisodePlan 风格 JSON，而是直接输出 State 子集结构，顶层仅包含：
`schema_version`、`meta`、`global_constraints`、`plan`、`segments`（不包含 `critic`、`control`）。
同时单 agent 也对齐结构化输出主路径（`response_format/json_schema`），阶段一产物文件名统一为 `state.json`。

---

### Task 01 - 定义单 Agent 的 State 子集 Schema（结构化输出契约）
- **Task name**: v3.7 - single_agent `response_format` schema
- **目标**: 为单 agent 的 Theme Planner 定义严格 JSON Schema，确保模型直接按 State 子集输出，禁止 `critic/control` 与额外顶层字段。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 在 `src/podcast_ai/modules/theme/agent_response_schemas.py` 新增单 agent 专用 schema（如 `SINGLE_AGENT_STATE_SUBSET_SCHEMA`）：
    - 顶层 required：`schema_version/meta/global_constraints/plan/segments`
    - `additionalProperties: false`
    - `critic/control` 不在 properties 中（即不允许输出）
  - 复用现有 `build_openrouter_response_format(...)`，为单 agent 生成 `response_format`。
  - 与 `state_schema.json` 对齐字段命名/层级；仅裁剪顶层，不引入新字段。
- **Input**: `state_schema.json` 与现有多 agent schema 约束
- **Output**: 可直接用于 single agent 调用的 strict schema
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - 参考：`state_schema.json`
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 调整单 Agent Prompt 与解析目标（从 EpisodePlan 转为 State 子集）
- **Task name**: v3.7 - `build_theme_planner_messages` 输出契约升级
- **目标**: 让单 agent 提示词与解析逻辑都面向 State 子集，而非 EpisodePlan 风格字段（如 `host_script/target_playlist`）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 更新 `src/podcast_ai/modules/theme/prompts.py` 中 `build_theme_planner_messages(...)`：
    - 明确要求输出 State 子集 JSON；
    - 明确禁止 `critic/control`；
    - 强调字段与层级（snake_case、required）。
  - 在 `src/podcast_ai/modules/theme/llm_planner.py` 的单 agent 路径中：
    - 解析目标改为 `PlanState` 子集 dict，而不是先解析 EpisodePlan 风格再转换；
    - 去掉/收敛仅服务 EpisodePlan 风格的解析分支（避免双轨心智）。
  - 保持 `EpisodePlan` 对外兼容：需要返回 `EpisodePlan` 的地方由统一转换函数从 State 子集构造，而不是反向拼装。
- **Input**: EpisodeRequest、single_agent 模式
- **Output**: 单 agent 直接产出 State 子集内存对象
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/llm_planner.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - 单 Agent 接入结构化输出（response_format/json_schema）
- **Task name**: v3.7 - ThemePlanner single_agent structured output
- **目标**: 单 agent 调用与多 agent 对齐，启用 `response_format` 严格约束，降低格式漂移与解析失败。
- **类型**: backend
- **依赖关系**: Task 01、Task 02
- **Description**:
  - 在 `llm_planner.py` 的 single_agent 调用中，按 `should_use_structured_output(...)` 判定是否附带 `response_format`；
  - 启用时传入 Task 01 的 schema（`json_schema.strict=true`）；
  - 保持错误处理：若结构化输出不可用/返回非约定结构，抛清晰错误（含请求标识），不静默回退。
- **Input**: LLMConfig、single_agent messages
- **Output**: 单 agent 正常路径可直接解析为约定 State 子集
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/infra/config.py`（只读复用 `should_use_structured_output`）
- **Estimated complexity**: S（1-2 小时）

---

### Task 04 - State 校验拆分：支持“单 Agent 子集”专用验证
- **Task name**: v3.7 - single_agent subset schema validation
- **目标**: 将当前 `validate_state_conforms_to_schema(..., agent_mode="single_agent")` 的“critic/control 可为 null”逻辑，调整为“single_agent 子集不含 critic/control 也合法”的显式校验语义。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 在 `src/podcast_ai/modules/theme/state.py` 增加单 agent 子集校验入口（示例）：
    - `validate_state_subset_for_single_agent(state)` 或在现有函数增加 `schema_variant="single_agent_subset"` 参数；
  - 校验规则：
    - 顶层必须且仅允许 `schema_version/meta/global_constraints/plan/segments`
    - 不允许出现 `critic/control`
    - 子结构类型仍与 `state_schema.json` 对应节点一致
  - 保持多 agent 校验行为不变（避免影响 v3.6）。
- **Input**: single_agent state dict
- **Output**: 单 agent 子集校验通过/失败（可定位错误）
- **Files involved**:
  - `src/podcast_ai/modules/theme/state.py`
  - 参考：`state_schema.json`
- **Estimated complexity**: M（2 小时）

---

### Task 05 - 阶段一链路同步：plan_episode / 落盘 / 调用契约
- **Task name**: v3.7 - stage1 single_agent contract sync
- **目标**: 同步阶段一受影响路径，保证 single_agent 产物与文件命名契约一致（`state.json`），且不破坏 multi_agent。
- **类型**: api
- **依赖关系**: Task 02、Task 03、Task 04
- **Description**:
  - `src/podcast_ai/core/pipeline.py`：
    - single_agent 下调用新的 single-agent state 生成与校验入口；
    - 保持落盘文件名 `state.json`（现有 `save_state_json` 复用即可）；
    - 若仍需返回 `EpisodePlan` 给 CLI 展示，使用统一转换函数从 state 子集生成。
  - `src/podcast_ai/cli.py`：
    - 文案与帮助信息同步（说明 single_agent 输出已是 state 子集，统一落 `state.json`）。
  - 若阶段二仍读取 plan JSON，本次迭代不强行改阶段二；仅保证阶段一契约自洽并可独立验证。
- **Input**: `plan-episode --agent-mode single_agent`
- **Output**: 阶段一输出 `state.json`（单 agent 子集），并可返回/展示必要路径信息
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/infra/storage/paths.py`（通常无需改，仅确认）
- **Estimated complexity**: M（2-3 小时）

---

### Task 06 - 测试与回归：单 Agent 子集契约 + 不破坏多 Agent
- **Task name**: v3.7 - single_agent subset e2e regression
- **目标**: 覆盖 v3.7 核心验收点，并回归 multi_agent 行为不受影响。
- **类型**: backend
- **依赖关系**: Task 01-05
- **Description**:
  - 新增/更新测试：
    1) `single_agent` 生成的 state 顶层仅含五个 key（无 `critic/control`）；
    2) single_agent 调用在启用结构化输出时携带正确 `response_format`；
    3) 阶段一落盘文件名为 `state.json`；
    4) `multi_agent` 现有测试继续通过（特别是 `critic/control` 仍存在、orchestrator 流程不变）。
  - 优先复用现有：
    - `tests/test_llm_structured_output.py`
    - `tests/test_pipeline.py`
    - `tests/test_theme_state.py`
- **Input**: stub LLM / mock transport / tmp_path
- **Output**: `pytest` 通过，v3.7 契约受控
- **Files involved**:
  - `tests/test_llm_structured_output.py`
  - `tests/test_pipeline.py`
  - `tests/test_theme_state.py`
  - （可选）新增 `tests/test_single_agent_state_subset.py`
- **Estimated complexity**: M（2-3 小时）

