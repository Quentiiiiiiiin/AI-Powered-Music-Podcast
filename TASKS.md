## 版本 v3.6（迭代十四：多 Agent 可审计落盘）

基于 PRD v3.6：在多 agent（`PlanOrchestrator`）路径上，按 **refinement 轮次 `i`** 与 **Agent 角色** 落盘可复查产物；每轮在 **Critic 完成且该轮 state 合并完成后** 再写入完整 `iteration[i]_state`。`i` 与编排层 **`control.iteration`**（进入该轮 while 时的值）全局一致。落盘为附加 I/O：**写失败须可观测且与 LLM/schema 主流程错误可区分**，默认不应因写盘失败导致整次 plan 生成失败（可选提供“严格模式”开关，若有再实现）。

---

### Task 01 - 审计目录与文件命名工具（paths / 小模块）
- **Task name**: v3.6 - 多 Agent 审计落盘路径约定
- **目标**: 在 `infra/storage` 或 `modules/theme` 下提供稳定、可测试的路径与文件名规则，满足 PRD：`iteration[i]_[agent]`、`iteration[i]_state`；单次运行使用独占子目录，避免多次调用互相覆盖。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 在 `src/podcast_ai/infra/storage/paths.py`（或新增 `src/podcast_ai/modules/theme/plan_audit.py`，二选一，避免双份逻辑）提供：
    - `get_multi_agent_audit_run_dir(base_output_dir: Path, run_id: str) -> Path`  
      建议：`{output_dir}/audit/multi_agent/{run_id}/`（`run_id` 优先取 `state["meta"]["request_id"]`，与现有 state 可追踪字段一致）。
    - `format_audit_agent_filename(iteration: int, agent_slug: str) -> str` → `iteration{iteration}_{agent_slug}.json`  
      **`agent_slug` 固定四值**：`planner` / `music_curator` / `script_writer` / `critic`（与 PRD「可识别角色名」一致；编排层展示名 `Planner` 等仅作映射）。
    - `format_audit_state_filename(iteration: int) -> str` → `iteration{iteration}_state.json`
  - 约定 **审计 JSON 内容结构**（写入 README 或模块 docstring 一行即可）：
    - 推荐单文件字段：`iteration`、`agent_slug`、`mode`（若已有）、`request_id`、`raw_llm_text`（模型 `content` 原文）、`parsed_patch`（解析后的 dict，即 merge 前 patch）、`ts_utc`（可选）。
    - `iteration[i]_state`：**完整 `PlanState` 快照**，与 `state_schema.json` 同构（即与内存 state 一致 JSON）。
  - 写文件使用 `utf-8`，`indent=2`，`ensure_ascii=False`。
- **Input**: `output_dir`、`run_id`、`iteration`、`agent_slug`、payload
- **Output**: 路径辅助函数 + 统一序列化约定说明
- **Files involved**:
  - `src/podcast_ai/infra/storage/paths.py` 和/或 `src/podcast_ai/modules/theme/plan_audit.py`（新增）
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 四个 Agent 在「解析成功后」写入 `iteration[i]_[agent]`
- **Task name**: v3.6 - Agent 侧审计钩子（raw + parsed）
- **目标**: 在 Planner / Music Curator / Script Writer / Critic 每次 LLM 返回且 **JSON 解析成功、进入 sanitize/merge 前或紧邻 merge**，写入对应审计文件；保证同一次调用只有一份落盘，便于对照 Critic `actions` 与下游 patch。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 为四个 Agent 增加可选依赖（推荐构造注入）：`audit_sink: PlanAuditSink | None`（协议或抽象：仅 `write_agent_artifact(...)`）。
  - 映射：`Planner` → `planner`，`Music Curator` → `music_curator`，`Script Writer` → `script_writer`，`Critic` → `critic`。
  - **写入用 iteration**：使用调用瞬间 `state["control"]["iteration"]` 的整型值，须与 `PlanOrchestrator` 外层循环本轮 `i` 一致（若发现 Agent 内已被改掉，改为由 Orchestrator 传入 `round_iteration: int` — 仅在不一致时做）。
  - **错误处理**：写盘异常 **捕获**；`logger.error(...)` 含路径与 `request_id`；**不包装为 AIServiceError**（避免与 LLM 失败混淆），除非后续显式实现“严格写盘模式”。
