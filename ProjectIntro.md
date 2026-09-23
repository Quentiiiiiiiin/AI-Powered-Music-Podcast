# podcast-ai — 项目技术介绍

**受众**：工程师 / 技术评审 / 二次开发者（非最终创作者用户手册）  
**配套文档**：使用与命令见 [`README.md`](README.md)；需求演进见 [`PRD.md`](PRD.md)；架构细节与 AD 记录见 [`ARCHITECTURE.md`](ARCHITECTURE.md)  
**当前阶段**：本地单体 MVP，产品迭代验证中（PRD 约 v6.0 量级；实现以代码与 `ARCHITECTURE.md` 为准）

---

## 1. 项目是什么

**podcast-ai** 是一款用 Python 实现的 **AI 音乐 Podcast 自动生成工具**：给定主题与目标时长，用大模型生成可执行的节目策划（段落结构、目标歌单、主持串词），再在用户自备本地曲库的前提下，自动完成曲目映射、TTS 串词合成、时间线混音、响度母带与成品导出。

形态特征：

| 维度 | 说明 |
|------|------|
| 运行时 | **本机单体** Python 应用（非微服务、非云端 SaaS） |
| 主入口 | Typer CLI：`podcast-ai …` |
| 调试入口 | Gradio **Developer Console**（本机浏览器，非正式 C 端产品 UI） |
| 版权边界 | **不抓取、不分发受版权保护的音乐**；曲库由用户自行放入目录 |
| 产物 | 一期节目目录下：计划 JSON、可选混音参数 JSON、中间 `mix.wav`、最终 MP3 + Show Notes |

一句话：**「LLM 负责策划与文案，本地音频管线负责按策划把歌和主持声做成一期可听的 Podcast。」**

---

## 2. 解决什么问题

### 2.1 业务痛点

手工做一期「主题音乐电台 / Podcast」通常要：

1. 定主题与段落节奏；
2. 选歌、排顺序、写串词；
3. 录/合成人声，再按时间线与歌曲对齐；
4. 做淡入淡出、响度统一、导出发布文件。

对独立创作者或小团队，**策划成本高、串词与歌曲时间线易错位、混音参数难调、调试反馈慢**。

### 2.2 本项目如何切入

| 痛点 | 项目做法 |
|------|----------|
| 策划与歌单生成慢 | 阶段一：单 Agent 或 **多 Agent 协作** 输出结构化 `PlanState` / Snapshot |
| 串词与歌曲对不齐 | 阶段二：按 **真实映射后的时间线边界** 插词，而非仅用目标时长估算 |
| 混音「黑盒」难调 | v4.5：**阶段二产出可编辑 MixParamsJSON** → 人工微调 → 阶段三再渲染 |
| 工程调试效率低 | v5.x：Gradio Console 可视化改参、分阶段 Run、观察日志与产物 |

### 2.3 明确不做的事

- 不替代合法曲库来源（无爬虫下载正版/盗版曲库）。
- Console **不是**面向大众的创作者产品前端；正式 Web UI 仍属后续扩展。
- 阶段二 **不**用 BPM 贪心重排破坏策划顺序（历史 MVP 曾做过，已按 PRD 纠正为 plan/snapshot 驱动）。

---

## 3. 技术栈

| 类别 | 选型 | 代码落点 |
|------|------|----------|
| 语言 | Python **3.10+** | `src/podcast_ai/` |
| 打包 / CLI 入口 | setuptools + Typer | `pyproject.toml` → `podcast-ai = podcast_ai.cli:main` |
| 配置 | YAML + `.env` + **pydantic-settings** | `infra/config.py` |
| 数据契约 | **Pydantic v2** 模型 | `core/models.py` |
| LLM | **OpenAI Chat Completions 兼容 HTTP**（`httpx`，非官方 SDK） | `infra/llm_client.py` |
| LLM 路由（可选） | OpenRouter：`base_url` + 可选 `provider` + `response_format` 结构化输出 | 配置项 `llm.*` |
| TTS | 可插拔：`edge` / `elevenlabs` / `minimax` | `infra/tts_client.py` |
| 音频处理 | **pydub**（叠化/导出）+ **FFmpeg/ffprobe**（必需） | `infra/audio_backend.py`、`modules/mixing/` |
| 音频分析 | **librosa**（BPM 估计、intro 启发式） | `intro_align.py` 等 |
| 元数据 | mutagen 等 | `modules/library/scanner.py` |
| 开发者 UI | **Gradio** Blocks（本机 `127.0.0.1:7860`） | `console/` |
| 测试 | pytest | `tests/` |

