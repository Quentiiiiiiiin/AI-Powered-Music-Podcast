## 版本 v6.0（迭代二十九：多 Agent 分阶段闸门编排 Stage-Gated）

基于 PRD v6.0 与 ARCHITECTURE **AD-v6.0**：多 Agent 阶段一新增默认编排 **`staged`（Stage-Gated）**，与现有 **`legacy`** 双轨并存（config 切换；legacy 冻结保留）。

**staged 要点**：
```text
Planner ⇄ Critic（阶段内闭环）
  → Music Curator ⇄ Critic
  → Script Writer ⇄ Critic
  → 输出终稿 snapshot
```
- 每阶段：首次生成 → Critic；不通过最多 **2 次修复**（合计最多 **3 次 Critic**）；通过进下一阶段；仍不通过 → **阶段失败、禁止回退**。
- 路由由 **Orchestrator FSM（代码）** 推进；Critic **不决定 `next_agent`**。
- 阶段失败仍写出**主 snapshot**（可被阶段二消费）+ 审计；`control.status`（或等价）标明失败阶段。
- **独立/重写 staged prompt**；legacy prompt **冻结不动**。
- **默认 `orchestration_mode: staged`**；与 `agent_mode=single_agent` 正交（仅 multi_agent 应用）。

**硬约束**：不改阶段二/三契约、`Stage2Snapshot` 对外 schema、创作 Agent 写权限主表（playlist/script 归属）；本轮不做可量化业务规则硬校验增强。

---

### Task 01 - 配置双轨：`orchestration_mode`
- **Task name**: v6.0 - Settings/config 暴露 staged|legacy
- **目标**: 在 `Settings`/`config.yaml` 增加 `orchestration_mode: staged | legacy`，**默认 `staged`**；非法值明确报错；环境变量可覆盖。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 字段挂载位置建议：`app.orchestration_mode` 或 `llm`/`theme` 同级清晰命名（实现选一处并文档化）。
  - `init-config` 模板与 README 同步列出默认值与语义。
  - CLI/Console 运行日志或状态中可打印当前 mode（最小可观测）。
- **Input**: PRD/AD-v6.0、现有 `infra/config.py`
- **Output**: 可切换、默认同 staged 的配置契约
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `src/podcast_ai/cli.py`（init-config 模板）
  - `README.md`
- **Estimated complexity**: S（1 小时）

---

### Task 02 - Staged FSM 编排器（闸门状态机）
- **Task name**: v6.0 - Stage-Gated Orchestrator FSM
- **目标**: 实现 staged 编排：`Planner⇄Critic → Curator⇄Critic → Writer⇄Critic`；每阶段最多 2 次修复；失败不回退；成功推进；Critic 不参与选下一创作 Agent。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 建议新建 `orchestrator_staged.py`（或同文件清晰分支），**保留**现有 `orchestrator.py` 作为 legacy 路径冻结。
  - 阶段内循环：create/revise → Critic → pass 则下一阶段，否则 revise（计数）；超预算 → 标记失败并停止。
  - 复用现有四 Agent 类与 `merge_plan_state` / sanitize；不在 UI/业务层复制 Agent 逻辑。
  - 支持可选 `on_progress`（对齐 v5.2）：上报 `stage` / `revision` / `agent` / `status`。
  - 失败路径：`control.status`（及失败阶段字段）可追溯；**仍返回可用 PlanState** 供上层写 snapshot。
- **Input**: 现有 Agents + PlanState
- **Output**: `run_staged(...)` 或等价入口，行为符合 PRD 闸门规则
- **Files involved**:
  - `src/podcast_ai/modules/theme/orchestrator_staged.py`（新建，建议）
  - `src/podcast_ai/modules/theme/orchestrator.py`（legacy 保持）
  - `src/podcast_ai/modules/theme/state.py`（若需补充 control 状态字段约定）
- **Estimated complexity**: L（3–5 小时）

---

### Task 03 - Staged 专用 Prompt / Critic 契约（与 legacy 分离）
- **Task name**: v6.0 - staged prompts + Critic 不写 next_agent
- **目标**: 为 staged 提供独立 prompt（Planner/Curator/Writer/Critic）；Critic 仅做阶段内 pass/评分/issues/actions；**禁止**要求或写入 `control.next_agent`。legacy prompt **冻结不动**。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01/02 并行，集成依赖 Task 02）
- **Description**:
  - 在 `prompts.py` 新增 `build_*_staged_*`（或独立 `prompts_staged.py`），避免改坏 legacy 函数。
  - 更新 staged 路径的 Critic response schema / sanitize：去掉对 `next_agent` 的写入；legacy Critic 路径不变。
  - Prompt 明确「当前阶段交付物」「修订轮次」「禁止改上游已锁定字段」等闸门语义（简洁即可）。
