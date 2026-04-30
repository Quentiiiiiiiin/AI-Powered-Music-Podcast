## 1. 技术选型

- **编程语言与运行环境**
  - **Python 3.10+**（PRD 已指定，生态成熟、音频与 AI 库丰富）
  - 本地运行，优先支持 **Windows**，兼容 macOS
  - 依赖管理：`venv + pip` 或 `poetry`（根据个人偏好选择其一）
- **核心第三方库（MVP）**
  - **音频处理**
    - `pydub`：音频切片、拼接、音量调整、crossfade 淡入淡出（依赖 FFmpeg，API 简单）
    - `librosa`：BPM、时长、基础音频特征分析；**v4.2** 起用于阶段二「下一首 intro」估计，以约束**串词→歌** crossfade 时机与重叠长度
    - 系统依赖：**FFmpeg**（单次安装，作为全局依赖）
  - **元数据读取**
    - `mutagen`：读取 mp3/wav 标签与基础元数据（时长、编码、标题等）
    - 或基于 `ffprobe` 的轻量封装作为备选
  - **LLM**
    - 抽象接口：`LLMClient`
    - 默认实现：接入 OpenAI / Claude / 其它兼容 LLM，模型名称与 API Key 通过环境变量或配置文件注入
  - **TTS**
    - 抽象接口：`TTSClient`
    - 支持实现：`edge` / `elevenlabs` / `minimax`（v4.1）
    - 供应商与模型选择：由 CLI 参数 + `.env` 配置驱动（无需改业务代码）
  - **配置与数据校验**
    - `pydantic` / `pydantic-settings`：用于配置加载、数据模型定义与校验
- **工具与基础设施**
  - CLI 框架：`Typer`（构建命令行工具，自动文档与良好帮助信息）
  - 日志：Python 标准库 `logging`，统一配置命令行与文件输出
  - 配置文件：`config.yaml`（音乐库路径、API 配置、默认参数等）

---

## 2. 系统架构

整体采用 **单体应用 + 分层架构**，以“个人开发、架构简单、易于扩展”为设计目标。

- **分层结构**
  - **接口层（Interface Layer）**
    - 形式：命令行工具（CLI），后续可加简单 GUI
    - 主要文件：`cli.py`
    - 职责：解析命令行参数、读取配置、调用应用服务；不包含业务逻辑
  - **应用层（Application Layer）**
    - 主要文件：`core/pipeline.py`
    - 职责：
      - 支持 **三阶段流程（v4.5+）**：① 规划阶段（输出歌单）→ ② 制作阶段（生成可编辑混音参数 JSON）→ ③ 最终混音导出阶段
      - 阶段一（v3.0）采用 **多 Agent 规划流水线**：Planner / Music Curator / Script Writer / Critic，基于共享 state 进行有限迭代回修
      - 串联 7 个 PRD 定义的功能模块（主题生成 → 扫库 → 选曲 → 混音 → 主持 → 母带 → 导出）
      - 管理整体流程、错误处理、进度日志与时间统计
      - 提供高层 API：`plan_episode`（仅规划）、`create_episode`（完整制作或从规划继续）
  - **领域模块层（Domain / Modules Layer）**
    - 按“功能模块”划分子包，每个模块只关注自己的业务规则与实现：
      - `modules/theme`：阶段一 Episode Plan 多 Agent 生成（LLM、共享 `PlanState`、编排与评估回修；具体文件见 §5 `modules/theme/`）
      - `modules/library`：本地音乐库扫描与元数据提取
      - `modules/selection`：按 plan 做本地曲目映射与缺失校验（不做二次重排）
      - `modules/mixing`：音轨拼接与 crossfade；按 PRD **v3.9.1** 语义时间线（歌→串词无 crossfade；串词→歌、歌→歌有 crossfade）；**v4.2** 在「串词→下一首」边界用 librosa 估计下一首 **intro**，参与 crossfade 时长与对齐点决策，失败时回退到与 v3.9.1 兼容的默认行为并打日志
      - `modules/voiceover`：主持串词生成与 TTS 语音生成
      - `modules/mastering`：Loudness normalization 与母带处理
      - `modules/exporter`：最终 MP3 导出与 Show Notes 输出
    - 模块之间通过核心数据模型（`core/models.py`）交互，避免相互直接耦合
  - **基础设施层（Infrastructure Layer）**
    - 对外部世界的全部访问集中在此层，提供可替换实现：
      - `infra/llm_client.py`：封装 LLM 调用（统一重试与日志）
      - `infra/tts_client.py`：统一 TTS 供应商抽象与路由（`edge` / `elevenlabs` / `minimax`），收敛输入校验、音频落盘、错误处理、日志；MiniMax 走同步语音合成（HTTP 非流式）链路
      - `infra/audio_backend.py`：封装 pydub / librosa / ffmpeg 的常用操作
      - `infra/storage/cache.py`：音乐库扫描结果缓存（JSON 或 SQLite）
      - `infra/config.py`：加载配置文件和环境变量
