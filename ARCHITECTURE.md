# AI 音乐 Podcast 自动生成工具 — 系统技术架构文档

**文档角色**：面向贡献者与二次开发者的技术架构说明，与 [`PRD.md`](PRD.md) 中的产品需求互为补充：PRD 描述「要什么」，本文描述「代码里如何实现、模块如何划分、数据如何流转」。  
**代码布局**：`src/podcast_ai/`（Typer CLI + 可选 Gradio Developer Console + 分层模块）。  
**运行时形态**：本地单体 Python 应用；主入口仍为 CLI。v5.0 起可另启本机 Gradio 开发者控制台（浏览器访问，默认如 `localhost:7860`），**仅作调试/实验入口，非正式 C 端产品前端**；业务仍经 `core/pipeline.py` 调用 LLM（默认 OpenAI 兼容端点，常见为 OpenRouter）与 TTS。

---

## 1. 系统目标与边界

- **目标**：从「主题 + 目标时长」生成可执行的节目策划（含段落、目标歌单、串词结构），在用户自备本地曲库的前提下，完成 **按策划顺序** 的映射、TTS、时间线混音、响度处理与导出。
- **明确边界**：
  - 不抓取版权音乐；曲库由用户放入目录。
  - 阶段二 **严格按策划顺序** 映射本地文件，不做 BPM 重排或贪心替换（见 `modules/selection/`）。
  - LLM/TTS 依赖网络与 API Key；音频处理依赖 **FFmpeg**（经 `pydub` 等间接调用）。

---

## 2. 技术栈（与实现对齐）

| 类别 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.10+ | |
| CLI | Typer | `cli.py` |
| Developer Console（v5.0） | Gradio | 本地开发者调试 UI；复用 pipeline，不引入 React |
| 配置 | YAML + 环境变量（pydantic-settings） | `infra/config.py` |
| LLM | OpenAI Chat Completions 兼容 HTTP | `infra/llm_client.py`，支持请求体附加 `response_format`（OpenRouter 结构化输出） |
| TTS | 可插拔供应商 | `infra/tts_client.py`：`edge` / `elevenlabs` / `minimax` |
| 音频 | pydub、librosa、FFmpeg | 混音与时间线在 `modules/mixing/`；intro 估计在 `intro_align.py` |
| 元数据 | mutagen 等 | `modules/library/scanner.py` |
| 校验 | Pydantic | `core/models.py` 等 |

---

## 3. 分层架构

```
┌─────────────────────────────────────────────────────────┐
│ 接口层                                                    │
│  · cli.py（Typer）：解析参数、调用 pipeline、退出码           │
│  · console/（Gradio，v5.0+）：本机开发者控制台，只装配/展示；v5.1 按三阶段切换；v5.2 阶段一运行态洞察与 Snapshot 可读编辑；v5.3 阶段二 MixParams 时间线试听与 `vm_seconds` 编辑（仍不写业务逻辑）     │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│ 应用层：core/pipeline.py                                   │
│  plan_episode / create_episode / create_episode_stage2 /   │
│  finalize_episode_stage3                                   │
└───────────────────────────┬─────────────────────────────┘
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
   modules/theme    modules/library +      modules/mixing +
   （规划/多 Agent）  selection + voiceover   mastering + exporter
         │                  │                  │
         └──────────────────┼──────────────────┘
                            ▼
                   infra/（llm、tts、config、storage、audio_backend）
```

- **接口层**：只做 I/O 与装配，不含领域规则。CLI 与 Developer Console **并列**，均只调用 `pipeline` / Settings，不复制业务逻辑。
- **应用层**：编排阶段流程、统一计时与异常语义（`PodcastAIError` / `PlanMappingError` 等）。
- **领域模块**：按 PRD 功能拆分，`core/models.py` 提供跨模块数据结构。
- **基础设施**：对外部系统（HTTP API、文件系统、缓存）的封装，便于替换实现。

---

## 4. 端到端阶段划分与命令映射

产品演进后在代码中形成 **清晰的「规划 →（可选）拆解的制作 → 导出」链路了**，与 CLI 对应如下。

### 4.1 阶段一：节目策划（Plan）

