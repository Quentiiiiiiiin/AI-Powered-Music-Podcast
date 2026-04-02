## 版本 v3.1（迭代九：阶段一模式可选 + 统一 state_schema.json 输出）

基于 PRD v3.1：
1) 阶段一支持 `single_agent` / `multi_agent` 模式切换（默认值可由实现指定，但必须可切换）。
2) 无论单/多 agent，阶段一最终输出文件均为 `state.json`，且与 `state_schema.json` **同结构**；单 agent 不涉及字段以 `null` 填充。
3) 若 schema 校验失败，必须报错并阻断后续阶段。

---

### Task 01 - plan-episode 暴露模式选择（single_agent / multi_agent）
- **Task name**: v3.1 CLI / pipeline 模式选择接入
- **目标**: 让用户能够通过配置或命令参数选择 `single_agent` 或 `multi_agent`，并把选择结果贯通到阶段一生成逻辑。
- **类型**: api
- **依赖关系**: 无
- **Description**:
  - 在 `src/podcast_ai/cli.py` 的 `plan-episode` 命令增加一个参数（示例：`--agent-mode`，choices: `single_agent|multi_agent`）。
  - 更新 `src/podcast_ai/core/pipeline.py::plan_episode` 增加入参（或读取同名配置），并将其映射到 `ThemePlanner.generate_plan(..., use_orchestrator=...)`。
  - 保持外部接口默认值不破坏现有体验（未传入时走默认实现）。
- **Input**:
  - `EpisodeRequest`（topic/duration/language/output_dir）
  - 用户选择的 `agent_mode`
- **Output**:
  - 阶段一可切换生成模式
  - 后续任务能依此选择正确的 state 生成路径
- **Files involved**:
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/theme/llm_planner.py`
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - state.json 落盘路径与读写工具
- **Task name**: episode 输出 state.json 的 storage 工具
- **目标**: 提供 `state.json` 的稳定落盘路径与读写函数，供阶段一输出与阶段二读取使用。
- **类型**: backend
- **依赖关系**: Task 01（需要确定模式切换的阶段一输出流程）
- **Description**:
  - 在 `src/podcast_ai/infra/storage/paths.py` 新增：
    - `get_state_path(episode_root: Path) -> Path`（文件名明确为 `state.json`）
    - `save_state_json(state: PlanState, output_dir/episode_id ...)`
    - `load_state_json(path: Path) -> PlanState`
  - 明确目录结构与现有 `plans/` 文件夹的关系（例如放在 `episodes/{episode_id}/plans/state.json`）。
- **Input**:
  - `PlanState`
  - `episode_id` / `episode_root`
- **Output**:
  - `state.json` 能正确写入并在阶段二读取
- **Files involved**:
  - `src/podcast_ai/infra/storage/paths.py`
  - `src/podcast_ai/modules/theme/state.py`（仅用于类型引用）
- **Estimated complexity**: S（1-2 小时）

---

### Task 03 - 阶段一统一输出 state.json（单 agent & 多 agent）
- **Task name**: PlanState 生成与 EpisodePlan 转换解耦
- **目标**: 无论 `single_agent` 还是 `multi_agent`，阶段一都返回并落盘 `state.json`；多 agent 走 Orchestrator 的 PlanState，单 agent 需要把 EpisodePlan 映射为 PlanState，并对不涉及字段填 null。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 让 `ThemePlanner`（或阶段一 pipeline 内部）同时支持两条路径：
    - `multi_agent`: 直接调用 `PlanOrchestrator.run(request)` 获取 PlanState
    - `single_agent`: 走现有单次 LLM 生成 EpisodePlan，然后映射成 PlanState（需要补齐 state_schema.json 要求的字段层级；不涉及的 `critic/control/...` 以 `null` 或不参与约束的默认形态填充）
  - 在保存前做 schema 契约校验；失败则抛出明确错误并阻断。
  - 同时保留 `playlist.md` 输出（不改动或最小调整）。
- **Input**:
  - `EpisodeRequest`
  - `agent_mode`
- **Output**:
  - `state.json` 与 `playlist.md` 落盘成功
  - schema 校验通过才允许返回给 CLI
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/theme/orchestrator.py`
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/modules/theme/state.py`
  - `src/podcast_ai/infra/storage/paths.py`
- **Estimated complexity**: L（3 小时）

---

### Task 04 - 与 state_schema.json 的结构一致校验（仅阶段一）
- **Task name**: state_schema.json 同构校验与可定位错误
- **目标**: 确保 state.json 真正与 `state_schema.json` 同结构，并在失败时给出可定位原因；同时允许单 agent 的“不涉及字段”为 null 的合法形态。
- **类型**: backend
- **依赖关系**: Task 03
- **Description**:
  - 在 `src/podcast_ai/modules/theme/state.py` 增加/强化校验逻辑：
    - 读取 `state_schema.json` 作为结构模板（或以等价的深度 key/类型检查实现）
    - 校验字段层级一致；数组/对象字段类型匹配；允许单 agent 模式下指定字段取 null（需要明确允许的字段集合）
  - 对校验失败抛出清晰错误信息（字段路径 + 期望/实际）。
- **Input**:
  - PlanState
  - state_schema.json 模板
  - agent_mode（用于决定允许哪些 null）
- **Output**:
  - `validate_state_conforms_to_schema(...)`（或等价函数）
  - 校验失败可读错误
- **Files involved**:
  - `src/podcast_ai/modules/theme/state.py`
  - `state_schema.json`
- **Estimated complexity**: L（3 小时）

---

### Task 05 - 单元测试：v3.1 阶段一模式切换 + state.json 生成/校验
- **Task name**: v3.1 - 阶段一回归测试
- **目标**: 自动化覆盖本次迭代的阶段一验收点：模式可切换、最终产物为 schema 同构 `state.json`，校验失败可定位并阻断。
- **类型**: backend
- **依赖关系**: Task 01, Task 03, Task 04
- **Description**:
  - 新增/更新测试：
    1) `single_agent` 与 `multi_agent` 两种模式下 `plan_episode` 都产出 `state.json`
    2) `state.json` 可通过与 `state_schema.json` 的同构校验
    3) 故意构造不合法字段的场景下，校验失败抛出可定位错误并阻断（不进入后续阶段；无需改 stage2 代码）
- **Input**:
  - mock LLM（单 agent）/ mock agents（multi agent）
  - state_schema.json
- **Output**:
  - `pytest` 通过
- **Files involved**:
  - `tests/test_pipeline.py`（或新增测试文件）
  - `tests/test_theme_state.py`
  - （可选）`tests/test_theme_planner.py`
- **Estimated complexity**: M（2-3 小时）

