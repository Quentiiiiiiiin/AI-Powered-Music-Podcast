## 版本 v6.1（迭代三十：Planner / Music Curator State Schema v4 升级）

基于 PRD **v6.1**：在既有 `state_schema.json` 上**仅升级 Planner / Music Curator 相关字段契约**（参考 `Schema_Planner_v4.txt`、`Schema_Music-Curator_v4.txt`）。同步 structured output、Agent sanitize/patch、最终 **`state` 文件**。

**硬约束**：
- **snapshot（`<episode_id>.json`）对外格式不变**；阶段二消费契约不改。
- Script Writer / Critic / `control` 业务字段本轮不改。
- Planner **不写** `playlist` / `script`；Music Curator **只写** `segments[*].playlist`（含 v4 解释字段）。
- `Schema_Planner_v4.txt` 样例中的 `playlist` 占位属示意，**落地以 Curator 契约为准**。
- 避免双轨字段堆叠：以 v4 字段为主替换旧 Planner 表达（如 `emotion_curve` / `segment_design` / `tone` 等），不为兼容保留两套并行必填。

---

### Task 01 - `state_schema.json` + PlanState 运行时契约对齐 v4
- **Task name**: v6.1 - state schema / empty state / validate
- **目标**: 将仓库 `state_schema.json` 与 `state.py`（空模板、必填键、轻量校验）升级为 Planner/Curator v4 字段；`schema_version` 升至可区分版本（如 `v4.0`）；**不改** `script` / `critic` / `control` 结构。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - **Planner 侧（对齐 Schema_Planner_v4，以实现清单为准）**：
    - `meta`：保留 `request_id`（系统写入）+ `theme` / `theme_description` / `language` / `target_duration_seconds`；新增 `theme_type` / `theme_subject` / `theme_relationship`（可空字符串）；弱化/移除对 `overall_bpm_range` 的 Planner 必填依赖。
    - `global_constraints`：`energy_strategy`、`sonic_world[]`、`avoid[]`（替换原 `tone` / `language_style` 为主契约）。
    - `plan`：`segment_count`、`episode_direction`、`segments_design`（替换原 `emotion_curve` 必填）。
    - `segments[*]`：保留 `segment_id` / `order` / `name` / `target_duration_seconds`；新增 `narrative_function`、`scene`、`sonic_direction[]`、`lyrical_direction[]`、`anchor_tracks[]`、`reference_material[]`、`sequence_direction[]`、`transition_to_next`；移除对 `bpm_range` / `mood` / `segment_design` 的 Planner 必填。
  - **Curator 侧**：`playlist[*]` 在 `track` / `artist`（`bpm` 可继续允许 null，可选保留）之上增加 `selection_reason`、`sequence_role`、`planner_alignment[]`、`transition_logic`。
  - `merge_plan_state` / 空 segment 模板同步默认键，保证 staged 失败路径仍可补齐空 playlist/script。
- **Input**: `Schema_Planner_v4.txt`、`Schema_Music-Curator_v4.txt`、现有 `state.py`
- **Output**: state 契约与示例文件对齐 v4；非法类型有明确错误
- **Files involved**:
  - `state_schema.json`
  - `src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 02 - Planner：structured output + sanitize + prompts
- **Task name**: v6.1 - Planner I/O 对齐 Schema_Planner_v4
- **目标**: Planner 的 response schema、sanitize/patch、staged/legacy prompts（及若共用契约的 single_agent）只产出/合并 v4 规划字段；禁止写入 `playlist` / `script` / `critic` / `control`。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 更新 `PLANNER_RESPONSE_SCHEMA`（及 single_agent 子集中与 Planner 重叠部分）。
  - 扩展 `_PLANNER_SEGMENT_ALLOWED_KEYS` / meta / plan / global_constraints 白名单与必填校验；缺关键字段明确失败。
  - 更新 `prompts.py` 与 `prompts_staged.py` 中 Planner 字段说明与 JSON 示例；Critic staged 的 Planner 评审提示改为对照 v4 交付物（不改 Critic 输出 schema）。
  - **范围**：`staged` + `legacy` 均适配；single_agent 若仍输出同一 state 子集则一并改，否则在 README 标明「仅 multi_agent」。
- **Input**: Task 01 字段清单
- **Output**: Planner 端到端可写入升级后的 state 规划部分
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/planner_agent.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/prompts_staged.py`
  - （必要时）`src/podcast_ai/modules/theme/llm_planner.py` single_agent 路径