- **主流程（三阶段时序：v4.5+）**
  - **阶段一：规划与歌单输出（用户可仅执行此阶段）**
    1. 用户通过 CLI 输入主题和时长 → 解析为 `EpisodeRequest`
    2. `Pipeline.plan_episode(request)`：
      - 调用 `ThemePlanner.generate_plan`（v3.0 多 Agent）
      - 产出：可追踪的 `EpisodePlan`（含评估反馈摘要）
      - 将 `EpisodePlan` 持久化为 JSON，并输出可读的「目标歌单」供用户查阅
    3. 用户根据目标歌单到各平台搜索、下载或整理歌曲，放入指定本地目录
  - **阶段二：生成可编辑混音参数 JSON（中间产物，不导出最终音频）**
    1. 用户再次运行 CLI，指定阶段一 snapshot 路径与音乐目录
    2. `Pipeline.create_episode_stage2(...)`：
      - 扫描音乐库 → 按 plan 顺序做本地曲目映射与缺失校验
      - 生成 TTS 串词语音文件与插入策略
      - 生成并保存「混音参数 JSON」（tracks、串词信息/tts 文件路径/文本，以及转场参数如 intro/vm_candidate 等）
    3. CLI 输出：混音参数 JSON 路径（供用户编辑微调）
  - **阶段三：读取用户编辑后的参数 JSON，完成最终混音与导出**
    1. `Pipeline.finalize_episode_from_mix_params(mix_params_json_path, ...)`
    2. 对 JSON 参数做必要校验（非法值中止，不静默降级）
    3. 执行最终时间线混音（保持 v3.9.1 转场语义一致）→ `wav` + `mp3`，并导出 Show Notes
    4. CLI 输出：最终音频、时长、选曲列表等摘要信息

---

## 3. 数据模型

（建议使用 Pydantic 数据模型统一管理与校验）

- **请求与整体结果**
  - `EpisodeRequest`
    - `topic: str`：节目主题
    - `duration_minutes: int`：目标时长（分钟）
    - `language: Literal["zh", "en"]`：串词与 TTS 语言
    - `output_dir: Path`：输出目录
  - `EpisodeResult`
    - `episode_id: str`
    - `audio_path: Path`
    - `actual_duration_seconds: int`
    - `show_notes: str`
    - `tracks: list[SelectedTrack]`
- **节目规划与结构**
  - `EpisodePlan`
    - `segments: list[EpisodeSegment]`
    - `target_duration_seconds: int`
    - `overall_bpm_range: tuple[int, int] | None`
    - `style_description: str`
    - `plan_id: str`（用于与制作阶段关联、持久化与加载）
    - `critic_summary: dict | None`（多 Agent 评估与回修摘要）
    - `generation_trace: list[dict] | None`（可选：记录 Planner/Curator/Writer/Critic 的关键步骤）
  - `PlanState`（v3.0 共享 state，阶段一内部使用）
    - `meta: dict`
    - `global_constraints: dict`
    - `plan: dict`
    - `segments: list[dict]`
    - `critic: dict`
    - `control: dict`（含 `max_iterations`，默认 3）
  - `EpisodeSegment`
    - `name: str`（如「开场」「中段」「收尾」）
    - `target_duration_seconds: int`
    - `bpm_range: tuple[int, int] | None`
    - `mood: str`
    - `host_script: str`（该段串词草稿）
    - `target_playlist: list[PlaylistItem]`（该段目标歌单规划，供用户按此下载）
  - `PlaylistItem`（目标歌单中的单条推荐）
    - `segment_name: str`
    - `recommended_tracks: list[str]`（推荐曲目名称或描述）
    - `search_hints: dict`（搜索条件：艺术家、曲风、BPM 区间、关键词等，便于用户在各平台搜索）
