## 版本 v6.4（迭代三十三：Critic 输出 Schema 升级 + 系统规则判定 pass/next_agent）

基于 PRD **v6.4**：按 `Schema_Critic_v4.txt` 升级 Critic **模型输出契约**——只负责评分与 issues/actions；**从 LLM 输出中移除** `pass`、`threshold`、`control`。**`critic.pass`** 与 **legacy 的 `control.next_agent`** 改由**系统规则**派生，并写入 state（供 Orchestrator / 审计使用）。

**系统判定规则（写死在任务中）**：
1. **`pass=true` 当且仅当**：
   - 每个评分维度得分 **严格大于** 系统配置的 `threshold`（threshold **不**由 Critic 输出）；**且**
   - **仅有 `minor` severity 的 issues，或 `actions` 为空**。
2. **`staged`**：不依赖 Critic 的 `control`；阶段推进仍由 Orchestrator FSM，读派生后的 `critic.pass`。
3. **`legacy`**：`control.next_agent` = 在 `actions[*].target_agent` 出现过的目标中，按固定顺序 **Planner → Music Curator → Script Writer** 取**最先顺位**；无 actions 且已 pass 时不错误路由。

**硬约束**：不改 snapshot 对外契约；审计可复查「Critic 原始评分/issues/actions」与「系统派生的 pass/next_agent」；避免再造一套编排框架。

---

### Task 01 - Critic v4 response schema + state 模板对齐
- **Task name**: v6.4 - Schema_Critic_v4 结构化契约
- **目标**: 将 Critic structured output（legacy 与 staged 共用模型字段）对齐 `Schema_Critic_v4`：`overall_score`、新多维 `scores`、`issues`（含 `severity` / `listener_impact`）、`actions`（含 `location`）；**required 中不再包含** `pass` / `threshold` / `control`。同步 `state_schema.json` / 空 state 中 critic 默认结构（`pass`/`threshold` 可由系统占位，不要求模型填写）。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 分数维（以实现为准，与 Schema 一致）：`theme_definition`、`theme_relationship`、`musical_concept`、`segment_differentiation`、`sequence_narrative`、`curator_actionability`、`creative_freedom`。
  - `severity` 枚举至少含 `minor` / 非 minor（如 `critical`；若 Schema 未列全，实现选定最小集合并校验）。
  - 可合并 `CRITIC_RESPONSE_SCHEMA` 与 `CRITIC_STAGED_RESPONSE_SCHEMA` 的 critic 体为同一套（staged 仍禁止顶层 `control`）。
  - 系统 threshold：常量或 `Settings` 小配置（每维一个 int）；默认值需合理且可测。
- **Input**: `Schema_Critic_v4.txt`、现有 `agent_response_schemas.py` / `state.py`
- **Output**: LLM schema 与 state 可容纳 v4 字段；旧三维 scores 退出模型契约
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/state.py`
  - `state_schema.json`（若仍作参考）
  - （可选）`src/podcast_ai/infra/config.py` 若暴露 threshold
- **Estimated complexity**: M（2 小时）

---

### Task 02 - sanitize：只收模型字段；拒绝 pass/threshold/control
- **Task name**: v6.4 - Critic sanitize 收敛
- **目标**: 更新 `_sanitize_critic_*`：校验并保留 v4 模型字段；若模型仍输出 `pass` / `threshold` / `control` → **明确失败或剥离并拒绝**（推荐直接报错，防静默漂移）；**不在 sanitize 内编造 pass**（留给 Task 03）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - legacy / staged 顶层差异保留：legacy 也不再要求模型带 `control`（改由系统写）；staged 继续禁 `control`。
  - 删除「`pass=false` 则 actions 至少 1 条」对**模型 pass** 的依赖；改由规则层处理（例如 fail 且无 actions 时的策略：仍可 `pass=false`，legacy 需有安全的 next_agent 回退——在 Task 04 定义）。
- **Input**: Task 01 schema
- **Output**: 解析后的 critic patch 仅含评分/issues/actions（+ overall_score）
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_agent.py`
- **Estimated complexity**: M（1.5–2 小时）

---

