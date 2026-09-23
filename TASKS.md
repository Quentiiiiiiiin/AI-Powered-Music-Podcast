## 版本 v6.8（迭代三十七：revision 护栏放宽 + 失败路径补写 snapshot）

基于 PRD **v6.8** 两处修复/优化：

1. **关闭** staged Critic revision 的系统硬护栏：曾用「前后 `issue.problem`（及 location+problem 指纹）比对」判定疑似新 issue 并 **报错中止**；现改为**不以规则硬判、更不得因此停止运行**（prompt 侧引导可保留）。
2. 写出 **`state_partial`** 时，在**同一目录**再写一份由 partial 内 state **派生**、可被 Console / 阶段二消费的 **snapshot**（字段契约与成功路径一致；命名实现约定并文档化）。

**硬约束**：不破坏正常成功路径的 `pass` / 闸门预算 / 审计；不回退 v6.6 终态 `state` 精简与 v6.7 Critic schema；snapshot **对外格式不变**。

---

### Task 01 - 关闭 revision「禁止新 issue」硬护栏
- **Task name**: v6.8 - disable revision new-issue hard assert
- **目标**: staged Critic `mode=revision` 路径**不再**因「疑似新 issue / `problem` 文本不一致」触发 `AIServiceError` 或中止流程。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 现状：`critic_agent.py` 在 revision 调用 `assert_staged_revision_constraints`（指纹 = `location::problem`，novel → 抛错）。
  - 处理：移除该调用，或将「禁止新 issue」分支改为 no-op / 仅 debug 日志；**推荐直接去掉硬校验调用**，少留死代码。
  - 「issues 减少则分数不得低于上一轮」若同函数内硬抛错：本轮可一并关闭（避免另一类误杀），或仅保留非阻断日志——以实现简洁为准并在注释写明。
  - **保留** `prompts_staged` 中 revision 的软性引导（不要发明新 issues 等），但不得作为硬失败条件。
  - 更新/删除依赖该护栏的单测。
- **Input**: `critic_rules.assert_staged_revision_constraints`、`critic_agent.run`
- **Output**: revision 措辞变化不再中断编排
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/critic_rules.py`
  - 相关 `tests/test_critic_*.py`
- **Estimated complexity**: S（≤1 小时）

---

### Task 02 - `state_partial` 同目录补写可消费 snapshot
- **Task name**: v6.8 - snapshot beside state_partial
- **目标**: 每次成功写出 `state_partial` 后，基于 partial 中的 PlanState（如 `state_before_round`）调用既有 `build_episode_snapshot_from_state`（必要时先补齐空 playlist/script），在**同目录**写入 Console/阶段二可加载的 snapshot；写盘失败只打日志、**不二次抛错淹没原错误**。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01 并行）
- **Description**:
  - 接入点优先：`FilePlanAuditSink.write_state_partial`（staged + legacy 文件名分支都覆盖），避免在 orchestrator 多处复制。
  - 命名建议（择一并注释/README 一行）：`stage_{stage}_rev{r}_snapshot.json` 或与 partial 成对的 `…_state_partial.snapshot.json`；legacy：`iteration{i}_snapshot.json`。
  - 派生失败（缺 meta/segments 等）→ 记录原因，不强行写坏文件。
  - 内容完整度以 partial 内 state 为准；契约须通过既有 snapshot 子集校验语义（与成功路径同一 builder）。
  - **不改**成功路径主目录 `{episode_id}.json` 的既有写出逻辑（pipeline 成功态仍照旧）。
- **Input**: `plan_audit.write_state_partial`、`build_episode_snapshot_from_state`
- **Output**: 失败审计目录内 partial 旁可见可打开 snapshot
- **Files involved**:
  - `src/podcast_ai/modules/theme/plan_audit.py`
  - `src/podcast_ai/infra/storage/paths.py`（文件名 helper，可选）
  - （必要时）复用 `orchestrator_staged._ensure_segment_snapshot_defaults` 或抽到 state/paths 小函数
- **Estimated complexity**: M（1.5–2 小时）

---

### Task 03 - 回归测试
- **Task name**: v6.8 - revision + partial-snapshot tests
- **目标**: ① revision 换 `problem` 措辞 / 新增 issue 指纹 **不再**抛错；② `write_state_partial` 后同目录存在合法 snapshot（可用 `validate_episode_snapshot_subset` 或字段冒烟）；③ pass 规则与终态无 critic/control 相关既有测不红。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 用临时目录 + 假 PlanState，不调 LLM。
- **Input**: Task 01–02
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_revision_guard_v68.py` / `tests/test_audit_partial_snapshot_v68.py`（新建或并入既有）
- **Estimated complexity**: S（1 小时）