- **Input**: `raw`、解析后 `dict`、`PlanState` 片段信息
- **Output**: 磁盘上的 `iteration[i]_[agent].json`
- **Files involved**:
  - `src/podcast_ai/modules/theme/planner_agent.py`
  - `src/podcast_ai/modules/theme/music_curator_agent.py`
  - `src/podcast_ai/modules/theme/script_writer_agent.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/plan_audit.py`（或等价）
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - Orchestrator 在每轮 Critic 结束后写入 `iteration[i]_state`
- **Task name**: v3.6 - 按轮次 state 快照落盘
- **目标**: 在 `PlanOrchestrator.run` 中，每轮执行完 `_run_round_from`（即本轮 **Critic 已跑完**）且 `merge` / `assert_plan_state_valid` 成功后，写入 `iteration[i]_state.json`，其中 `i` 为本轮进入 while 时的 `iteration` 变量（与 PRD「与 control.iteration 计数一致」对齐）。
- **类型**: backend
- **依赖关系**: Task 01、Task 02（Task 02 可与本任务并行，但集成测试建议 Task 02 完成后一起做）
- **Description**:
  - 修改 `src/podcast_ai/modules/theme/orchestrator.py`：
    - 在 `try` 块内 `_run_round_from` 返回且 `assert_plan_state_valid(state)` 通过后，调用 `audit_sink.write_state_snapshot(round_iteration=i, state=state)`。
    - 在 `AIServiceError` 分支：可选写入 `iteration{i}_state_partial.json` 或仅打日志（**不要静默**）；若实现 partial，须在 docstring 说明与 PRD 验收 4 对齐。
    - 将 `audit_sink` 与 `audit_run_dir` 在 `PlanOrchestrator.__init__` 或 `run()` 内构造（推荐 `run()` 内用 `initialize_plan_state` 后的 `request_id` 创建目录，保证目录早存在）。
  - **注意**：当 `critic.pass` 为 true 时，`control.iteration` 会被 merge 为 `iteration + 1`；**state 快照仍应对齐本轮业务轮次 `i`**（使用外层变量 `i`，不要用合并后的 `control.iteration`）。
- **Input**: 本轮合并后的 `PlanState`
- **Output**: `iteration[i]_state.json`
- **Files involved**:
  - `src/podcast_ai/modules/theme/orchestrator.py`
  - `src/podcast_ai/modules/theme/plan_audit.py`
- **Estimated complexity**: M（2 小时）

---

### Task 04 - 配置开关与文档（可选但推荐）
- **Task name**: v3.6 - `multi_agent_audit.enabled` 与输出目录说明
- **目标**: 满足「不显著增加失败率」：允许关闭审计落盘（默认开启）；在 `README.md` 用 3～5 句说明目录结构、`i` 的含义、文件内字段约定。
- **类型**: backend（+ 少量文档）
- **依赖关系**: Task 02 或 Task 03（实现开关时需在 Orchestrator 构造 sink 处读取）
- **Description**:
  - 在 `src/podcast_ai/infra/config.py` 增加 `AppConfig` 或独立小块配置，例如：`multi_agent_audit_enabled: bool = True`。
  - Orchestrator：若关闭，则不注入 `audit_sink`，Agent 不写审计文件。
- **Input**: Settings
- **Output**: 可配置行为 + 简短用户说明
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `README.md`（少量）
- **Estimated complexity**: S（1 小时）

---

### Task 05 - 单测：命名、轮次对齐、写盘失败不拖垮主流程
- **Task name**: v3.6 - 审计落盘回归测试
- **目标**: 覆盖 PRD 验收 1～5 的核心点：文件存在、命名正确、`i` 与 orchestrator 轮次一致；写盘失败时主路径仍成功（若按默认非严格实现）。
- **类型**: backend
- **依赖关系**: Task 01～03
- **Description**:
  - 新增 `tests/test_plan_audit.py`（或扩展现有 `test_theme_orchestrator.py`）：
    - 使用 `tmp_path`、`stub LLM`、注入 `PlanOrchestrator`，跑 1～2 轮；断言存在 `iteration1_planner.json`、`iteration1_critic.json`、`iteration1_state.json` 等。
    - 可选：monkeypatch `Path.write_text` 抛 `OSError`，断言 plan 仍返回/或仅告警日志（与实现策略一致）。
  - **注意**：`single_agent` 模式不写此类审计（或明确不写），避免误测。
- **Input**: stub agents / mock LLM
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_plan_audit.py`（新建）
  - 可能复用：`tests/test_theme_orchestrator.py`、`tests/conftest.py`
- **Estimated complexity**: M（2-3 小时）