- **入口**：`podcast-ai plan-episode …`
- **核心 API**：`core.pipeline.plan_episode(request, agent_mode=...)`
- **行为**：
  1. `ThemePlanner.generate_plan_and_state()` 生成内存中的 `EpisodePlan` + `PlanState`。
  2. `validate_state_conforms_to_schema(state, agent_mode=...)` 做契约校验。
  3. 生成 `episode_id`，落盘：
     - **完整状态**：`{output_dir}/episodes/{episode_id}/plans/state.json`
     - **阶段二输入子集**：`…/plans/{episode_id}.json`（由 `build_episode_snapshot_from_state` 从 state 剪枝而来，字段见 `infra/storage/paths.py`）

- **`agent_mode`**：
  - `multi_agent`（默认）：`PlanOrchestrator` 驱动四角色 + Critic 迭代。
  - `single_agent`：单次 LLM 调用产出 **无 `critic`/`control` 的 state 子集**，同样经校验后写入上述两文件。

阶段一 **不再以旧版独立 EpisodePlan 文件作为主产物路径**；阶段二默认读取 `<episode_id>.json`（`Stage2Snapshot`）。

### 4.2 阶段二（制作）：一站式导出 vs 可编辑参数拆分

**路径 A — 一站式（适合无需微调转场参数）**

- **命令**：`podcast-ai create-episode <snapshot.json> <music_dir>`
- **API**：`create_episode(snapshot_path, music_dir, …)`
- **顺序**：加载 snapshot → 扫库 → `select_tracks_by_snapshot` → 计算 segment 边界 → TTS → `Mixer.build_mix` → 母带 → 导出 Show Notes。

**路径 B — v4.5 拆分（适合人工微调 crossfade / intro 相关参数后再导出）**

1. **命令**：`podcast-ai create-episode-stage2 …`  
   **API**：`create_episode_stage2`  
   **产出**：`{episode_root}/mix_params/{episode_id}_mix_params.json`（`MixParamsJSON`：tracks、voiceovers、`transitions` 等），**不导出最终音频**。

2. **命令**：`podcast-ai finalize-episode-stage3 <*_mix_params.json>`  
   **API**：`finalize_episode_stage3`  
   **行为**：`MixParamsJSON.model_validate_json` 严格校验 → `Mixer.render_final_mix_from_mix_params` → 母带 → 导出。

### 4.3 阶段二输入契约：`Stage2Snapshot`

由阶段一的 `{episode_id}.json` 解析而来（`core.pipeline._load_stage2_snapshot`），结构与 `build_episode_snapshot_from_state` 一致：

- 顶层：`schema`、`meta`（含 `request_id`、`theme`、`language`、`target_duration_seconds`）、`segments`。
- 每段 `segments[i]`：`segment_id`、`name`、`target_duration_seconds`、`playlists`（即 state 中的 `playlist` 列表）、`script`（`segment_intro`、`between_tracks` 等）。

阶段二 **不再以旧 EpisodePlan JSON 为主输入**；内存里仍会构造 `EpisodePlan` 的简化视图供导出/Show Notes 复用（`_episode_plan_from_snapshot`）。

---

## 5. 多 Agent 架构（阶段一核心）

### 5.1 设计要点

- **共享事实源**：所有协作围绕 `PlanState`（`modules/theme/state.py` 中的 `Dict` 约定 + 校验函数）进行，而非各自独立的文本。
- **有限迭代**：外层循环由 `PlanOrchestrator` 控制；`control.max_iterations` 限制 Critic 评估轮数上限（初始化见 `initialize_plan_state`，默认 `DEFAULT_MAX_ITERATIONS`，当前代码为 **5**）。
- **结构化输出**：各 Agent 在支持的 LLM 配置下通过 `response_format` + JSON Schema（`agent_response_schemas.py` / `build_openrouter_response_format`）约束输出；解析统一走 `agent_json_parser.parse_agent_json_response`（非结构化路径下可回退 JSON 修复模块）。
- **写权限隔离**：每个 Agent 输出经 `_sanitize_*_patch`（Planner 为公开的 `sanitize_planner_patch`）裁剪后，`merge_plan_state` 合并进全局 state，防止越权改写字段。

### 5.2 角色与编排顺序

固定顺序（见 `orchestrator.py` 中 `_AGENT_ORDER`）：

1. **Planner** — `planner_agent.py`：更新 `meta.theme_description`、`global_constraints`、`plan`、`segments` 的设计字段（不含 playlist/script）。
2. **Music Curator** — `music_curator_agent.py`：仅写各 segment 的 `playlist`（曲目与顺序）。
3. **Script Writer** — `script_writer_agent.py`：仅写 `segments[*].script`（段首与曲间串词）。
4. **Critic** — `critic_agent.py`：只写 `critic.*` 与 `control.next_agent` 等控制字段，不直接改业务段落内容。