- **Estimated complexity**: L（3–4 小时）

---

### Task 03 - Music Curator：playlist v4 解释字段 I/O
- **Task name**: v6.1 - Curator playlist 对齐 Schema_Music-Curator_v4
- **目标**: Curator structured output、sanitize、prompts 要求每条 playlist 含选曲解释字段；非法/缺关键字段明确失败；仍禁止改 plan 骨架与 script。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - `_PLAYLIST_ALLOWED_KEYS` / `build_music_curator_response_schema` 扩展：`selection_reason`、`sequence_role`、`planner_alignment`、`transition_logic`（`bpm` 策略：保留可选或从 required 降级，避免无 BPM 时硬失败——以实现简洁为准并写清）。
  - Prompt 明确依据 Planner 的 `sonic_direction` / `sequence_direction` / `anchor_tracks` 等填写 `planner_alignment`。
  - staged + legacy Curator 路径同步。
- **Input**: `Schema_Music-Curator_v4.txt`、Task 01
- **Output**: state 中 `segments[*].playlist[*]` 带齐 v4 解释字段
- **Files involved**:
  - `src/podcast_ai/modules/theme/music_curator_agent.py`
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/prompts_staged.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 04 - Snapshot 剪枝：对外格式冻结（防字段泄漏）
- **Task name**: v6.1 - snapshot 仍输出旧子集
- **目标**: `build_episode_snapshot_from_state`（及校验）保证阶段二输入**字段集不变**；state 中新增的 Planner/Curator 字段**不得**进入 `<episode_id>.json` 的对外契约（尤其 playlist 解释字段、段落叙事字段）。
- **类型**: backend
- **依赖关系**: Task 01（可与 02/03 并行开发，联调依赖它们）
- **Description**:
  - 现状会把整个 `playlist` 对象拷进 `playlists`；v4 后必须**显式映射**为旧条目子集（至少 `track` / `artist`；若旧快照含 `bpm` 则按现网约定保留或剥离，与阶段二实际读取对齐，不扩新键）。
  - `meta` / `segments` 顶层仍只输出既有 snapshot 键；不把 `narrative_function` 等写入 snapshot。
  - 不改 `selector` / `create_episode` 解析逻辑。
- **Input**: 现有 snapshot 契约、`paths.py`
- **Output**: 扩字段后的 state → 旧格式 snapshot；阶段二无感
- **Files involved**:
  - `src/podcast_ai/infra/storage/paths.py`
  - （必要时）`src/podcast_ai/modules/theme/state.py` 中 snapshot 校验
- **Estimated complexity**: S（1 小时）

---

### Task 05 - 单测：v4 merge/sanitize + snapshot 不泄漏
- **Task name**: v6.1 - schema/sanitize/snapshot 回归
- **目标**: 用 fixture 锁定 Planner/Curator sanitize 接受 v4、拒绝越权；最终 state 含新字段；snapshot 仅含旧子集；Script/Critic 相关断言不因本轮被破坏。
- **类型**: backend
- **依赖关系**: Task 02, Task 03, Task 04
- **Description**:
  - 优先单元测，不依赖真实 LLM。
  - 覆盖：缺 `selection_reason` 等关键字段失败；Planner 写入 `playlist` 失败；snapshot 无 `selection_reason` / 无 `sonic_direction`。
- **Input**: Task 02–04 完成物
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_state_schema_v4.py`（新建，或拆入既有 test 文件）
  - 既有 planner/curator/snapshot 测试按需更新
- **Estimated complexity**: M（2 小时）

---

### Task 06 - 文档与 Console 冒烟说明（最小）
- **Task name**: v6.1 - README/范围说明
- **目标**: README（或简短注释）标明 state v4 与 snapshot 不变；确认 Console 阶段一仍可读/跑通（timeline 只依赖 snapshot 子集，不因 state 扩字段崩溃）。
- **类型**: backend
- **依赖关系**: Task 04
- **Description**:
  - 不强制改 Console UI；若 timeline 编辑器对未知 segment 键敏感，仅做「忽略额外键」的最小防护。
  - 写清 single_agent 是否已适配（承接 Task 02 决议）。
- **Input**: 实现结果
- **Output**: 开发者可按文档理解双契约（富 state / 瘦 snapshot）
- **Files involved**:
  - `README.md`
  - （仅必要时）`src/podcast_ai/console/app.py`
- **Estimated complexity**: S（≤1 小时）