- **音乐库与选曲**
  - `Track`
    - `id: str`
    - `file_path: Path`
    - `title: str | None`
    - `artist: str | None`
  - `TrackMetadata`
    - `track_id: str`
    - `duration_seconds: float`
    - `bpm: float | None`
    - `genre: str | None`
  - `TrackWithMetadata`
    - `track: Track`
    - `metadata: TrackMetadata`
  - `SelectedTrack`
    - `track: Track`
    - `start_time_in_episode: float`
    - `end_time_in_episode: float`
    - `effective_duration: float`（考虑 crossfade 后的有效占用时长）
- **主持与音频渲染配置**
  - `VoiceoverSegment`
    - `segment_id: str`
    - `text: str`
    - `audio_path: Path`
    - `insert_time_in_episode: float`
  - `AudioRenderConfig`
    - `sample_rate: int`
    - `bitrate: str`
    - `crossfade_seconds: float`
    - `loudness_target_lufs: float`
  - **（v4.5）可编辑混音参数 JSON（阶段二输出）**
    - `MixParamsJSON`
      - `meta`: episode/segment 关联信息、`schema_version`
      - `tracks`: 阶段二选曲后对应的本地音轨及其在时间线上的位置（start/end 或等价字段）
      - `voiceovers`: 每段串词对应的 TTS 音频落盘路径、串词文本与插入时间（insert_time）
      - `transitions`: 各边界的转场参数集合（例如 `form=crossfade`；包含 `intro`/`vm_candidate` 等可编辑字段）
      - **校验规则**：阶段三在执行前对 JSON 参数做必要校验，非法值明确报错并中止（不静默降级）

---

## 4. API 设计

当前阶段 API 以 **内部 Python 接口 + CLI 命令** 为主，非网络服务，便于本地个人使用。

- **应用层高阶 API**
  - 文件：`core/pipeline.py`
  - 主要函数：
    - `plan_episode(request: EpisodeRequest) -> EpisodePlan`
      - **阶段一**：多 Agent 协同生成 Episode Plan（含目标歌单与评估回修），输出为 JSON 文件；用户据此手动下载歌曲
    - `create_episode(plan_path: Path, music_dir: Path, ...) -> EpisodeResult`
      - 便捷封装：阶段二（生成可编辑混音参数 JSON）+ 阶段三（最终混音与导出）
    - `create_episode_stage2(plan_path: Path, music_dir: Path, ...) -> MixParamsJsonPath`
      - **阶段二**：生成 TTS 串词文件与「混音参数 JSON」（可编辑中间产物），不执行最终导出
    - `finalize_episode_stage3(mix_params_json_path: Path, ...) -> EpisodeResult`
      - **阶段三**：读取（并校验）用户编辑后的参数 JSON，执行最终时间线混音与导出（`wav` + `mp3`），保持与 v3.9.1 转场语义一致