依赖声明见仓库根目录 `pyproject.toml`。音频链路强依赖系统 PATH 中的 **FFmpeg**。

---

## 4. Architecture

### 4.1 分层总览

```
┌────────────────────────────────────────────────────────────┐
│ 接口层                                                       │
│  cli.py（Typer）                                             │
│  console/（Gradio：装配 UI + runner 映射，不写领域规则）         │
└────────────────────────────┬───────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────┐
│ 应用层：core/pipeline.py                                      │
│  plan_episode / create_episode /                            │
│  create_episode_stage2 / finalize_episode_stage3            │
└────────────────────────────┬───────────────────────────────┘
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  modules/theme        modules/library +     modules/mixing +
  （规划 / 多 Agent）     selection +           mastering +
                         voiceover             exporter
         │                   │                   │
         └───────────────────┼───────────────────┘
                             ▼
              infra/（config · llm · tts · storage · audio_backend）
```

设计原则：

- **接口层只装配**：CLI 与 Console 并列，都只调 `pipeline` / `Settings`。
- **应用层编排阶段**：统一计时、异常语义（如 `PodcastAIError`、`PlanMappingError`）。
- **领域模块按能力拆分**：theme / library / selection / voiceover / mixing / mastering / exporter。
- **基础设施可替换**：LLM、TTS、扫库缓存、路径约定集中在 `infra/`。

### 4.2 包结构（实现视角）

```
src/podcast_ai/
  cli.py                 # 命令行：init-config / plan / stage2 / stage3 / create / console …
  console/               # Gradio Developer Console
    app.py               # UI 控件与事件绑定
    runner.py            # 表单 → Settings/pipeline；抓取 logging 供 UI
    presets.py           # 参数快照（不含密钥）
    snapshot_timeline.py # 阶段一 snapshot 可读时间线适配
    mix_params_timeline.py
  core/
    pipeline.py          # 阶段编排入口
    models.py            # 跨模块 Pydantic 模型
    exceptions.py / logging_config.py
  infra/
    config.py / llm_client.py / tts_client.py
    audio_backend.py / storage/
  modules/
    theme/               # Planner / Curator / Writer / Critic / Orchestrator / prompts / state
    library/ selection/ voiceover/
    mixing/              # mixer.py · intro_align.py
    mastering/ exporter/
```

### 4.3 端到端阶段与命令映射

产品将一期制作拆成清晰阶段（也支持一键合并）：

| 阶段 | CLI | Pipeline API | 核心产出 |
|------|-----|--------------|----------|
| **一 · 规划** | `plan-episode` | `plan_episode` | `plans/state.json` + `plans/{episode_id}.json`（Stage2Snapshot） |
| **二 · 准备** | `create-episode-stage2` | `create_episode_stage2` | TTS 缓存音频 + `mix_params/{id}_mix_params.json`（**不**出最终 MP3） |
| **三 · 导出** | `finalize-episode-stage3` | `finalize_episode_stage3` | `mix/mix.wav` + `final/{id}.mp3` + Show Notes |
| **一键制作** | `create-episode` | `create_episode` | 等价「阶段二制作逻辑 + 阶段三导出」，跳过人工改 MixParams |

磁盘约定（默认 `app.output_dir`）：

```text
output/episodes/{episode_id}/
  plans/state.json
  plans/{episode_id}.json          # 阶段二主输入
  mix_params/{episode_id}_mix_params.json
  mix/mix.wav                      # 混音中间件（无损 PCM）
  final/{episode_id}.mp3           # 母带后发布文件
  final/{episode_id}_show_notes.md
output/cache/                      # 扫库 / TTS 等
output/audit/multi_agent/{request_id}/   # 可选多 Agent 审计
```

### 4.4 阶段一：多 Agent 架构（核心 AI）

共享事实源是 **`PlanState`**（`modules/theme/state.py` 约定 + 校验；参考根目录 `state_schema.json`），不是各 Agent 互不相关的自由文本。

