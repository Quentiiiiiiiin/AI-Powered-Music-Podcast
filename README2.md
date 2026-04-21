## v3.0 阶段一多 Agent 改造简要说明

> 临时文档，仅供自己参考。

### 1. 阶段一总体架构（v3.0）

- **核心目标**：将原来的「单次 ThemePlanner LLM 调用」升级为多 Agent Pipeline：  
  `Planner → Music Curator → Script Writer → Critic`，基于共享 `PlanState` 做有限次回修。
- **唯一事实源**：`PlanState`（JSON 结构）  
  顶层：`schema_version / meta / global_constraints / plan / segments / critic / control`

### 2. 主要模块与文件

- `modules/theme/state.py`
  - `initialize_plan_state(request)`：根据 `EpisodeRequest` 生成初始 `PlanState`，结构与 `state_schema.json` 对齐。
  - `merge_plan_state(base, patch)`：字段级 merge，支持 list-of-dict 按索引合并（避免不同 Agent 互相覆盖）。
  - `validate_plan_state_schema / assert_plan_state_valid`：最小 schema + 必填字段校验。

- `modules/theme/llm_planner.py`
  - 旧路径（v2.2，默认）：`ThemePlanner.generate_plan(request)` 使用单次 LLM 调用 + JSON 解析，行为不变。
  - 新路径（v3.0）：`ThemePlanner.generate_plan(request, use_orchestrator=True)`：
    - 调用 `PlanOrchestrator.run(request)` 得到最终 `PlanState`
    - `_episode_plan_from_state(state)` 将 `PlanState` 转为 `EpisodePlan`

- `modules/theme/planner_agent.py`：`PlannerAgent` + 公开 `sanitize_planner_patch`；写 `plan` 与 `segments[*]` 段落骨架；更新 `control.last_updated_by`。
- `modules/theme/music_curator_agent.py`：`MusicCuratorAgent` + `_sanitize_curator_patch`；只写 `segments[*].playlist`。
- `modules/theme/script_writer_agent.py`：`ScriptWriterAgent` + `_sanitize_script_writer_patch`；只写 `segments[*].script`（`segment_intro + between_tracks`）。
- `modules/theme/critic_agent.py`
  - `CriticAgent` + `_sanitize_critic_patch`；只写：
    - `critic.pass / critic.scores / critic.issues / critic.actions`
    - `control.next_agent`
  - 当 `pass=false` 时，要求 `actions` 至少一条。

- `modules/theme/orchestrator.py`
  - `PlanOrchestrator.run(request, initial_state=None)`：
    - 一轮 iteration = 从 `control.next_agent` 起步，按顺序至少执行到 Critic：
      - 起点为 `Planner` → `Planner→Curator→Writer→Critic`
      - 起点为 `Music Curator` → `Curator→Writer→Critic`
      - 等等
    - 使用 `control.max_iterations` 控制最多轮次（=最多几次 Critic 评估）：
      - `critic.pass=True` → `status=completed`，提前结束。
      - 超过 `max_iterations` 仍 `pass=false` → `status=max_iterations_reached`。
      - 任一 Agent 抛 `AIServiceError` → `status=error` 并记录 `last_error_agent/message`。

### 3. PlanState → EpisodePlan 的映射（v3.0 路径）

- 文件：`modules/theme/llm_planner.py::_episode_plan_from_state`
- 关键映射：
  - `meta.target_duration_seconds` → `EpisodePlan.target_duration_seconds`
  - `meta.overall_bpm_range` → `EpisodePlan.overall_bpm_range`（若合法）
  - `plan.segments_design` → `EpisodePlan.style_description`
  - `segments[*]`：
    - `name / target_duration_seconds / bpm_range / mood`
    - `script.segment_intro` → `EpisodeSegment.host_script`
    - `playlist[*]` → `PlaylistItem`：
      - `track + artist` → `recommended_tracks[0]` 文本
      - `bpm` → 写入 `search_hints["bpm"]`
  - `critic` → `EpisodePlan.critic_summary`（pass / scores / issues / actions）
  - `meta.generation_trace`（Planner / Curator / Writer / Critic 顺序）→ `EpisodePlan.generation_trace`（每步 `{agent: ...}`）

### 4. EpisodePlan 模型扩展

- 文件：`core/models.py::EpisodePlan`
  - 新增字段：
    - `critic_summary: Optional[Dict[str, Any]]`
    - `generation_trace: Optional[List[Dict[str, Any]]]`
  - 旧字段保持不变（兼容阶段二既有逻辑）。

### 5. 测试覆盖（与 v3.0 相关）

- `tests/test_theme_planner.py`：保持 v2.2 单次 LLM 解析路径的稳定性（严格 JSON、字段校验等）。
- `tests/test_theme_state.py`：校验 `PlanState` schema 与 merge 行为（含 list-of-dict 按索引合并）。
- `tests/test_planner_agent.py`：只写允许字段 / 越权写 `playlist` 报错。
- `tests/test_music_curator_agent.py`：只写 `playlist`，保留段落骨架 / 越权写 `script` 报错。
- `tests/test_script_writer_agent.py`：只写 `script`，最小语言一致性 / 越权写 `playlist` 报错。
- `tests/test_critic_agent.py`：`pass=false` 必须有 actions / 顶层越权写 `plan` 报错。
- `tests/test_theme_orchestrator.py`：
  - 单轮收敛（第一轮 Critic 即 `pass=true`）。
  - 使用 `control.next_agent` 的回修路径（第一轮 `pass=false`→下一轮从 Planner 起步，第二轮 `pass=true`）。
  - `max_iterations` 限制（永远 `pass=false` 时在上限轮次退出）。 

### 6. 常用命令（本地操作）

- **安装依赖（开发环境）**：
  - `pip install -e ".[dev]"`（或按根 README 中说明执行）

- **初始化配置（第一次使用）**：
  - 生成默认配置与示例环境变量：
    - `podcast-ai init-config`
  - 根据需要编辑：
    - `config.yaml`
    - `.env`（设置 LLM/TTS 的 API Key 等）

- **阶段一：只做规划（生成 EpisodePlan + 目标歌单）**：
  - 使用默认单 Agent 路径（v2.2）：
    - `podcast-ai plan-episode --topic "Late Night Chill" --duration 60`
  - 试验多 Agent 路径（需要在代码中切换为 `use_orchestrator=True` 或添加 CLI 入口后）：
    - 伪代码示例：
      - `planner = ThemePlanner()`
      - `plan = planner.generate_plan(request, use_orchestrator=True)`

- **只跑与 v3.0 相关的核心测试**：
  - `pytest -q tests/test_theme_state.py tests/test_planner_agent.py tests/test_music_curator_agent.py tests/test_script_writer_agent.py tests/test_critic_agent.py tests/test_theme_orchestrator.py`

- **跑 ThemePlanner 相关测试（v2.2 + v3.0）**：
  - `pytest -q tests/test_theme_planner.py tests/test_theme_orchestrator.py`

- **跑全量测试**：
  - `pytest -q`

- **通过多 Agent 路径生成 EpisodePlan（示意代码片段）**：
  - `planner = ThemePlanner()`  
  - `plan = planner.generate_plan(request, use_orchestrator=True)`