- **领域模块接口（示例）**
  - `ThemePlanner`（模块 1：主题生成门面）
    - 文件：`modules/theme/llm_planner.py`
    - `generate_plan(request: EpisodeRequest, use_orchestrator: bool = False) -> EpisodePlan`
    - `use_orchestrator=False`：单次 LLM 调用（v2.x）
    - `use_orchestrator=True`（v3.0）：方法内懒加载 `PlanOrchestrator`，对返回的 `PlanState` 经 `_episode_plan_from_state` 转为 `EpisodePlan`
    - 输出包含：节目结构、段落、情绪、BPM 区间、串词与**目标歌单规划**（v2/v3 schema 细节以 PRD 与 `state` 为准）
  - `PlanOrchestrator`（v3.0，阶段一内部编排）
    - 文件：`modules/theme/orchestrator.py`；依赖 `planner_agent.PlannerAgent`、`music_curator_agent.MusicCuratorAgent`、`script_writer_agent.ScriptWriterAgent` 与 `critic_agent.CriticAgent`
    - `run(request: EpisodeRequest, initial_state: PlanState | None = None) -> PlanState`
    - 协调 Planner / Music Curator / Script Writer / Critic，按共享 state 执行有限回修迭代；**不**在此转换为 `EpisodePlan`
  - `LibraryScanner`（模块 2：音乐库扫描）
    - `scan_library(root_dir: Path) -> list[TrackWithMetadata]`
    - `scan_or_load_cache(root_dir: Path) -> list[TrackWithMetadata]`
    - 默认假设用户已根据目标歌单规划将本期候选歌曲放入指定目录
  - `TrackSelector`（模块 3：自动选曲与排序）
    - `select_tracks(plan: EpisodePlan, library: list[TrackWithMetadata]) -> list[SelectedTrack]`
    - 严格按 plan 的歌曲序列执行映射；若 plan 曲目无法映射到本地文件则报错并中断
  - `Mixer`（模块 4：自动混音）
    - `build_mix(selected_tracks: list[SelectedTrack], voiceovers: list[VoiceoverSegment], config: AudioRenderConfig) -> Path`
  - `VoiceoverService`（模块 5：主持语音）
    - `generate_voiceovers(plan: EpisodePlan) -> list[VoiceoverSegment]`
    - 支持通过命令参数选择 `edge` / `elevenlabs` / `minimax`；模型从 `.env` 读取
    - MiniMax 路径采用同步语音合成（HTTP 非流式）并接入现有音频插入流程；调用失败返回清晰错误信息（不静默失败）
  - `MasteringService`（模块 6：母带处理）
    - `apply_mastering(mix_path: Path, config: AudioRenderConfig) -> Path`
  - `Exporter`（模块 7：导出）
    - `export_episode(final_audio_path: Path, result_meta) -> EpisodeResult`
- **CLI 命令（基于 Typer）**
  - `podcast-ai init-config`
    - 生成默认配置文件 `config.yaml` 与 `.env.example`
  - `podcast-ai plan-episode --topic "Late Night Chill" --duration 60`
    - **阶段一**：生成节目结构与**目标歌单规划**，输出 JSON 与可读歌单；用户据此手动下载歌曲
  - `podcast-ai create-episode --plan-file "episode_xxx.json" --music-dir "D:\Music\本期节目"`
    - **阶段二**：用户准备好歌曲后，从规划文件继续，扫描目录 → 选曲 → 混音 → 主持 → 母带 → 导出
  - `podcast-ai scan-library --music-dir "D:\Music"`
    - 扫描音乐库并缓存分析结果（可选，用于提前了解已有曲目）

---

## 5. 项目目录结构

采用单仓库、单体应用、`src` 布局，方便个人开发与未来扩展：

```text
project-root/
  pyproject.toml / requirements.txt
  README.md
  PRD.md
  ARCHITECTURE.md
  config.example.yaml
  .env.example              # API Key 示例（不提交真实 Key）

  src/
    podcast_ai/
      __init__.py
      cli.py                # Typer 命令行入口

      core/
        models.py           # EpisodeRequest / Plan / Track 等核心模型
        pipeline.py         # create_episode 流水线
        logging_config.py
        exceptions.py

      infra/
        config.py           # 配置加载（config.yaml + 环境变量）
        llm_client.py       # LLMClient 抽象 + 默认实现
        tts_client.py       # TTSClient 统一供应商抽象与路由（edge / elevenlabs / minimax）
        audio_backend.py    # 对 pydub / librosa / ffmpeg 的统一封装
        storage/
          cache.py          # 音乐库扫描缓存（JSON/SQLite）
          paths.py          # 输出与临时文件目录管理；规划文件（EpisodePlan JSON）的保存与加载路径

      modules/
        theme/
          llm_planner.py            # ThemePlanner；v2 单次规划；PlanState→EpisodePlan；v3 内懒加载 PlanOrchestrator
          planner_agent.py          # PlannerAgent；sanitize_planner_patch（公开）
          music_curator_agent.py    # MusicCuratorAgent；_sanitize_curator_patch
          script_writer_agent.py    # ScriptWriterAgent；_sanitize_script_writer_patch
          critic_agent.py           # CriticAgent；_sanitize_critic_patch
          orchestrator.py           # PlanOrchestrator；调度四 Agent 与迭代控制
          state.py                  # PlanState、initialize/merge/assert
          prompts.py                # 各 Agent / v2 ThemePlanner 的 message 构建
        library/
          scanner.py        # 扫描目录、提取元数据
        selection/
          selector.py       # 按 plan 映射本地曲目与缺失校验（不做二次重排）
        mixing/
          mixer.py          # 时间线混音、歌↔串词↔歌 crossfade；v4.2：串词→下一首 intro 估计（librosa）与回退
        voiceover/
          tts_service.py    # 主持语音生成与管理
        mastering/
          processor.py      # Loudness normalization
        exporter/
          exporter.py       # 导出 MP3 + Show Notes

  tests/
    test_pipeline.py
    test_selection.py
    test_mixing.py
```