固定角色顺序（`PlanOrchestrator` / `_AGENT_ORDER`）：

1. **Planner** — 主题描述、全局约束、段落骨架（不含 playlist / script）。
2. **Music Curator** — 仅写各段 `playlist`（曲目名与顺序）。
3. **Script Writer** — 仅写 `segments[*].script`（段首 / 曲间串词）。
4. **Critic** — 写 `critic.*` 与 `control.next_agent`，不直接改业务段落内容。

一轮 **iteration** = 从 `control.next_agent` 指定角色起，**至少执行到 Critic**。`critic.pass == true` 则完成；否则迭代（有 `max_iterations` 上限）。

可靠性与可观测性：

- 支持 OpenRouter **`response_format` + JSON Schema** 约束 Agent 输出；
- patch 经 **sanitize + merge**，防止越权改写；
- 可选 **按轮次审计落盘**（`iteration{i}_{agent}.json` / `iteration{i}_state.json`）；
- 另有 **`single_agent`** 模式：一次调用产出无 `critic`/`control` 的 state 子集，契约仍对齐同一出口校验。

### 4.5 阶段二 / 三：音频制作链路

```text
Stage2Snapshot
    → LibraryScanner（扫库 / 缓存元数据）
    → select_tracks_by_snapshot（严格按 playlist 顺序映射；缺歌即失败）
    → compute_segment_boundaries…（真实时间边界）
    → VoiceoverService + TTSClient（串词音频）
    → Mixer：
         · stage2：build_mix_params → MixParamsJSON
         · stage3 / create：render / build_mix → mix.wav
    → MasteringService（ffmpeg loudnorm → final MP3）
    → Exporter（Show Notes + EpisodeResult）
```

混音时间线语义（与 PRD / mixer 一致）：

| 边界 | Crossfade |
|------|-----------|
| 歌 → 串词 | 否（硬切，保人声起句清晰） |
| 串词 → 歌 | 是（可结合 intro 估计动态 vm，再限幅） |
| 歌 → 歌 | 是（`audio.crossfade_seconds`） |

Intro：`intro_align.estimate_track_intro_seconds` 用 librosa RMS / onset 等启发式估计前奏结束点，供「串词→歌」叠化决策；失败则回退默认 vm 并打日志。

**中间件 vs 成品**：

- `mix/mix.wav`：混音后、母带前的 **无损中间文件**；
- `final/*.mp3`：loudnorm（默认约 -14 LUFS）后再编码（当前管线常用 **320k**）的 **发布文件**。

### 4.6 Developer Console（接口层扩展）

`podcast-ai console` → Gradio 本机服务。约束（架构 AD）：

- **只调用**现有 `pipeline`，禁止在 UI 层重写选曲 / 混音 / TTS；
- API Key 仍走 `.env`，Preset 快照不落密钥；
- 按阶段一 / 二 / 三 Tabs 组织；观察面板展示状态、产物路径、日志（runner 内临时挂 `_BufferLogHandler` 抓取本次 Run 的 logging）、音频预览等。

---

## 5. 具体做了什么（能力清单）

按「已实现能力」归纳，便于对外展示与 onboarding：

### 5.1 节目策划（AI）

- 输入：主题、时长、语言（zh/en）、`single_agent` / `multi_agent`。
- 输出：统一 `state.json` + 阶段二用 Snapshot `{episode_id}.json`。
- 多 Agent 迭代精炼、结构化输出、schema 校验、可选审计目录。
- Prompt / Agent response schema 集中在 `modules/theme/`。

### 5.2 本地曲库与选曲

- 扫描目录中的音频，缓存元数据（时长、可选 BPM 等）。
- **按策划 playlist 顺序映射到文件**；映射失败明确报错并阻断（避免静默换歌导致串词错位）。

### 5.3 主持 TTS

- 供应商抽象：Edge / ElevenLabs / MiniMax。
- 串词插入点基于 **实际歌曲时间线边界**，支持段首 `segment_intro` 与曲间 `between_tracks`。
- TTS 结果可缓存，降低重复调用成本。

### 5.4 混音与转场

