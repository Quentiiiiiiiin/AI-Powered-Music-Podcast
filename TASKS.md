## 版本 v7.3（迭代四十一：Script Writer 整期扁平串词契约 + 系统回填）

基于 PRD **v7.3**、ARCHITECTURE **AD-v7.3** 与根目录 **`ScriptWriter_FlatCue_Adapter.md`**：

- **Agent 输入**：策划要点 + 带 `episode_track_index` 的扁平歌单（各段 `playlist` 顺序拼接）。
- **Agent 输出**：定长 `script_before[]`（长度 = 总曲目 `N`；元素 `string | null`；插在第 i 首之前；不设「最后一首之后」槽）。
- **系统 mapper**：按下标回填既有 `segments[*].script.segment_intro` / `between_tracks`；锚点用下标，不用曲名；映射失败 = sanitize 失败，**禁止半套 script**。
- **下游不变**：持久化 `script` schema、snapshot、TTS、Console 时间线、Planner/Curator、编排 FSM。

**硬约束**：不双格式并存；revision 仍输出整份定长 `script_before`；审计保留「原始扁平输出 + 映射后 script」。

---

### Task 01 - 扁平歌单索引与 Agent 输入装配
- **Task name**: v7.3 - flatten episode playlist for Writer input
- **目标**: 从当前 PlanState 计算总曲目 `N`，生成带 `episode_track_index`（及 segment 归属）的扁平歌单视图，供 prompt / schema 共用；`N=0` 时 Writer 阶段明确失败。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 纯函数：`(segment_index, track_index_in_segment) ↔ episode_track_index` 双向可逆。
  - 输入展示格式对齐 Adapter（`[i] Artist – Track (seg_id)`）；策划要点可压缩，勿整份 PlanState 无脑倾倒。
  - 建议落在 `script_flat_mapper.py` 或 `script_writer_agent` 旁小模块，避免塞进 orchestrator。
- **Input**: Curator 已写好的 `segments[*].playlist`
- **Output**: `N` + 扁平表 + 下标映射表
- **Files involved**:
  - `src/podcast_ai/modules/theme/script_flat_mapper.py`（新建，建议）
- **Estimated complexity**: S（1 小时）

---

### Task 02 - Writer structured output + sanitize（仅 `script_before`）
- **Task name**: v7.3 - script_before schema & sanitize
- **目标**: `response_format` 顶层仅 `script_before`；`minItems=maxItems=N`；元素 `string|null`。sanitize 拒绝其它顶层键、长度不符、错误类型；非空串沿用既有语言启发式。**本步不写** `segments[*].script`。
- **类型**: backend
- **依赖关系**: Task 01（需要 `N`）
- **Description**:
  - 替换现有 `build_script_writer_response_schema(segment_count)` 为按 `N` 定长数组。
  - 空串可规范化为 `null`（实现定一种并测稳）。
  - 接入 v7.2 output retry：sanitize 失败走再 generate，不落半套。
- **Input**: Task 01 的 `N`
- **Output**: 合法扁平 patch（仅 `script_before`）
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/script_writer_agent.py`
- **Estimated complexity**: M（1.5–2 小时）

---

### Task 03 - 系统 mapper：`script_before` → 既有 `segments[*].script`
- **Task name**: v7.3 - flat cues → segment_intro / between_tracks
- **目标**: 将定长数组按下标写回旧 schema；段内第 0 首 → `segment_intro`；段内第 `k≥1` 首 → `between_tracks` 的 `{ after_track_index: k-1, text }`。补齐空槽，保证下游假设稳定。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 统一跨段缝策略（Adapter 推荐：一律落在「后一首所属位置」的 before）。
  - 每段保证有 `script`；未出现文案的 intro / between 用 `null`（与现网可空一致）。
  - `between_tracks` 建议补齐 `0 … len(playlist)-2`，无文案 `text: null`。
  - 长度不一致等 → 抛 `AIServiceError`，**不** merge 半套。
- **Input**: 合法 `script_before` + 当前 segments playlist 结构
- **Output**: 可 `merge_plan_state` 的 `segments[*].script` patch
- **Files involved**:
  - `src/podcast_ai/modules/theme/script_flat_mapper.py`
- **Estimated complexity**: M（2 小时）

---

### Task 04 - `ScriptWriterAgent.run` 接线 + 审计双产物
- **Task name**: v7.3 - wire Writer run: sanitize → map → merge
- **目标**: `run()` 流程改为 generate → parse → sanitize(`script_before`) → mapper → merge 映射后的 script；审计同时保留原始扁平 JSON 与映射后 script（失败尝试仍可只落 raw）。
- **类型**: backend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - 删除/停用旧「模型直接输出 segments[].script」路径，避免双格式。
  - mapper 失败与 sanitize 同等：触发 output retry 或最终 `AIServiceError`。
  - **不改** Orchestrator FSM 调用点签名（仍 `run(state, mode, …)`）。
- **Input**: Task 02–03
- **Output**: 落盘 state 仍是旧 `script` 形态
- **Files involved**:
  - `src/podcast_ai/modules/theme/script_writer_agent.py`
  - `src/podcast_ai/modules/theme/plan_audit.py`（仅当需扩展 artifact 字段时）
- **Estimated complexity**: M（1.5 小时）

---

### Task 05 - staged / legacy prompt + Guide 对齐扁平契约
- **Task name**: v7.3 - Writer prompts & Guide for script_before
- **目标**: staged 与 legacy Writer messages、以及 `PROMPT_Guide_Script-Writer.txt` 改为：整期扁平歌单输入、只填 `script_before[i]`、删除「填 segment_intro/between_tracks」的 Agent 填写说明；强调不报幕章节、约每 3–5 首插点、打招呼/收束在最后一首之前。
- **类型**: backend
- **依赖关系**: Task 01（可与 02–04 并行起草，联调依赖扁平输入）
- **Description**:
  - revision：附带上一版已映射的扁平串词视图 + `critic.actions`；要求整份定长输出；未点名处保持原文。
  - 可注明系统会把旧 `location`（如 `segments[i].script…`）译成 `episode_track_index` 供理解。
  - **不做**：回修轮切回旧分段 JSON。
- **Input**: Adapter §4/§7、现有 Guide
- **Output**: 双轨 prompt 口径一致
- **Files involved**:
  - `src/podcast_ai/modules/theme/guides/PROMPT_Guide_Script-Writer.txt`
  - `src/podcast_ai/modules/theme/prompts_staged.py`
  - `src/podcast_ai/modules/theme/prompts.py`
- **Estimated complexity**: M（2 小时）

---

### Task 06 - revision「未点名保持」+ 单测覆盖 Adapter 验收点
- **Task name**: v7.3 - preserve cues on revision + mapper tests
- **目标**: revision 路径确保只改 action 相关下标时其它 cues 与上一轮落盘一致（允许 null/空白规范化）；单测覆盖 Adapter §9：`N=1`、跨段、最后一首前、全 null、长度≠N 失败、映射不污染下游假设。
- **类型**: backend
- **依赖关系**: Task 03, Task 04, Task 05
- **Description**:
  - 「保持」可用：映射前对比上一轮反扁平结果，或映射后做最小 diff 校验；选简单一种。
  - 不强制真实 LLM；不测 TTS/混音全链路（契约未变）。
- **Input**: Task 03–05
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_script_flat_mapper_v73.py`（新建）
  - （可选）`tests/test_script_writer_agent_v73.py`
- **Estimated complexity**: M（2 小时）