如需后续增加简单 GUI（如 Streamlit / PySimpleGUI），可在 `src/ui/` 下新增，不影响现有核心结构。

---

## 6. 关键技术难点与应对方案

- **1）音频分析与性能（BPM & 时长）**
  - 难点：本地音乐库规模较大时，BPM 计算与元数据提取会比较耗时。
  - 方案：
    - 首次全量扫描后，将结果缓存为 JSON/SQLite，后续只对新增或修改文件做增量扫描。
    - BPM 计算可采用近似方法（降低采样密度、只分析前 N 秒）以换取速度。
- **2）plan 一致性与本地曲目映射**
  - 难点：阶段二必须严格按 plan 播放顺序执行，且 plan 曲目未必能在本地目录找到同名或可匹配文件。
  - 方案：
    - `TrackSelector` 仅负责“按顺序映射 + 缺失检测”，不做 BPM 重排或贪心替代。
    - 引入明确的映射失败报告（缺失曲目清单、建议补齐项），失败即中断，避免 silently fallback。
    - 将“时长精度”从阶段二强约束中移除，优先保障 plan 一致性。
- **3）Crossfade 混音与音量一致性（含 v3.9.1 / v4.2）**
  - 难点：不同来源的音轨音量差异大；**串词→歌**需在可懂度与「intro 垫底、主歌将起」听感之间折中；intro 估计可能失败。
  - 方案：
    - 在混音前，对每首歌做一次粗略的音量归一化（RMS 或简单 LUFS 估计）。
    - **歌→歌**：维持 6–10 秒量级 crossfade（可配置），语义与 v3.9.1 一致，不因 v4.2 被改写。
    - **歌→串词**：无 crossfade（自然结束或硬切接人声）。
    - **串词→歌（v4.2）**：对紧随串词后的下一首本地音频用 **librosa** 做 intro 区间估计（RMS 包络、onset、节拍等启发式，阈值与回退策略见实现注释）；据此设定 crossfade 时机与重叠上限；**估计失败或置信度过低**时必须回退到文档化的默认 crossfade，并记录日志。
    - 在最终导出前，调用 `MasteringService` 使用 ffmpeg 的 `loudnorm` 或类似方案进行整体 Loudness normalization。
- **4）主持语音与歌曲边界对齐（v1.3 + v3.9.1 时间线）**
  - 难点：串词插入点若按目标时长估算，容易与实际歌曲边界错位；混音顺序须避免「整段歌先 crossfade 再插串词」导致的串词滞后。
  - 方案：
    - 串词插入点以 plan 与 snapshot 展开后的**真实歌曲边界**为准；整期顺序为 串词1 → segment1（歌曲…）→ …（见 PRD v3.9.1 示例）。
    - `Mixer` 按 **snapshot 单一时间线** 排布后再施加转场：**歌→串词**无 crossfade；**串词→歌**、**歌→歌**有 crossfade（v4.2 在串词→歌处叠加 intro 对齐逻辑）。
- **5）LLM 与 TTS 调用的可靠性与成本**
  - 难点：网络调用存在失败与超时风险，同时需要控制 token 与调用次数。
  - 方案：
    - 在 `LLMClient` / `TTSClient` 中内建重试、超时与基础日志机制。
    - 将模型名称、最大字数/时长、语言等参数配置化，方便按需调优成本与效果。
    - 通过 prompt 约束串词长度与风格，降低无效生成。
