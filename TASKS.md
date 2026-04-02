## 版本 v3.0（迭代八：阶段一 Episode Plan 多 Agent 化）

基于 PRD v3.0 与 `6.3 Agent 输入/输出契约表`、`ARCHITECTURE.md`：阶段一从单次模型调用升级为多 Agent Pipeline（Planner / Music Curator / Script Writer / Critic），以共享 `PlanState` 为唯一事实源，通过 Critic 结构化反馈驱动 2~3 次有限回修迭代。

---

### Task 01 - 定义 PlanState schema 与契约校验工具
- **Task name**: v3.0 - `PlanState` 数据结构与读写约束落地
- **目标**: 落地共享状态模型（`meta / global_constraints / plan / segments / critic / control`）与最小校验工具，确保所有 Agent 输入/输出都基于统一 schema。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 新增 `modules/theme/state.py`，定义 `PlanState` 与子结构（含 `schema_version`、`control.max_iterations`、`control.next_agent`、`control.last_updated_by`）。
  - 提供基础函数：初始化 state、字段级 merge、必填字段检查、schema 合法性检查。
  - 明确默认值：`control.max_iterations=3`。
- **Input**:
  - PRD v3.0 state schema 草案
  - 6.3 Agent 读写契约
- **Output**:
  - 可被 4 个 Agent 与 Orchestrator 共享的结构化 state
  - 基础校验工具可检测缺字段/类型不符
- **Files involved**:
  - `src/podcast_ai/modules/theme/state.py`
  - `src/podcast_ai/core/models.py`（若补充 PlanState/EpisodePlan 可选追踪字段）
- **Estimated complexity**: M（2-3 小时）

---

### Task 02 - Planner Agent（全局结构与段落骨架）
- **Task name**: v3.0 - 实现 Planner Agent
- **目标**: 实现只负责“全局结构与段落骨架”的 Planner，严格遵守契约仅写允许字段。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 在 `modules/theme/llm_planner.py`（或拆分 `planner.py`）实现 Planner agent 调用：
    - 读取 `meta/global_constraints/critic.issues`
    - 写入 `plan.segments_design`、`plan.emotion_curve`、`segments[*].name/target_duration_seconds/bpm_range/mood/segment_design`、`control.next_agent`
  - 增加“禁止写字段”保护（越权字段忽略或报错）。
- **Input**:
  - `EpisodeRequest`
  - 共享 `PlanState` 初始状态
- **Output**:
  - 结构完整的段落骨架 state
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - Music Curator Agent（按段落填充 playlist）
- **Task name**: v3.0 - 实现 Music Curator Agent
- **目标**: 基于 Planner 产出的段落设计填充 `segments[*].playlist`，并保证曲目顺序可执行；只写允许字段。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 新增 `modules/theme/music_curator.py`，读取段落 mood/bpm/segment_design 与 critic 历史问题。
  - 写入 `segments[*].playlist`（推荐曲目与顺序）。
  - 添加字段保护，禁止改写 `plan` 主结构、`segments[*].script`、`critic.*` 等。
- **Input**:
  - Planner 阶段后的 `PlanState`
- **Output**:
  - 含每段 playlist 的 state
- **Files involved**:
  - `src/podcast_ai/modules/theme/music_curator.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 04 - Script Writer Agent（段前串词与段内串词）
- **Task name**: v3.0 - 实现 Script Writer Agent
- **目标**: 按 `meta.language` 与 playlist 结构写入脚本字段（段前串词与段内过渡串词），并保持语言一致性。
- **类型**: backend
- **依赖关系**: Task 01, Task 03
- **Description**:
  - 新增 `modules/theme/script_writer.py`。
  - 读取 `meta.language`、`segments[*].playlist/mood`、critic 历史反馈。
  - 写入 `segments[*].script.segment_intro`、`segments[*].script.between_tracks`。
  - 校验脚本语言一致性（最小规则即可）并禁止越权改写 playlist。
- **Input**:
  - Curator 阶段后的 `PlanState`
- **Output**:
  - 含串词脚本的 state
- **Files involved**:
  - `src/podcast_ai/modules/theme/script_writer.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 05 - Critic Agent（结构化评估与修复动作）
