## 1. 技术选型

- **编程语言与运行环境**
  - **Python 3.10+**（PRD 已指定，生态成熟、音频与 AI 库丰富）
  - 本地运行，优先支持 **Windows**，兼容 macOS
  - 依赖管理：`venv + pip` 或 `poetry`（根据个人偏好选择其一）
- **核心第三方库（MVP）**
  - **音频处理**
    - `pydub`：音频切片、拼接、音量调整、crossfade 淡入淡出（依赖 FFmpeg，API 简单）
    - `librosa`：BPM、时长和基础音频特征分析
    - 系统依赖：**FFmpeg**（单次安装，作为全局依赖）
  - **元数据读取**
    - `mutagen`：读取 mp3/wav 标签与基础元数据（时长、编码、标题等）
    - 或基于 `ffprobe` 的轻量封装作为备选
  - **LLM**
    - 抽象接口：`LLMClient`
    - 默认实现：接入 OpenAI / Claude / 其它兼容 LLM，模型名称与 API Key 通过环境变量或配置文件注入
  - **TTS**
    - 抽象接口：`TTSClient`
    - 默认实现：`ElevenLabs`（v1.4 主路径）
    - 备选实现（可选，非默认）：OpenAI TTS / Coqui
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
      - 支持 **两阶段流程**：① 规划阶段（输出歌单）→ ② 制作阶段（用户准备好歌曲后继续）
      - 串联 7 个 PRD 定义的功能模块（主题生成 → 扫库 → 选曲 → 混音 → 主持 → 母带 → 导出）
      - 管理整体流程、错误处理、进度日志与时间统计
      - 提供高层 API：`plan_episode`（仅规划）、`create_episode`（完整制作或从规划继续）
  - **领域模块层（Domain / Modules Layer）**
    - 按“功能模块”划分子包，每个模块只关注自己的业务规则与实现：
      - `modules/theme`：主题与节目结构生成（LLM 调用与 prompt 设计）
      - `modules/library`：本地音乐库扫描与元数据提取
      - `modules/selection`：自动选曲与排序算法
      - `modules/mixing`：音轨拼接、crossfade 混音逻辑
      - `modules/voiceover`：主持串词生成与 TTS 语音生成
      - `modules/mastering`：Loudness normalization 与母带处理
      - `modules/exporter`：最终 MP3 导出与 Show Notes 输出
    - 模块之间通过核心数据模型（`core/models.py`）交互，避免相互直接耦合
  - **基础设施层（Infrastructure Layer）**
    - 对外部世界的全部访问集中在此层，提供可替换实现：
      - `infra/llm_client.py`：封装 LLM 调用（统一重试与日志）
      - `infra/tts_client.py`：封装 TTS 调用（默认 ElevenLabs，支持 SDK/HTTP 两种接入路径二选一）
      - `infra/audio_backend.py`：封装 pydub / librosa / ffmpeg 的常用操作
      - `infra/storage/cache.py`：音乐库扫描结果缓存（JSON 或 SQLite）
      - `infra/config.py`：加载配置文件和环境变量
- **主流程（两阶段时序）**
**阶段一：规划与歌单输出（用户可仅执行此阶段）**
  1. 用户通过 CLI 输入主题和时长 → 解析为 `EpisodeRequest`
  2. `Pipeline.plan_episode(request)`：
    - 调用 `ThemePlanner.generate_plan`：生成节目结构、段落、情绪、BPM 区间、串词草稿、**目标歌单规划** → `EpisodePlan`
    - 将 `EpisodePlan` 持久化为 JSON 文件，并输出可读的「目标歌单」供用户查阅
  3. 用户根据目标歌单到各平台搜索、下载或整理歌曲，放入指定本地目录
  **阶段二：制作（用户准备好歌曲后执行）**
  1. 用户再次运行 CLI，指定规划文件（或 episode_id）与音乐目录
  2. `Pipeline.create_episode(plan_path, music_dir)` 或 `create_episode(request, plan=...)`：
    - 加载已有 `EpisodePlan`（或可选：重新生成）
    - 调用 `LibraryScanner.scan_or_load_cache`：扫描用户准备好的目录 → `list[TrackWithMetadata]`
    - 调用 `TrackSelector.select_tracks`：在已扫描歌曲中，**结合目标歌单规划**与节目结构选曲、排序 → `list[SelectedTrack]`
    - 调用 `VoiceoverService.generate_voiceovers`：生成主持语音片段 → `list[VoiceoverSegment]`
    - 调用 `Mixer.build_mix`：拼接歌曲与主持语音，做 crossfade → 中间混音结果
    - 调用 `MasteringService.apply_mastering`：统一 Loudness → 最终音频
    - 调用 `Exporter.export_episode`：输出 MP3 与 Show Notes → `EpisodeResult`
  3. CLI 输出：文件路径、实际时长、选曲列表等摘要信息

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

---

## 4. API 设计

当前阶段 API 以 **内部 Python 接口 + CLI 命令** 为主，非网络服务，便于本地个人使用。