- **6）阶段一多 Agent 状态一致性（v3.0）**
  - 难点：多 Agent 协作时容易出现 JSON 非法、字段漂移、越权写入、回修循环失控。
  - 方案：
    - 以 `PlanState` 为唯一事实源，按读写契约限制每个 Agent 的可写字段。
    - `Critic` 输出结构化问题与修复动作；`Orchestrator` 控制有限迭代（默认 `max_iterations=3`）与提前收敛。
    - 对非法 JSON/缺字段增加重试与回退，确保最终 `EpisodePlan` 可执行。
- **7）跨平台依赖安装（尤其是 FFmpeg）**
  - 难点：Windows 与 macOS 上 FFmpeg 安装方式与路径各异，易导致运行时错误。
  - 方案：
    - 在 README 中提供面向 Windows/macOS 的简明安装步骤与验证命令。
    - 应用启动或首次音频操作前检查 FFmpeg 是否可用，若不可用则给出明确错误提示与参考链接。
    - 所有对 FFmpeg 的调用统一通过 `infra/audio_backend.py`，避免在各模块中散落命令调用。

- **8）（v4.5）阶段二→阶段三：可编辑参数校验与语义一致性**
  - 难点：用户编辑 crossfade 参数后，若不校验容易产生静默错位、重叠非法、或与 v3.9.1 转场语义不一致。
  - 方案：
    - 阶段三入口只依赖「阶段二输出的混音参数 JSON」，并对所有可编辑字段做严格校验。
    - 明确失败策略：非法值直接报错中止；必要时记录回溯信息，禁止静默回退到旧参数。
    - 保持转场语义：**歌→串词无 crossfade；串词→歌 crossfade；歌→歌 crossfade**，由阶段三严格执行。

---

## 7. Architecture Decisions

- **AD-2026-03-v1.4：TTS 供应商切换到 ElevenLabs（迭代四）**
  - **状态**：Accepted
  - **结论**：**不需要调整系统架构（否）**，仅需实现层最小改动
  - **背景**：PRD v1.4 指出 Edge TTS 音质不满足发布要求，目标切换至 ElevenLabs
  - **最小改动方案**：
    - 保持现有分层与模块边界不变（`VoiceoverService` + `TTSClient` 抽象继续沿用）
    - 将 `TTSClient` 默认实现从 Edge 路径切换为 ElevenLabs
    - 在 `config.yaml` 增加/确认 ElevenLabs 配置项（`api_key`、`voice_id`、`model`、`output_format`）
    - 明确失败策略：TTS 调用失败时返回可读错误并中断当前流程，不静默降级
  - **影响面**：
    - 主要影响 `infra/tts_client.py`、`modules/voiceover/tts_service.py` 与配置文件
    - 对 Pipeline、数据模型、目录结构无结构性变更
- **AD-2026-03-v3.0：阶段一 Episode Plan 多 Agent 化（迭代八）**
  - **状态**：Accepted
  - **结论**：**需要小幅架构调整（是）**，但不改变整体分层与单体形态
  - **背景**：PRD v3.0 要求阶段一从单次模型调用升级为 Planner / Music Curator / Script Writer / Critic 的多 Agent Pipeline，并基于共享 state 做有限回修迭代
  - **最小改动方案**：
    - 保持现有两阶段流程、CLI 入口和应用层边界不变
    - 仅在 `modules/theme` 内新增 `orchestrator + 4 agents + state schema`，由 `plan_episode` 调用
    - `EpisodePlan` 增加可选追踪字段（`critic_summary`、`generation_trace`），用于可解释性与问题回溯
    - 迭代控制参数仅保留 `max_iterations`（默认 3），避免过度配置
  - **影响面**：
    - 主要影响 `modules/theme/*` 与 `core/pipeline.py` 的阶段一编排
    - 阶段二混音链路、TTS 链路、导出链路保持不变
  - **实现落地（当前仓库）**：阶段一四 Agent 各独占 `*_agent.py`，类与同文件内 `_sanitize_*_patch`（Planner 为公开 `sanitize_planner_patch`）共存；`PlanOrchestrator` 仅依赖上述四模块与 `state` 等；`ThemePlanner` 在 v3 路径方法内懒加载 `orchestrator`。