### Task 03 - 系统规则：派生 `critic.pass`（+ 写入 threshold）
- **Task name**: v6.4 - derive_critic_pass
- **目标**: 实现纯函数（建议独立小模块或 `critic_agent` 旁）按 PRD 规则计算 `pass`，并写入 state 的 `critic.pass`；同时写入系统 `critic.threshold`（便于审计对照）。Orchestrator 只读派生后的 `pass`。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - `pass = (∀ dim: score[dim] > threshold[dim]) AND (issues 全为 minor OR actions 为空)`。
  - 调用点：CriticAgent.run 在 sanitize 之后、`merge_plan_state` 之时/之后统一派生（staged 与 legacy 共用同一规则）。
  - 缺维、非 int、未知 severity → 明确报错，不默认 pass。
- **Input**: 已 sanitize 的 critic 体 + 系统 threshold
- **Output**: state 中可信的 `critic.pass` / `threshold`
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_rules.py`（新建，建议）或 `critic_agent.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 04 - legacy：系统按 actions 计算 `next_agent`
- **Task name**: v6.4 - derive_next_agent (legacy)
- **目标**: legacy 路径不再信任模型 `control.next_agent`；根据 `actions[*].target_agent`，按 **Planner → Music Curator → Script Writer** 取最先出现顺位写入 `control.next_agent`。`pass=true` 且无待修 actions 时不错误指向创作 Agent（保持完成语义，与现有 orchestrator 读 `critic.pass` 一致）。
- **类型**: backend
- **依赖关系**: Task 03
- **Description**:
  - 目标名归一（大小写/别名若已有则复用）；非法 target_agent 明确报错或忽略并记录——选一种简单策略并测住。
  - `pass=false` 且 actions 为空：定义最小回退（例如保持上一 `next_agent` 或固定回当前轮起点 Agent），避免编排空转；写清注释。
  - 改动 `orchestrator.py` 仅当需适配新语义；优先在 CriticAgent 合并时写好 `control`。
- **Input**: 派生后的 critic + 现有 PlanOrchestrator
- **Output**: legacy 回修路由可预期
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_rules.py`（或等价）
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/orchestrator.py`（仅必要时）
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 05 - Critic prompts（staged + legacy）对齐 v4、去掉 pass/路由教唆
- **Task name**: v6.4 - Critic prompts 收敛
- **目标**: 更新 `build_critic_agent_messages` 与 `build_critic_staged_messages`：要求输出 Schema_Critic_v4 字段；**禁止**让模型决定 `pass` / `threshold` / `next_agent`；说明系统将据此判定。staged 仍强调阶段焦点；legacy 说明 actions 的 `target_agent`/`location` 用于系统选下一修谁。
- **类型**: backend
- **依赖关系**: Task 01（可与 02–04 并行改文案，联调依赖 schema）
- **Description**:
  - 分数维与 issue/action 字段示例与 schema 一致；删除旧 coherence/emotion_flow/immersion 与「scores>=threshold 则 pass」教唆。
  - 不借本轮重写创作 Agent prompt。
- **Input**: Schema_Critic_v4、现有 critic prompts
- **Output**: 模型侧不再被要求输出调度字段
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/prompts_staged.py`
- **Estimated complexity**: M（2 小时）

---

### Task 06 - 单测：pass 规则、next_agent 优先序、schema 拒绝项
- **Task name**: v6.4 - critic rules / sanitize 回归
- **目标**: 不调 LLM；锁定 ① 维分卡阈值 → fail；② 非 minor issue 且有 actions → fail；③ 全维过线 + 仅 minor 或空 actions → pass；④ legacy next_agent 优先序；⑤ 模型带 `pass`/`control` 被拒；⑥ staged 仍只靠 `critic.pass`、不写 next_agent。
- **类型**: backend
- **依赖关系**: Task 02, Task 03, Task 04
- **Description**:
  - 更新既有 `tests/test_critic_agent.py` / orchestrator 测试中依赖旧 scores/`pass` 模型输出的 fixture。
- **Input**: Task 02–04
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_critic_rules.py`（新建，建议）
  - `tests/test_critic_agent.py`
  - （必要时）`tests/test_orchestrator*.py`
- **Estimated complexity**: M（2 小时）