- **Task name**: v3.0 - 实现 Critic Agent
- **目标**: 基于全量 state 产出结构化评估：`pass/scores/issues/actions`，并按契约写 `control.next_agent`。
- **类型**: backend
- **依赖关系**: Task 01, Task 04
- **Description**:
  - 新增 `modules/theme/critic.py`。
  - 输出字段：`critic.pass`、`critic.scores`、`critic.issues`、`critic.actions`。
  - 规则：当 `pass=false` 时 `actions` 至少 1 条，且包含目标 agent + 指令；Critic 不得改业务内容字段。
- **Input**:
  - Script Writer 阶段后的 `PlanState`
- **Output**:
  - 可执行的评估结果与修复指令
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 06 - Orchestrator（有限迭代、重试、回退）
- **Task name**: v3.0 - 实现多 Agent 编排器
- **目标**: 实现 `PlanOrchestrator.run()`：按 Planner → Curator → Writer → Critic 执行，支持 `max_iterations` 次有限回修与提前收敛。
- **类型**: backend
- **依赖关系**: Task 02, Task 03, Task 04, Task 05
- **Description**:
  - 新增 `modules/theme/orchestrator.py`。
  - 编排逻辑：
    - 正向流水执行 4 agents
    - Critic `pass=true` 提前结束
    - `pass=false` 根据 `critic.actions` 与 `control.next_agent` 定向回修
    - 达到 `max_iterations` 输出最终状态与未解决问题
  - 加入异常路径处理：JSON 非法、缺字段、越权写入时重试/回退。
- **Input**:
  - `EpisodeRequest`
  - 初始 `PlanState`
- **Output**:
  - 最终 `PlanState`（含 critic 评估结果）
- **Files involved**:
  - `src/podcast_ai/modules/theme/orchestrator.py`
  - `src/podcast_ai/modules/theme/state.py`
  - `src/podcast_ai/modules/theme/*.py`（agent 调用）
- **Estimated complexity**: L（3 小时）

---

### Task 07 - ThemePlanner / Pipeline 接入 Orchestrator
- **Task name**: v3.0 - 阶段一入口切换到多 Agent
- **目标**: 将阶段一入口从“单次 generate_plan”切换为 orchestrator 输出，并把 `PlanState` 结果映射回 `EpisodePlan`（兼容后续阶段二）。
- **类型**: backend
- **依赖关系**: Task 06
- **Description**:
  - 在 `ThemePlanner.generate_plan` 中调用 `PlanOrchestrator.run()`，将最终 state 转换为 `EpisodePlan`。
  - 回填 `EpisodePlan.critic_summary` 与 `EpisodePlan.generation_trace`（可选字段）。
  - `core/pipeline.py::plan_episode` 保持外部接口不变，仅替换内部阶段一实现。
- **Input**:
  - Orchestrator 最终 state
- **Output**:
  - 与现有 `plan_episode`/持久化流程兼容的 `EpisodePlan`
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/core/models.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 08 - 多 Agent 契约与异常路径测试
- **Task name**: v3.0 - 单测覆盖 Agent 契约与迭代控制
- **目标**: 覆盖 v3.0 验收重点：契约化读写、Critic 结构化输出、有限迭代、异常重试/回退、可追踪输出。
- **类型**: backend
- **依赖关系**: Task 07
- **Description**:
  - 新增 `tests/test_theme_orchestrator.py`（建议）：
    - 正常路径：1 次或 2 次迭代收敛
    - `pass=false` 路径：检查 `critic.actions` 生效并触发定向回修
    - 超过 `max_iterations`：输出最终状态与未解决问题
    - 非法 JSON/缺字段：触发重试或回退
    - 越权写字段：被拦截
  - 补充 `tests/test_pipeline.py`，验证 `plan_episode` 仍能落盘并被阶段二消费。
- **Input**:
  - mock LLM 返回（各 agent）
  - state schema 合法/非法样例
- **Output**:
  - 自动化测试通过且覆盖 v3.0 关键验收项
- **Files involved**:
  - `tests/test_theme_orchestrator.py`（新增）
  - `tests/test_pipeline.py`
  - `tests/conftest.py`（如需 fixture）
- **Estimated complexity**: M（2-3 小时）