- **AD-2026-04-v4.1：新增 MiniMax TTS + TTS 模块重构（迭代十九）**
  - **状态**：Accepted
  - **结论**：**需要小幅架构调整（是）**，保持单体与现有分层不变
  - **背景**：PRD v4.1 要求新增 MiniMax（同步语音合成，HTTP 非流式），并将 TTS 模块重构为统一供应商抽象，以支持 `edge` / `elevenlabs` / `minimax` 的命令级切换与 `.env` 模型配置
  - **最小改动方案**：
    - 保持 `VoiceoverService` 与 `TTSClient` 抽象边界不变，仅在 `infra/tts_client.py` 内新增 MiniMax provider 与统一路由层
    - 抽取并复用公共逻辑（输入规范化、音频落盘、错误处理、日志），减少供应商分支散落在业务层
    - CLI 增加 `tts_provider` 参数（`edge` / `elevenlabs` / `minimax`）；各供应商模型参数统一走 `.env`
    - MiniMax 定制字段（`voice_setting`、`audio_setting`）本轮固定默认值，先保证同步链路稳定，不提前做复杂配置化
  - **影响面**：
    - 主要影响 `infra/tts_client.py`、`infra/config.py`、`cli.py`、`modules/voiceover/tts_service.py`
    - 不改变 `pipeline` 编排、混音链路与数据模型主结构
- **AD-2026-04-v4.2：串词→下一首歌 crossfade 与 intro 对齐（迭代二十）**
  - **状态**：Accepted
  - **结论**：**需要小幅架构调整（是）**，不改变分层、单体形态与阶段二主流程
  - **背景**：PRD v4.2 要求在「串词→下一首」边界用 librosa 估计下一首 **intro**，以设定 crossfade 时机与重叠长度，改善长串词尾部听感；**歌→歌** crossfade 语义须与 v3.9.1 保持一致
  - **最小改动方案**：
    - 能力收敛在 `modules/mixing`：在 `Mixer`（或同包内新增单一小模块，如 `intro_align.py`）实现 intro 估计纯函数 + 回退策略，由 `build_mix` 在应用「串词→歌」crossfade 时调用
    - 不重构整条 `pipeline`；不新增跨服务；`AudioRenderConfig` 或 `config` 仅增加与 v4.2 相关的上限/开关（若 PRD 要求可配置则最小字段集）
    - intro 估计失败或置信度过低：**必须**回退到与 v3.9.1 兼容的默认 crossfade，并打日志，禁止静默错位
  - **影响面**：
    - 主要影响 `modules/mixing/mixer.py`（及可选同目录辅助模块）、`infra/config.py`（若增加配置项）
    - 不改变阶段一、`TrackSelector`、TTS 主抽象边界

- **AD-2026-04-v4.5：新增阶段三支持人工微调最终 crossfade（迭代二十三）**
  - **状态**：Accepted
  - **结论**：**需要结构性但仍保持简单**的架构调整（由两阶段扩展为三阶段），不改变单体分层与主要模块边界
  - **背景**：PRD v4.5 要求将「自动估计」与「最终混音导出」解耦：阶段二产出可编辑“混音参数 JSON”，阶段三仅依赖该 JSON 做最终混音与导出；用户可在最后 mix 前微调每段 crossfade 参数
  - **最小改动方案**：
    - 扩展主流程为三阶段：
      - 阶段二：生成并落盘「混音参数 JSON（MixParamsJSON，可编辑）」，同时保留 TTS 串词音频与转场参数的可追溯字段
      - 阶段三：读取（并校验）用户编辑后的 MixParamsJSON，执行最终时间线混音与导出（`wav` + `mp3`），并保持与 v3.9.1 转场语义一致
    - 保持阶段二/阶段三的契约边界清晰：阶段三不再依赖阶段二内部的不可编辑中间状态；非法编辑值明确报错中止，不静默降级
  - **影响面**：
    - 主要影响 `core/pipeline.py`（增加阶段二/阶段三接口与编排方式）、`cli.py`（新增入口以承载“编辑参数 JSON → 最终导出”）
    - `modules/mixing/mixer.py` 需要支持从 MixParamsJSON 读取并应用用户覆盖的 crossfade 参数（特别是 `intro/vm_candidate` 等）

