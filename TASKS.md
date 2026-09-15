## 版本 v6.3（迭代三十二：staged 模式 Planner / Music Curator Prompt 对齐 Guide）

基于 PRD **v6.3**：`staged` 下 Planner / Music Curator 的 schema（v6.1）已齐，但 prompt 未充分教会「按字段思考与填写」。本轮基于仓库根目录 **`PROMPT_Guide_Planner.txt`**、**`PROMPT_Guide_Music-Curator.txt`**，重写/升级 **`prompts_staged` 中二者的 messages**。

**目标形态**：
- Guide 的**字段释义与思考方法尽量原封不动**进入对应 Agent（勿摘要阉割关键规则）。
- **保留**现有 staged 编排外壳：GENERATION / **REVISION**、当前闸门、输入 PlanState、输出 schema 契约、禁止越权写他 Agent 字段。

**硬约束**：
- **仅** `orchestration_mode=staged` 的 Planner、Music Curator。
- **不改** snapshot 对外格式；**不强制**改 Script Writer / Critic（除非引用新语义所必需的最小改动）。
- **`legacy`（`prompts.py`）本轮不同步**。
- 避免过度设计：不做通用 Prompt 引擎/模板语言；拼接「编排外壳 + Guide 正文 + state」即可。

---

### Task 01 - staged Planner：纳入 Guide_Planner + 保留闸门外壳
- **Task name**: v6.3 - Planner staged prompt ← PROMPT_Guide_Planner
- **目标**: 重写 `build_planner_staged_messages`，使 system（或等价）完整承载 `PROMPT_Guide_Planner.txt` 的思考框架与字段释义，并与现有 GENERATION/REVISION、只写 Planner 字段、禁止 playlist/script 等约束并存且可切换。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 对照 Guide 验收关键可见约束：如「不是 Curator/不选曲」「不是 Script Writer」「按主题类型思考」「给 Curator 方向而非代劳选曲」等不得因拼接丢失。
  - 实现建议（择一，保持简单）：
    1. 运行时读取仓库内 Guide 文件（路径相对包/项目根，失败则明确报错）；或
    2. 将 Guide 正文以模块常量/旁路 `.txt` 放入 `modules/theme/`（便于打包），`prompts_staged` 只做外壳拼接。
  - **禁止**把 Guide 压成几句摘要替代正文；允许极薄外壳（mode / FORBIDDEN / output schema 提醒）。
  - user 侧继续注入当前 `PlanState` JSON；REVISION 时强调跟随 `critic.issues` / `actions`、最小改动。
  - 与 v6.1 structured output / sanitize 字段清单一致（不引入未支持键）。
- **Input**: `PROMPT_Guide_Planner.txt`、现有 `build_planner_staged_messages`
- **Output**: staged Planner messages 可对照 Guide 做全文级 diff 验收
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts_staged.py`
  - （可选）`src/podcast_ai/modules/theme/guides/PROMPT_Guide_Planner.txt` 或加载根目录原文件
- **Estimated complexity**: M（2–3 小时，主要为文本整合与自检）

---

### Task 02 - staged Music Curator：纳入 Guide_Music-Curator + 保留闸门外壳
- **Task name**: v6.3 - Curator staged prompt ← PROMPT_Guide_Music-Curator
- **目标**: 重写 `build_music_curator_staged_messages`，完整纳入 `PROMPT_Guide_Music-Curator.txt`，并保留 GENERATION/REVISION、只写 `playlist`（含 v4 解释字段）、禁止重设计 episode / 改 script 等 staged 约束。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01 并行）
- **Description**:
  - Guide 关键约束须在最终 prompt 中明确可见：如「不是 Planner、不重设计 episode」「选录音而非标题关键词」「硬/软约束区分」「填写 selection_reason / sequence_role / planner_alignment / transition_logic」等。
  - 拼接策略与 Task 01 保持一致（同一加载方式），避免两套机制。
  - REVISION：只修 Critic 指向的 playlist 项；上游 Planner 字段只读。
  - 不改 Curator sanitize / response schema（除非发现 prompt 与 schema 明显冲突，做最小对齐）。
- **Input**: `PROMPT_Guide_Music-Curator.txt`、现有 `build_music_curator_staged_messages`
- **Output**: staged Curator messages 可对照 Guide 验收
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts_staged.py`
  - （可选）同 Task 01 的 guides 旁路文件
- **Estimated complexity**: M（2–3 小时）

---

### Task 03 - 编排外壳抽公共小函数（防重复、不伤语义）
- **Task name**: v6.3 - staged envelope helper
- **目标**: 将 GENERATION/REVISION 提示、闸门声明、FORBIDDEN、state JSON user 消息等重复逻辑抽成极小 helper，保证 Planner/Curator 外壳一致；**不**改变 Script Writer / Critic 行为（可不动或仅复用 helper 签名）。
- **类型**: backend
- **依赖关系**: Task 01, Task 02（或在二者落地时顺带完成）
- **Description**:
  - 控制在 `prompts_staged.py` 内局部函数即可；禁止新建大型 prompt 框架包。
  - 文档字符串注明：Guide 正文来源文件名，便于 AC5 定位。
- **Input**: Task 01/02 合并后的重复片段
- **Output**: 可维护的拼接结构，语义与分任务一致
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts_staged.py`
- **Estimated complexity**: S（≤1 小时）

---

### Task 04 - 轻量断言：Guide 关键句 + mode 切换仍在
- **Task name**: v6.3 - prompt composition smoke tests
- **目标**: 不调 LLM；断言 staged Planner/Curator messages 在 generation/revision 下均包含 Guide 特征片段（如 “Do not choose songs” / “Do not redesign the episode”）以及 REVISION/GENERATION 外壳；确认未改 `prompts.py` legacy 入口。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 可选：Guide 文件可读、拼出的 system 长度显著大于旧短 prompt（防误用摘要）。
  - 不强制端到端跑通真实 API；若本地有既有 staged orchestrator mock 测，保持绿即可。
- **Input**: Task 01–02 完成物
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_prompts_staged_guides.py`（新建）
- **Estimated complexity**: S（1 小时）

---

### Task 05 - 范围确认与文档一句说明（最小）
- **Task name**: v6.3 - scope note
- **目标**: README 或 `prompts_staged` 模块头注释标明：staged Planner/Curator 已对齐仓库 Guide 文件；`legacy` 未同步；Script Writer/Critic 本轮未改。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 一两句即可，避免长文档。
- **Input**: 实现结果
- **Output**: 开发者能快速知道改哪里、对照哪份 Guide
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts_staged.py`（模块注释）
  - （可选）`README.md` 一行
- **Estimated complexity**: S（≤30 分钟）