- 按 snapshot 展开的块序列（music / voice）拼接，而非「先全曲 crossfade 再硬插串词」。
- 歌间 crossfade；串词→歌动态叠化 + intro 对齐 + 串词时长限幅。
- v4.5：产出可编辑 **MixParamsJSON**（tracks / voiceovers / transitions），阶段三严格校验后渲染。

### 5.5 母带与导出

- ffmpeg `loudnorm` 响度标准化后导出最终 MP3。
- 生成 Markdown Show Notes（曲目清单、时间戳等）。

### 5.6 工程与调试体验

- 完整 CLI、配置模板（`init-config`）、分级日志（`--log-level` / `--log-file`）。
- Gradio Developer Console：改参、Preset、分阶段 Run、运行态洞察与时间线编辑（v5.x）。
- `tests/` 覆盖 orchestrator、Agent、选曲、混音、TTS、pipeline 等关键路径。

### 5.7 迭代演进摘要（对外讲故事用）

| 阶段 | 代表能力 |
|------|----------|
| v1.x | 端到端 MVP；串词时序修正；plan 驱动选曲；ElevenLabs |
| v2.x | 串词/歌曲边界过渡策略；Planner prompt 强化 |
| v3.x | 多 Agent + PlanState；结构化输出；审计落盘；Snapshot 契约统一 |
| v4.x | MiniMax TTS；intro 对齐；MixParams 人机协同；OpenRouter provider |
| v5.x | Gradio Developer Console（三阶段布局、运行态、时间线编辑） |

更细的验收标准与 User Story 见 `PRD.md` 迭代记录。

---

## 6. 关键数据契约（工程师速查）

| 名称 | 位置 | 用途 |
|------|------|------|
| `EpisodeRequest` | `core/models.py` | 规划请求入口 |
| `PlanState` | `theme/state.py` | 多 Agent 共享状态 |
| `Stage2Snapshot` | `core/models.py` | 阶段二输入（`{episode_id}.json`） |
| `MixParamsJSON` | `core/models.py` | 阶段二产出 / 阶段三输入 |
| `AudioRenderConfig` | `core/models.py` | crossfade、响度、bitrate、intro 开关等 |
| `EpisodeResult` | `core/models.py` | 最终导出结果（路径、时长、show notes、曲目） |
| `state_schema.json` | 仓库根 | PlanState 外部契约参考 |

---

## 7. 给工程师的使用入口（非用户手册）

```bash
# 可编辑安装
pip install -e .
pip install ".[test]"   # 可选

# 配置
podcast-ai init-config
# 编辑 .env：LLM / TTS Key

# 主路径示例
podcast-ai plan-episode "主题" 60 --agent-mode multi_agent -l zh
# 用户自行把推荐曲目放入 music_dir 后：
podcast-ai create-episode-stage2 path/to/ep_xxx.json path/to/music
# 可选：人工编辑 mix_params JSON
podcast-ai finalize-episode-stage3 path/to/ep_xxx_mix_params.json

# 或一键
podcast-ai create-episode path/to/ep_xxx.json path/to/music

# 调试 UI
podcast-ai console
```

更完整的命令、配置表与排错见 `README.md`。

---

## 8. 架构决策摘录（沟通时常用）

- **单体仓库内模块化**，不引入独立 LLM/TTS 微服务（AD-v3.0）。
- **TTS 供应商集中在 `infra/tts_client.py`**，业务只选 provider（AD-v4.1）。
- **混音参数人机协同**：计算与最终渲染拆开（AD-v4.5）。
- **Console 只扩展接口层**，不改变 pipeline 阶段契约与领域规则（AD-v5.0+）。

---

## 9. 文档导航

| 文档 | 用途 |
|------|------|
| 本文 `ProjectIntro.md` | 对外/对内工程介绍：是什么、解决什么、栈、架构、做了什么 |
| `README.md` | 安装、命令、配置、排错 |
| `ARCHITECTURE.md` | 分层、阶段、多 Agent、AD、贡献者 FAQ |
| `PRD.md` | 产品需求与迭代验收标准 |
| `TASKS.md` | 当前迭代任务拆分 |

---

*维护建议：当 `pipeline` 阶段划分、`PlanOrchestrator` 状态机、Snapshot / MixParams 契约或 CLI/Console 入口变更时，请同步更新本文与 `ARCHITECTURE.md` / `PRD.md`。*