- **Input**: 现有 `prompts.py`、`critic_agent.py`、`agent_response_schemas.py`
- **Output**: staged/legacy 两套 prompt 可按 mode 加载
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts.py`（或 `prompts_staged.py`）
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
- **Estimated complexity**: M–L（3–4 小时）

---

### Task 04 - ThemePlanner / pipeline 双轨接线 + 失败仍落盘 snapshot
- **Task name**: v6.0 - multi_agent 按 mode 分发 + 失败产物
- **目标**: `agent_mode=multi_agent` 时按 `orchestration_mode` 调用 staged 或 legacy；两种路径最终都经既有校验写 `state.json` + `{episode_id}.json`；staged 阶段失败时**仍输出主 snapshot**，状态标明失败。
- **类型**: backend
- **依赖关系**: Task 01, Task 02, Task 03
- **Description**:
  - 改动点：`llm_planner.py`（及必要时 `pipeline.plan_episode`）；日志打印当前 mode。
  - 确保失败不抛到「无产物」：Orchestrator 返回带失败标记的 state → 上层仍 `save_state_json` / snapshot。
  - `single_agent` 路径不受影响。
- **Input**: Task 02/03 完成物
- **Output**: 默认同 staged 的端到端 plan 路径；legacy 可切回
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/core/pipeline.py`（仅必要时）
- **Estimated complexity**: M（2 小时）

---

### Task 05 - 审计落盘按阶段/修订轮次可追溯
- **Task name**: v6.0 - staged audit 命名约定
- **目标**: staged 下审计文件可按**阶段 + 修订轮次**追溯（命名实现定义并写进代码注释/README）；写盘失败不阻断主流程（延续 v3.6 语义）。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 复用 `FilePlanAuditSink` 或小幅扩展文件名 helper；legacy 审计命名保持兼容。
  - 建议示例：`stage_planner_rev0_critic.json` 等（实现选定一种并文档化即可）。
- **Input**: 现有 `plan_audit.py`
- **Output**: staged 审计可对齐阶段/轮次
- **Files involved**:
  - `src/podcast_ai/modules/theme/plan_audit.py`
  - `src/podcast_ai/modules/theme/orchestrator_staged.py`
- **Estimated complexity**: S（1–2 小时）

---

### Task 06 - CLI/Console 可观测当前 mode（最小）
- **Task name**: v6.0 - mode 可观测
- **目标**: CLI plan 摘要与 Console 阶段一面板能看到当前 `orchestration_mode`（只读展示即可）；可选允许 Console 覆盖 mode（非必须，避免过度设计）。
- **类型**: frontend
- **依赖关系**: Task 01, Task 04
- **Description**:
  - Console：参数区或 Run 结果区显示 mode；进度洞察可展示 `stage`/`revision`（若 Task 02 已 emit）。
  - 不强制大改 UI 信息架构。
- **Input**: Settings + progress 事件
- **Output**: 开发者可见当前编排模式
- **Files involved**:
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/console/app.py` / `runner.py`
- **Estimated complexity**: S（1 小时）

---

### Task 07 - 单测：闸门预算、禁止回退、legacy 不回归
- **Task name**: v6.0 - staged FSM + dual-mode 回归测试
- **目标**: 用 mock Agent 锁定：① 阶段顺序；② 每阶段最多 2 次修复；③ 失败不调用上游 Agent；④ Critic 不写 next_agent；⑤ `legacy` 路径仍可跑通既有语义；⑥ 失败仍得到可构建 snapshot 的 state。
- **类型**: backend
- **依赖关系**: Task 02, Task 03, Task 04
- **Description**:
  - 优先单元测 FSM（注入 fake agents），少做真实 LLM。
  - 可选：config 默认值为 `staged` 的断言。
- **Input**: staged orchestrator + mocks
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_orchestrator_staged.py`（新建）
  - `tests/test_orchestrator.py`（legacy 冒烟/既有用例不破坏）
- **Estimated complexity**: M（2–3 小时）
