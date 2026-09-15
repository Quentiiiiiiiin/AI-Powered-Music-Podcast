## 版本 v6.5（迭代三十四：staged Planner Critic Prompt 对齐 Guide）

基于 PRD **v6.5**：为 **`staged` 下 Planner 阶段 Critic** 提供专用 prompt，基于仓库 **`PROMPT_Guide_Planner_Critic.txt`**，评估方法尽量**原样纳入**；并产品化区分 **generation / revision**。同步评分刻度为维度满分 **100**、系统 threshold 默认 **80**（与 v6.4 `pass`：每维得分须 **> threshold** 合取）。

**generation vs revision（写死）**：
- **generation**：从头评估——打分、列 issues、列 actions。
- **revision**：打分；检查既有 issues 是否解决并舍弃已解决项；再列 actions；**禁止生成新的 issues**；若 issues **确实减少**，则各维分数**不得低于上一轮**（仅可持平或更高）。

**硬约束**：
- 本轮**只做 Planner Critic（staged）**；Curator / Writer Critic、`legacy` **不强制同步**。
- 不改 snapshot 对外契约；不破坏每阶段最多 2 次修复的闸门预算与审计。
- 复用 v6.3 的 Guide 加载方式（`modules/theme/guides/` + 薄编排外壳）；不做新 Prompt 框架。

---

### Task 01 - 纳入 Guide：staged Planner Critic 专用 prompt
- **Task name**: v6.5 - Planner Critic staged ← PROMPT_Guide_Planner_Critic
- **目标**: 当 `stage=planner` 时，`build_critic_staged_messages`（或专用 builder）拼接 **Guide 全文** + 极薄 staged 外壳（禁止模型输出 `pass`/`threshold`/`control`、只评 Planner 交付物、输出 Schema_Critic_v4 字段）；Curator/Writer 仍走现有通用 staged Critic（本轮可不动正文）。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 将 `PROMPT_Guide_Planner_Critic.txt` 拷入 `modules/theme/guides/`（与 Planner/Curator Guide 同目录），经既有 `_load_guide` 读取；缺失则明确报错。
  - 禁止把 Guide 压成摘要；外壳仅补 MODE / 输出契约 / FORBIDDEN。
  - 分数说明与 Guide 一致：**每维 0–100**（替换当前 prompt 中错误的 0–10 表述）。
- **Input**: 根目录 `PROMPT_Guide_Planner_Critic.txt`、现有 `prompts_staged.py`
- **Output**: Planner 阶段 Critic messages 可对照 Guide 做 diff 验收
- **Files involved**:
  - `src/podcast_ai/modules/theme/guides/PROMPT_Guide_Planner_Critic.txt`（新增）
  - `src/podcast_ai/modules/theme/prompts_staged.py`
- **Estimated complexity**: M（2 小时，主要为接入与自检）

---

### Task 02 - generation / revision 外壳（Planner Critic）
- **Task name**: v6.5 - Planner Critic mode envelopes
- **目标**: 在 Planner Critic 专用路径上明确两类 MODE 行为（见上文规则）；revision 时 user/system 注入「上一轮 `critic.issues` / `scores` 为基准、禁止新增 issues、issues 减少则分数不降」等指令。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - generation：允许完整新建 issues/actions。
  - revision：强调只收敛既有 issues → 更新 actions；不得开新 issue 条目（可用 location/problem 指纹对照说明）。
  - 输入 state 已含上一轮 critic 时，在 user 消息中可再点名「Previous critic snapshot」以免模型忽略（保持简洁，勿重复整份 Guide）。
- **Input**: Task 01 builder、`mode` 参数（orchestrator 已传 generation/revision）
- **Output**: 两种 mode 文案可区分且可测
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts_staged.py`
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 03 - 评分刻度 0–100 + 系统 threshold 默认 80
- **Task name**: v6.5 - critic score scale & threshold=80
- **目标**: 将 Critic 各维分数契约从 0–10 改为 **0–100**；`DEFAULT_CRITIC_THRESHOLDS` 每维默认 **80**（`pass` 仍要求 **严格大于** threshold）；同步 structured output schema、sanitize 范围校验、空 state、相关 prompt/注释与单测期望。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01 并行；联调依赖一致刻度）
- **Description**:
  - 改动点：`agent_response_schemas.py`（maximum）、`critic_rules.py`、`critic_agent.py` sanitize、`state.py` 注释、staged/legacy Critic prompt 中若仍写 0–10 则一并改（legacy **仅改刻度/阈值**即可，不强制换 Guide）。
  - `overall_score` 语义与 Guide/schema 保持一致（模型可输出；系统不因本轮改 pass 公式）。
  - 更新 `tests/test_critic_rules.py` 等 fixture 分数。
- **Input**: PRD v6.5、现有 v6.4 规则实现
- **Output**: `score > 80` 才过分数门槛；与 Guide 0–100 一致
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_rules.py`
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/prompts.py` / `prompts_staged.py`（刻度文案）
  - `tests/test_critic_*.py`
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 04 -（可选最小）revision 约束的系统侧校验
- **Task name**: v6.5 - optional revision soft-check
- **目标**: 对 **staged + stage=planner + mode=revision** 做轻量后置校验（或纠偏），防止模型明显违反「禁止新 issues / issues 减少时分数不降」；失败时明确报错或按规则夹紧——**选一种简单策略并写清**，避免复杂 issue 匹配引擎。
- **类型**: backend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - 建议最小实现：以 `location+problem`（或整条 issue 规范化字符串）集合比较；若出现上一轮没有的新 key → `AIServiceError`；若 `len(issues)` 下降且任维 `score < prev` → 报错或抬升到 prev（优先报错更清晰）。
  - **不做**语义级「是否真的解决了问题」判定（那是模型职责）。
  - 若时间紧，可将本 Task 降为「仅 prompt 约束 + 测试断言 prompt 含关键字」，但 PRD AC2 更稳妥的是有一层系统护栏——实现时二选一写进代码注释。
- **Input**: CriticAgent.merge 前后的 prev/next critic
- **Output**: revision 明显违规可被发现
- **Files involved**:
  - `src/podcast_ai/modules/theme/critic_rules.py` 或 `critic_agent.py`
- **Estimated complexity**: S（1 小时；可选）

---

### Task 05 - 轻量测试与范围注释
- **Task name**: v6.5 - prompt/threshold smoke + scope note
- **目标**: ① Planner Critic staged messages 含 Guide 特征句 + GENERATION/REVISION 差异；② threshold 默认 80、`81` pass / `80` fail（严格大于）；③ 模块注释标明仅 Planner Critic Guide、Curator/Writer/legacy 未换 Guide。
- **类型**: backend
- **依赖关系**: Task 01, Task 02, Task 03
- **Description**:
  - 不强制真实 LLM E2E；不改 snapshot / orchestrator 预算逻辑。
- **Input**: Task 01–03（及若做了的 Task 04）
- **Output**: `pytest` 通过；开发者可定位 Guide 文件
- **Files involved**:
  - `tests/test_prompts_staged_planner_critic.py`（新建，建议）
  - `tests/test_critic_rules.py`
  - `src/podcast_ai/modules/theme/prompts_staged.py`（模块头注释）
- **Estimated complexity**: S（1 小时）
