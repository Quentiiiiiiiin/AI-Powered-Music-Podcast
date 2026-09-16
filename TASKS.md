## 版本 v6.7（迭代三十六：Critic Schema 一致性修复）

基于 PRD **v6.7**：修复 v6 系列落地后的两处 Critic 契约不一致——

1. **移除** Planner Critic 的 `overall_score`（schema / structured output / 校验 / merge / prompt / 过程 state 残留一并清掉）。
2. **为** Music Curator Critic 的 `actions[*]` **增加 `location`**，与 Planner Critic actions（`target_agent` + `location` + `instruction`）对齐。

**硬约束**：
- 系统 `pass` 规则（各维 **> 80**，且仅 minor 或空 actions）不回退。
- 终态主 `state` 仍不含 `critic`/`control`（v6.6）。
- 不做新功能、不扩 Writer Critic；避免顺手重构 Critic 框架。

参考文件：`Schema_Critic_v4.txt`（已无 overall_score）、`Schema_Curator_Critic_v4.txt`（actions 已含 location）——以仓库 Schema 与 PRD 为准，**代码侧对齐**。

---

### Task 01 - 移除 Planner Critic `overall_score`
- **Task name**: v6.7 - drop overall_score from Planner Critic
- **目标**: Planner Critic（含 legacy 共用 Planner schema 路径）模型契约与 sanitize **不再要求/保留** `overall_score`；过程 state 模板与校验不再把该字段当必填。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - `agent_response_schemas.py`：`_CRITIC_BODY_SCHEMA` 去掉 `overall_score` 属性与 required。
  - `critic_agent.py`：`require_overall_score` 对 Planner/legacy 改为 `False`（或删除该开关分支若两端都不再需要）。
  - `state.py`：空 critic 模板与 `_CRITIC_REQUIRED_KEYS` 去掉 `overall_score`；类型校验改为「若存在则须 int」或直接禁止残留（选简单策略：不写入即可）。
  - `prompts.py` / `prompts_staged.py`：删除 overall_score 教唆与示例；输出说明改为仅 `scores/issues/actions`。
  - 全库检索 `overall_score`，清掉 Critic 相关残留（测试 fixture 一并改）。
- **Input**: PRD v6.7、`Schema_Critic_v4.txt`
- **Output**: Planner Critic 路径无 overall_score
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/state.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/prompts_staged.py`
  - 相关 `tests/*critic*`
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 02 - Curator Critic `actions[*].location` 对齐
- **Task name**: v6.7 - Curator action.location required
- **目标**: Curator Critic structured output 与 sanitize 要求每条 action 含 **`location`**（string），字段集与 Planner actions 一致：`target_agent`、`location`、`instruction`。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01 并行）
- **Description**:
  - 更新 `_CURATOR_CRITIC_ACTION_ITEM` / `_CURATOR_ACTION_KEYS`。
  - `prompts_staged` Curator Critic 外壳：去掉「no action.location」表述，改为必填 location（可与 Guide 中 location 语义一致）。
  - 若根目录 / guides 旁路 Schema 文案已含 location，代码注释与 Schema 文件保持同步即可。
- **Input**: `Schema_Curator_Critic_v4.txt`、现有 Curator Critic sanitize
- **Output**: 缺 `location` 的 Curator action 明确失败
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/prompts_staged.py`
  - （可选）`Schema_Curator_Critic_v4.txt` 若与代码仍有出入则对齐
- **Estimated complexity**: S（≤1 小时）

---

### Task 03 - 回归测试：契约断言 + pass 规则不回退
- **Task name**: v6.7 - critic schema consistency tests
- **目标**: 单测锁定 ① Planner sanitize **拒绝或剥离** overall_score（推荐：模型若仍输出则报错/禁止进入 required）；② Curator action 缺 location 失败、齐备则通过；③ `derive_critic_pass` 行为与阈值 80 不变；④ 终态 `save_state_json` 仍无 critic/control。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 更新既有 `test_critic_*` / `test_curator_critic_*` fixture；不强制真实 LLM。
- **Input**: Task 01–02
- **Output**: `pytest` 相关用例通过
- **Files involved**:
  - `tests/test_critic_agent.py` / `tests/test_curator_critic_v66.py`（或新建 `test_critic_schema_v67.py`）
- **Estimated complexity**: S（1 小时）