**一轮（iteration）语义**：从 `control.next_agent` 指定的角色起，**依次执行到 Critic（含）**。  
若 `critic.pass == True`，置 `control.status = "completed"` 并提前结束；否则递增 `control.iteration`，进入下一轮，下一轮起点由 Critic 写入的 `next_agent` 决定。  
若在某轮中捕获 `AIServiceError`，将 `control.status = "error"` 并中断；若用尽迭代仍未通过，则 `status = "max_iterations_reached"`。

### 5.3 与 PRD 契约表的关系

PRD §6.3 的「可读 / 可写 / 禁止写」字段表是设计与代码的共同契约；实现上通过 **prompt 约束 + sanitize + merge** 三层减小漂移。新增字段时应同步：

- `state_schema.json`（仓库根目录，外部契约参考）
- `state.py` 内校验逻辑
- `prompts.py` 与各 Agent 的 response schema

### 5.4 可审计落盘（v3.6）

当 `settings.app.multi_agent_audit_enabled` 为真时，`PlanOrchestrator` 构造 `FilePlanAuditSink`，目录为：

`{output_dir}/audit/multi_agent/{request_id}/`

写入内容包括（详见 `plan_audit.py`）：

- 每次 Agent 调用：`iteration{i}_{agent_slug}.json`（含 raw 文本、解析后的 patch 等）。
- 每轮 state 合并完成后：`iteration{i}_state.json`。
- 异常时可选：`iteration{i}_state_partial.json`。

写盘失败 **记录日志但不阻断主流程**，避免与模型错误混淆。

### 5.5 单 Agent 模式差异

- **产出结构**：顶层仅 `schema_version`、`meta`、`global_constraints`、`plan`、`segments`（无 `critic`/`control`）。
- **调用**：`ThemePlanner._generate_plan_state_single_agent_subset` 使用 `build_theme_planner_messages` + 可选结构化输出 schema `SINGLE_AGENT_STATE_SUBSET_SCHEMA`。
- **校验**：`validate_state_subset_for_single_agent`；pipeline 出口仍调用 `validate_state_conforms_to_schema(..., agent_mode="single_agent")`。

---

## 6. 制作链路关键技术（阶段二 / 三）

### 6.1 选曲与顺序

- `modules/selection/selector.py`：`select_tracks_by_snapshot` 按 snapshot 中 playlist **顺序** 映射本地库；缺失则抛错中断（`PlanMappingError`），符合 PRD「不让贪心算法替换 plan」。

### 6.2 Segment 边界与串词时序

- `compute_segment_boundaries_from_snapshot`：在已知 crossfade 策略下计算 **真实歌曲时间边界**，供 TTS 段对齐（继承 PRD v1.3+：不按 `target_duration_seconds` 估算插词点）。
- `VoiceoverService.generate_voiceovers_from_snapshot`：基于边界与脚本生成各语音切片路径。

### 6.3 混音时间线与转场语义（v3.9.1+）

`modules/mixing/mixer.py` 按 **snapshot 展开的时间线** 交替编排串词与曲目，三类边界：

| 边界类型 | Crossfade |
|----------|-----------|
| 歌 → 串词 | 否 |
| 串词 → 歌 | 是（v4.2+ 可结合 intro 估计） |
| 歌 → 歌 | 是 |

### 6.4 Intro 估计与串词→歌限幅（v4.2–v4.4）

- `modules/mixing/intro_align.py`：`estimate_track_intro_seconds` 返回 `intro_seconds`、`confidence`、`reason`。
- 配置项（`Settings.audio`）：如 `voice_music_intro_align_enabled`、`voice_music_intro_align_max_seconds`、`voice_music_crossfade_seconds` 等，与 PRD v4.3「crossfade 不超过串词时长」一致，由 mixer 在构建参数与渲染阶段执行。

### 6.5 母带与导出

- `modules/mastering/processor.py`：对混音结果做响度归一化等到导出友好状态。
- `modules/exporter/exporter.py`：写入最终 MP3 与 Show Notes 等。

---

## 7. 核心数据模型索引