- **应用层高阶 API**
  - 文件：`core/pipeline.py`
  - 主要函数：
    - `plan_episode(request: EpisodeRequest) -> EpisodePlan`
      - **阶段一**：仅生成节目结构、串词草稿与**目标歌单规划**，输出为 JSON 文件；用户据此手动下载歌曲
    - `create_episode(plan_path: Path, music_dir: Path, ...) -> EpisodeResult`
      - **阶段二**：加载已有规划，从扫描用户准备好的音乐目录开始，执行选曲 → 混音 → 主持 → 母带 → 导出
    - `create_episode(request: EpisodeRequest, plan: EpisodePlan | None = None) -> EpisodeResult`
      - 可选：若传入 `plan` 且用户已准备好歌曲，可跳过阶段一直接执行阶段二
- **领域模块接口（示例）**
  - `ThemePlanner`（模块 1：主题生成）
    - `generate_plan(request: EpisodeRequest) -> EpisodePlan`
    - 输出包含：节目结构、段落、情绪、BPM 区间、串词草稿、**目标歌单规划**（每段推荐曲目或搜索条件）
  - `LibraryScanner`（模块 2：音乐库扫描）
    - `scan_library(root_dir: Path) -> list[TrackWithMetadata]`
    - `scan_or_load_cache(root_dir: Path) -> list[TrackWithMetadata]`
    - 默认假设用户已根据目标歌单规划将本期候选歌曲放入指定目录
  - `TrackSelector`（模块 3：自动选曲与排序）
    - `select_tracks(plan: EpisodePlan, library: list[TrackWithMetadata]) -> list[SelectedTrack]`
    - 在已扫描到的候选歌曲中，**结合目标歌单规划**与节目结构进行选择：BPM 接近、曲风一致、总时长匹配
  - `Mixer`（模块 4：自动混音）
    - `build_mix(selected_tracks: list[SelectedTrack], voiceovers: list[VoiceoverSegment], config: AudioRenderConfig) -> Path`
  - `VoiceoverService`（模块 5：主持语音）
    - `generate_voiceovers(plan: EpisodePlan) -> list[VoiceoverSegment]`
    - 默认使用 ElevenLabs 生成主持语音；若调用失败，返回清晰错误信息（不静默失败）
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
        tts_client.py       # TTSClient 抽象 + 默认实现（ElevenLabs）
        audio_backend.py    # 对 pydub / librosa / ffmpeg 的统一封装
        storage/
          cache.py          # 音乐库扫描缓存（JSON/SQLite）
          paths.py          # 输出与临时文件目录管理；规划文件（EpisodePlan JSON）的保存与加载路径

      modules/
        theme/
          llm_planner.py
          prompts.py
        library/
          scanner.py        # 扫描目录、提取元数据
        selection/
          selector.py       # 选曲与排序策略
        mixing/
          mixer.py          # crossfade、音轨合成
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
- **2）选曲与总时长控制**
  - 难点：在保证 BPM/风格连续性的前提下，使总时长落在目标值 ±5% 区间内。
  - 方案：
    - 将问题视作简单的“带约束的背包问题”，采用启发式贪心算法：
      - 先按 BPM / 风格过滤候选，再按时长与差值进行排序与微调。
    - 在模型中显式记录 crossfade 重叠时间，计算“有效节目时长”时扣除重叠部分。
- **3）Crossfade 混音与音量一致性**
  - 难点：不同来源的音轨音量差异大，直接拼接容易出现忽大忽小或削波。
  - 方案：
    - 在混音前，对每首歌做一次粗略的音量归一化（RMS 或简单 LUFS 估计）。
    - 使用 pydub 的 `fade_in` / `fade_out` 和自定义 crossfade 秒数，实现固定 6–10 秒的过渡。
    - 在最终导出前，调用 `MasteringService` 使用 ffmpeg 的 `loudnorm` 或类似方案进行整体 Loudness normalization。
- **4）主持语音插入的时序规划**
  - 难点：需要避免主持语音压住歌曲关键段落，同时保证节目节奏自然。
  - 方案：
    - 在 `EpisodePlan` 中为每个 `EpisodeSegment` 约定插入策略（如段首、段尾或中间某个时间点）。
    - 在混音阶段，使用统一的时间线（以秒为单位）管理所有事件（歌曲开始/结束、主持插入），避免写死 offset。
- **5）LLM 与 TTS 调用的可靠性与成本**
  - 难点：网络调用存在失败与超时风险，同时需要控制 token 与调用次数。
  - 方案：
    - 在 `LLMClient` / `TTSClient` 中内建重试、超时与基础日志机制。
    - 将模型名称、最大字数/时长、语言等参数配置化，方便按需调优成本与效果。
    - 通过 prompt 约束串词长度与风格，降低无效生成。

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
- **6）跨平台依赖安装（尤其是 FFmpeg）**
  - 难点：Windows 与 macOS 上 FFmpeg 安装方式与路径各异，易导致运行时错误。
  - 方案：
    - 在 README 中提供面向 Windows/macOS 的简明安装步骤与验证命令。
    - 应用启动或首次音频操作前检查 FFmpeg 是否可用，若不可用则给出明确错误提示与参考链接。
    - 所有对 FFmpeg 的调用统一通过 `infra/audio_backend.py`，避免在各模块中散落命令调用。

