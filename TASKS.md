## 版本 v6.6（迭代三十五：staged Music Curator Critic 专用 Schema/Prompt + 终态 state 精简）

基于 PRD **v6.6**：
1. 为 **`staged` × Music Curator 阶段 Critic** 接入专用契约与指南：`Schema_Curator_Critic_v4.txt`、`PROMPT_Guide_Curator_Critic.txt`（Guide 尽量原样纳入；评分维度与 **Planner Critic 分离、不可混用**）。
2. staged 编排按阶段切换 Critic 的 schema / prompt / structured output（为未来 Writer Critic 留同一扩展点，**本轮不实现 Writer 专用 Guide**）。
3. Critic 仍不输出 `pass`；系统规则不变：各维得分 **> 80**，且（仅 minor issues **或** actions 为空）→ `pass`。
4. **最终主产物 `state.json`（staged 与 legacy）落盘时去掉顶层 `critic`、`control`**；过程态 / 审计仍可保留完整 critic。

**硬约束**：不改 snapshot 对外契约；不回退 v6.5 Planner Critic；避免造通用「Critic 插件框架」——用阶段分支/映射表即可。

---

### Task 01 - Curator Critic：独立 schema / 分数维 / sanitize
- **Task name**: v6.6 - Schema_Curator_Critic_v4 契约
- **目标**: 新增 Curator Critic 的 structured output 与 sanitize，评分维与 Planner Critic **完全分离**；模型输出不含 `pass` / `threshold` / `control`。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 分数维（对齐 Schema）：`planner_alignment`、`thematic_relevance`、`sequence_coherence`、`audience_listening_quality`、`track_fitness`（0–100）。
  - issues：含 `severity`（Guide/Schema 为 **minor|major** 等——以实现与 Guide 一致为准；**仅 `minor` 算「仅有 minor」**）、`reason`（注意：不是 Planner 的 `listener_impact`）、以及 type/location/problem/suggestion。
  - actions：至少 `target_agent` + `instruction`；Schema 样例无 `location` 则**不要**强行要求 Planner 那套 action.location。
  - `derive_critic_pass` 改为按**当前阶段分数维**计算（或接受 `score_dims`/`thresholds` 参数），默认阈值仍每维 **80**。
  - 内存过程态 `state.critic` 可继续承载当轮结果；空模板按阶段初始化 scores 键，避免维混用。
- **Input**: `Schema_Curator_Critic_v4.txt`、现有 `CRITIC_SCORE_DIMS` / sanitize
- **Output**: Curator Critic 与 Planner Critic 两套不可互换的模型契约
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/critic_rules.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - （必要时）`src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M–L（3 小时）

---

### Task 02 - staged Curator Critic：Guide 全文 + MODE 外壳
- **Task name**: v6.6 - Curator Critic prompt ← PROMPT_Guide_Curator_Critic
- **目标**: `stage=music_curator` 时使用专用 builder：编排薄外壳 + `PROMPT_Guide_Curator_Critic.txt` 全文；generation/revision 区分（revision：收敛既有 issues、禁随意开新题——对齐 v6.5 精神，文案简洁即可）；仍禁止模型输出 pass/threshold/control。
- **类型**: backend
- **依赖关系**: Task 01（字段名需与 schema 一致；可并行起草 Guide 拷贝）
- **Description**:
  - Guide 放入 `modules/theme/guides/`，复用 `_load_guide`。
  - `build_critic_staged_messages`：`planner` → 既有 Planner Critic Guide；`music_curator` → 本 Guide；`script_writer` → 暂留通用短提示（本轮不换专用 Guide）。
  - 明确只评 playlist / 选曲序列，不重开 Planner 结构问题。
- **Input**: 根目录 `PROMPT_Guide_Curator_Critic.txt`
- **Output**: Curator 阶段 Critic messages 可对照 Guide 验收
- **Files involved**:
  - `src/podcast_ai/modules/theme/guides/PROMPT_Guide_Curator_Critic.txt`（新增）
  - `src/podcast_ai/modules/theme/prompts_staged.py`
- **Estimated complexity**: M（2 小时）

---

### Task 03 - CriticAgent / staged 编排：按阶段切换 schema+prompt+规则
- **Task name**: v6.6 - stage-routed Critic invocation
- **目标**: `CriticAgent.run(..., stage=...)` 在 staged 下按阶段选择 response schema、sanitize、pass 维度与 messages；Orchestrator 调用点无需复制业务逻辑。Planner 路径行为保持 v6.5。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 审计仍落盘当轮完整 critic（含派生 pass）；不在此 Task 精简审计。
  - legacy 路径：本轮可继续用 Planner Critic schema（或文档标明未切换）；**不强制**为 legacy 引入 Curator 专用 Critic。
  - 若 revision soft-check（v6.5）绑定 Planner 维，需按阶段跳过或改用对应维，避免 Curator 误伤。
- **Input**: Task 01–02、`orchestrator_staged.py`
- **Output**: Curator 闸门实际走专用 Critic 链路
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/orchestrator_staged.py`（仅必要时）
  - `src/podcast_ai/modules/theme/critic_rules.py`
- **Estimated complexity**: M（2 小时）

---

### Task 04 - 终态主 `state.json` 去掉 `critic` / `control`
- **Task name**: v6.6 - strip critic/control on final state save
- **目标**: `save_state_json`（或 pipeline 写盘前）写出的**主** `state` **不含**顶层 `critic`、`control`；staged 与 legacy 均适用。审计 / 过程 `stage_*_state.json` / `iteration*_state.json` **仍可含**完整字段。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01–03 并行）
- **Description**:
  - 实现：写盘前 `dict` 浅拷贝并 `pop("critic")` / `pop("control")`；内存中的编排 state 可继续保留二者直至写盘。
  - 调整「主 state 落盘校验」：终态允许/要求无 critic/control（与 single_agent 子集精神接近，但保留完整业务字段）；**过程校验**不要误伤内存态。
  - snapshot 仍由既有剪枝函数生成，对外格式不变。
- **Input**: `paths.save_state_json`、`pipeline.plan_episode`
- **Output**: 磁盘主 `state.json` 干净；审计仍可查 critic
- **Files involved**:
  - `src/podcast_ai/infra/storage/paths.py`
  - （必要时）`src/podcast_ai/core/pipeline.py`、`src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: S（1 小时）

---

### Task 05 - 单测：维度隔离、pass 规则、终态精简、Planner 不回退
- **Task name**: v6.6 - curator critic + final state tests
- **目标**: ① Curator sanitize 拒收 Planner 分数维；② Curator 维 >80 + 仅 minor/空 actions → pass；③ `major` issue 且有 actions → fail；④ staged messages 在 music_curator 含 Guide 特征、planner 仍含 Planner Guide；⑤ `save_state_json` 产物无 critic/control；⑥ 既有 Planner Critic / threshold 测试不红。
- **类型**: backend
- **依赖关系**: Task 01, Task 02, Task 03, Task 04
- **Description**:
  - 不强制真实 LLM E2E。
- **Input**: Task 01–04
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_curator_critic_v66.py`（新建，建议）
  - `tests/test_critic_rules.py` / `test_prompts_staged_planner_critic.py`（回归）
- **Estimated complexity**: M（2 小时）