| 模型 / 别名 | 定义位置 | 用途 |
|-------------|----------|------|
| `EpisodeRequest` | `core/models.py` | CLI / pipeline 输入 |
| `EpisodePlan` | `core/models.py` | 内存规划视图、导出元数据、旧接口兼容 |
| `PlanState` | `modules/theme/state.py` | 阶段一共享状态（dict） |
| `Stage2Snapshot` | `core/models.py` | 阶段二输入（Pydantic） |
| `MixParamsJSON` | `core/models.py` | v4.5 阶段二产出 / 阶段三输入 |
| `AudioRenderConfig` | `core/models.py` | crossfade、响度、比特率等渲染参数 |

仓库根 `state_schema.json` 为 PlanState 的结构化参考；代码中另有运行时校验函数与之对齐。

---

## 8. 目录与文件布局（磁盘）

在 `app.output_dir` 下（默认来自 `config.yaml`），典型一期节目：

```text
output/
  episodes/
    {episode_id}/
      plans/
        state.json              # 完整 PlanState
        {episode_id}.json       # Stage2Snapshot（阶段二主输入）
      mix/
        mix.wav                 # 中间混音（一站式 create-episode）
      mix_params/
        {episode_id}_mix_params.json   # v4.5 阶段二产出（可选流程）
      final/
        {episode_id}.mp3
        {episode_id}_show_notes.md
  cache/                        # 扫库缓存、TTS 缓存等
  audit/multi_agent/{request_id}/   # 多 Agent 审计（可选）
```

---

## 9. 配置与安全

- **配置文件**：`config.yaml`（`podcast-ai init-config` 生成模板）。
- **密钥**：优先环境变量（如 `PODCAST_AI_LLM__API_KEY`、`PODCAST_AI_TTS__…`），避免把真实 Key 写入仓库。
- **日志**：`core/logging_config.py`，CLI 支持 `--log-level`、`--log-file`。

---

## 10. 测试与质量

- 测试位于 `tests/`，覆盖 orchestrator、各 Agent、sselection、mixing、TTS、pipeline 等关键路径；结构化输出与 JSON 解析有专项用例（如 `test_llm_structured_output.py`、`test_agent_json_parser.py`）。

---

## 11. 架构决策摘录（历史结论）

以下结论仍适用；细节见 PRD 迭代说明与 git 历史。

- **AD-v3.0**：阶段一引入多 Agent + `PlanState`，仍在单体仓库内以子模块实现，不引入独立服务。
- **AD-v4.1**：TTS 供应商抽象收敛到 `infra/tts_client.py`，CLI 仅切换 provider。
- **AD-v4.2 / v4.5**：intro 估计与「阶段二参数 JSON + 阶段三渲染」拆分，混音语义 backward compatible（歌→歌规则不因 intro 改动）。
- **AD-v5.0：Developer Console — Gradio 本地开发者控制台（迭代二十五）**
  - **状态**：Accepted
  - **结论**：**需要小幅架构调整（是）**——扩展接口层，不改变应用层/领域层/基础设施层边界与阶段划分
  - **背景**：PRD v5.0 要求交付本机 Gradio **开发者调试控制台**（非正式 C 端前端），用于可视化改参、Run、结构化观察 Pipeline 状态与产物；CLI 必须保留
  - **最小改动方案**：
    - 新增接口入口（建议包路径 `src/podcast_ai/console/`，如 `app.py` / `launch`），用 Gradio 做表单与状态展示
    - Console **只调用**现有 `core.pipeline`（`plan_episode` / `create_episode` / `create_episode_stage2` / `finalize_episode_stage3` 等）与 `Settings`；禁止在 UI 层重写选曲、混音、TTS 逻辑
    - 本轮至少支持：核心参数编辑、参数集保存/加载（快照级）、Run 后展示阶段状态/产物路径/日志与错误/耗时；完整 Experiment 对比可后续迭代
    - 依赖：将 `gradio` 加入可选或主依赖清单；启动失败给出明确错误（端口占用、导入失败等）
    - **明确不做**：不因本迭代引入 React / 独立 Web 产品栈；不把 Console 当作多用户正式前端
  - **影响面**：
    - 主要：`cli` 旁新增 `console/`、依赖声明、README 启动说明
    - 不改：`modules/*` 领域规则、`pipeline` 阶段契约、混音/TTS 抽象（除非仅为 Console 暴露已有返回值做展示）
- **AD-v5.2：阶段一 Console — 运行态洞察 + Snapshot 可读编辑（迭代二十七）**
  - **状态**：Accepted
  - **结论**：**需要小幅调整（是）**，但**不改变**系统分层、阶段契约与 `Stage2Snapshot` schema；能力收敛在 `console/`
  - **背景**：PRD v5.2 要求在阶段一面板展示 iteration / 当前 Agent / 失败原因，并以播出时间线结构可读编辑 snapshot，保存为同目录新文件供阶段二消费
  - **最小改动方案**：
    - **运行态洞察**：优先从现有日志或 `audit/multi_agent` 事件抽取；若解析脆弱，可在 `PlanOrchestrator` 增加可选、无副作用的进度回调/结构化日志字段（iteration、agent、error），供 Console 订阅——**不**改变编排语义
    - **Snapshot 可读编辑**：在 `console/` 内做「timeline 视图 ⇄ `Stage2Snapshot`」双向适配（章节串词 → 曲目 → 曲间串词交错）；写回前用既有 Pydantic/`Stage2Snapshot` 校验；默认**新文件名**保存，不覆盖源文件
    - 禁止把业务校验/选曲/混音逻辑搬进 UI；保存失败明确报错
  - **影响面**：
    - 主要：`src/podcast_ai/console/`（UI + 适配器）；可选轻量触达 `modules/theme/orchestrator.py` 的可观测性钩子
    - 不改：`Stage2Snapshot` 字段契约、阶段二输入格式、pipeline 阶段边界
- **AD-v5.3：阶段二 Console — 节目时间线试听与转场 `vm_seconds` 编辑（迭代二十八）**
  - **状态**：Accepted
  - **结论**：**需要小幅调整（是）**，但**不改变**系统分层、`MixParamsJSON` schema 与阶段三渲染契约；能力收敛在 `console/`
  - **背景**：PRD v5.3 要求在阶段二面板按节目时间线展示音乐/串词/转场节点，支持本地音频试听（含进度条），并在线编辑 `transitions[*].vm_seconds`，另存新 JSON 供阶段三消费
  - **最小改动方案**：
    - 在 `console/` 内新增「MixParams 时间线视图 ⇄ `MixParamsJSON`」适配（音乐 → 串词 → 转场信息交错），模式对齐 v5.2 的 snapshot timeline，**不**改 `core/models.py` 中 MixParams 字段定义
    - 试听仅读取 JSON 中已有本地路径（曲目 `file_path`、串词 `audio_path`），用 Gradio Audio/进度控件播放；不做二次混音或重算 intro
    - 编辑聚焦 `vm_seconds`；保存前用 `MixParamsJSON` 校验；非法值明确报错；默认同目录**新文件**保存，不覆盖源文件
    - 阶段三仍只消费校验后的 MixParamsJSON；Console 不复制 `Mixer.render_final_mix_from_mix_params` 逻辑
  - **影响面**：
    - 主要：`src/podcast_ai/console/`（阶段二面板 UI + MixParams timeline 适配器）
    - 不改：`create_episode_stage2` / `finalize_episode_stage3` 契约、`Mixer` 转场语义、`MixParamsTransition` 字段集合

---

## 12. 常见问题（给新贡献者）

1. **阶段二读哪个文件？**  
   阶段一打印的 `{episode_id}.json`（在 `episodes/.../plans/`），不是旧的 `plans/<plan_id>.json` 主路径。

2. **多 Agent 从哪读状态机？**  
   `modules/theme/orchestrator.py` 的 `while iteration <= max_iterations` 与 `_run_round_from`。

3. **为何仍保留 `EpisodePlan`？**  
   兼容导出、Show Notes 与部分测试；主输入契约已迁移到 `Stage2Snapshot`。

4. **JSON 修复是否主路径？**  
   OpenRouter 结构化输出开启时以严格 schema 为主；`json_repair` 主要作为非结构化或兜底路径（见 `parse_agent_json_response` 参数）。

5. **Gradio Console 和正式 Web UI 是一回事吗？**  
   不是。v5.0 Developer Console 仅供开发者本机调试，必须复用 `pipeline`；正式创作者 Web UI 仍属后续扩展（PRD Phase 5）。

---

*文档维护建议：当修改 `pipeline.py` 阶段划分、`orchestrator.py` 状态机、snapshot 字段或接口层入口（CLI / Console）时，请同步更新本文与 PRD 中的契约描述。*
