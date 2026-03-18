## 开发任务拆解（基于 PRD.md + ARCHITECTURE.md）

### 复杂度标尺（Estimated complexity）
- **XS**：0.5 天内可完成，改动面小，风险低
- **S**：1 天左右，可独立交付
- **M**：2–3 天，涉及多个模块或需要反复调参验证
- **L**：4–7 天，算法/集成复杂或对质量要求高

---

## Task 01 - 项目脚手架与依赖管理
- **Task name**: 初始化 Python 包结构与依赖管理
- **Description**: 按 `src/` 布局创建包结构，选择并落地依赖管理方式（`requirements.txt` 或 `pyproject.toml`），提供最小可运行入口。
- **Input**: `PRD.md`、`ARCHITECTURE.md` 的目录结构与技术选型
- **Output**: 可安装/可运行的项目骨架；基础依赖清单；基础包导入可用
- **Files involved**:
  - `pyproject.toml` 或 `requirements.txt`
  - `src/podcast_ai/__init__.py`
  - `src/podcast_ai/cli.py`（占位）
  - `README.md`（后续任务补充内容）
- **Dependencies**: 无
- **Estimated complexity**: XS

## Task 02 - 配置系统（YAML + 环境变量）与示例文件
- **Task name**: 配置加载与校验（config.yaml + .env）
- **Description**: 使用 `pydantic-settings` 设计配置模型，支持从 `config.yaml` 与环境变量读取；产出 `config.example.yaml` 与 `.env.example`。
- **Input**: 配置项（音乐目录、输出目录、LLM/TTS 配置、默认 crossfade、目标 LUFS、缓存开关等）
- **Output**: 运行时可读取的配置对象；示例配置文件可复制即用
- **Files involved**:
  - `config.example.yaml`
  - `.env.example`
  - `src/podcast_ai/infra/config.py`
  - `src/podcast_ai/core/models.py`（配置相关模型若放这里）
- **Dependencies**: Task 01
- **Estimated complexity**: S

## Task 03 - 日志与错误体系
- **Task name**: 统一 logging 配置与异常类型
- **Description**: 提供命令行友好的日志格式、文件日志（可选）、耗时统计辅助；定义核心异常（配置错误、FFmpeg 不可用、AI 调用失败、音频处理失败等）。
- **Input**: PRD 非功能需求（错误提示、日志）
- **Output**: `logging_config` 可复用；异常分层清晰；关键失败信息可定位
- **Files involved**:
  - `src/podcast_ai/core/logging_config.py`
  - `src/podcast_ai/core/exceptions.py`
  - `src/podcast_ai/cli.py`（接入日志）
- **Dependencies**: Task 01
- **Estimated complexity**: S

## Task 04 - 核心数据模型（Pydantic）
- **Task name**: 实现 EpisodeRequest / EpisodePlan / Track 等核心模型
- **Description**: 将架构文档中的数据模型落地为 Pydantic 模型（含序列化/反序列化），作为各模块间契约。
- **Input**: `ARCHITECTURE.md` 第 3 节数据模型
- **Output**: `core/models.py` 定义完成；规划 JSON 可保存/加载；字段校验明确
- **Files involved**:
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 01, Task 02（部分字段引用配置）
- **Estimated complexity**: S

## Task 05 - 路径与存储约定（规划文件/临时文件/输出目录）
- **Task name**: EpisodePlan 持久化与目录组织
- **Description**: 定义 episode_id / plan_id 生成策略；规范规划 JSON、临时混音文件、最终输出文件的命名与落盘位置；提供保存/加载工具函数。
- **Input**: `EpisodePlan`、输出目录约束、两阶段流程需求
- **Output**: 可稳定复现的文件结构；CLI 能引用 plan 文件继续制作
- **Files involved**:
  - `src/podcast_ai/infra/storage/paths.py`
  - `src/podcast_ai/modules/exporter/exporter.py`（输出路径对接）
  - `src/podcast_ai/core/pipeline.py`（保存/加载调用）
- **Dependencies**: Task 04
- **Estimated complexity**: S

## Task 06 - FFmpeg 可用性检测与音频后端封装
- **Task name**: audio_backend（pydub/librosa/ffmpeg）统一封装
- **Description**: 检测 FFmpeg/ffprobe 是否可用；封装常用音频 I/O、时长获取、格式转换、简单归一化、crossfade 拼接、ffmpeg loudnorm 调用等能力。
- **Input**: 系统依赖约束（Windows 优先）、音频处理需求（crossfade、loudness）
- **Output**: 基础音频操作 API；缺失 FFmpeg 时给出明确错误
- **Files involved**:
  - `src/podcast_ai/infra/audio_backend.py`
  - `src/podcast_ai/core/exceptions.py`
- **Dependencies**: Task 01, Task 03
- **Estimated complexity**: M

## Task 07 - LLMClient 抽象与默认实现（含重试/超时/日志）
- **Task name**: 统一封装 LLM 调用
- **Description**: 定义 `LLMClient` 接口与实现（可先做 OpenAI 兼容）；支持超时、重试、错误包装、请求/响应日志脱敏（不打印 key）。
- **Input**: 主题生成模块需要的 prompt 与输出结构（EpisodePlan）
- **Output**: 可调用 LLM 并返回结构化内容；失败可诊断
- **Files involved**:
  - `src/podcast_ai/infra/llm_client.py`
  - `src/podcast_ai/core/exceptions.py`
  - `src/podcast_ai/infra/config.py`（API 配置）
- **Dependencies**: Task 02, Task 03, Task 04
- **Estimated complexity**: M

## Task 08 - TTSClient 抽象与默认实现（含重试/缓存策略）
- **Task name**: 统一封装 TTS 调用
- **Description**: 定义 `TTSClient` 接口与实现（优先 `edge-tts` 或 OpenAI TTS 二选一）；支持失败重试与可选缓存（相同文本避免重复生成）。
- **Input**: VoiceoverSegment 模型、语言选择（zh/en）
- **Output**: 文本→音频文件；可复用与可追踪的生成记录
- **Files involved**:
  - `src/podcast_ai/infra/tts_client.py`
  - `src/podcast_ai/infra/storage/paths.py`（缓存路径）
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 02, Task 04, Task 05
- **Estimated complexity**: M

## Task 09 - 模块 1：主题生成（EpisodePlan + 目标歌单规划）
- **Task name**: ThemePlanner.generate_plan（prompt + 结构化输出）
- **Description**: 设计 prompt 与输出 schema，生成节目结构（segments）、情绪描述、BPM 区间、每段主持串词草稿、每段目标歌单规划（曲目建议/搜索提示）。
- **Input**: `EpisodeRequest(topic, duration, language)`；LLMClient；配置（风格约束、段落数量范围等）
- **Output**: `EpisodePlan`（可序列化为 JSON），包含 `segments[].target_playlist/search_hints`
- **Files involved**:
  - `src/podcast_ai/modules/theme/prompts.py`
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/infra/llm_client.py`
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 04, Task 07
- **Estimated complexity**: M

## Task 10 - 阶段一：规划命令（plan-episode）与可读歌单输出
- **Task name**: CLI 命令 plan-episode + 规划文件落盘
- **Description**: 实现 `podcast-ai plan-episode`：读取配置/参数→调用 ThemePlanner→保存 `EpisodePlan` JSON→输出人类可读的目标歌单（Markdown/TXT）。
- **Input**: CLI 参数（topic、duration、language、output_dir 可选）或配置默认值
- **Output**: `episode_plan_*.json` + `playlist.md`（或类似）+ 控制台摘要
- **Files involved**:
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/core/pipeline.py`（plan_episode）
  - `src/podcast_ai/infra/storage/paths.py`
  - `src/podcast_ai/modules/theme/llm_planner.py`
- **Dependencies**: Task 02, Task 03, Task 05, Task 09
- **Estimated complexity**: S

## Task 11 - 模块 2：本地音乐库扫描与元数据提取（含缓存）
- **Task name**: LibraryScanner.scan_or_load_cache
- **Description**: 扫描用户指定目录下 mp3/wav；提取基础标签与时长；计算/估计 BPM（可选只分析前 N 秒）；缓存结果并支持增量更新。
- **Input**: `music_dir: Path`；缓存配置；audio_backend（时长/BPM 能力）
- **Output**: `list[TrackWithMetadata]`；缓存文件（JSON/SQLite 二选一，MVP 可先 JSON）
- **Files involved**:
  - `src/podcast_ai/modules/library/scanner.py`
  - `src/podcast_ai/infra/storage/cache.py`
  - `src/podcast_ai/infra/audio_backend.py`
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 04, Task 06
- **Estimated complexity**: L

## Task 12 - 模块 3：自动选曲与排序（时长约束 + BPM 连续性）
- **Task name**: TrackSelector.select_tracks
- **Description**: 在候选曲目中结合 `EpisodePlan` 的段落目标时长与 BPM 区间进行过滤/打分；输出按 BPM 递增、相邻差值受控的排序；考虑 crossfade 重叠的有效时长，逼近目标时长 ±5%。
- **Input**: `EpisodePlan`；`list[TrackWithMetadata]`；渲染配置（crossfade_seconds）
- **Output**: `list[SelectedTrack]`（含时间线占位/有效时长）
- **Files involved**:
  - `src/podcast_ai/modules/selection/selector.py`
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 04, Task 11
- **Estimated complexity**: L

## Task 13 - 模块 5：主持串词与语音生成（TTS）
- **Task name**: VoiceoverService.generate_voiceovers
- **Description**: 从 `EpisodePlan.segments[].host_script` 生成对应语音文件；为后续混音提供插入策略（段首/段尾/指定 offset），并输出 `VoiceoverSegment` 列表。
- **Input**: `EpisodePlan`；`TTSClient`；路径与缓存策略
- **Output**: `list[VoiceoverSegment]`（含 audio_path 与 insert_time 策略）
- **Files involved**:
  - `src/podcast_ai/modules/voiceover/tts_service.py`
  - `src/podcast_ai/infra/tts_client.py`
  - `src/podcast_ai/core/models.py`
  - `src/podcast_ai/infra/storage/paths.py`
- **Dependencies**: Task 04, Task 05, Task 08
- **Estimated complexity**: M

## Task 14 - 模块 4：混音与时间线渲染（crossfade + 插入主持）
- **Task name**: Mixer.build_mix（事件时间线 + 音频合成）
- **Description**: 构建“歌曲 + 主持”的统一时间线，按固定 crossfade（6–10s）拼接曲目；将主持语音按策略插入并做基础音量处理，输出中间混音文件。
- **Input**: `list[SelectedTrack]`；`list[VoiceoverSegment]`；`AudioRenderConfig(crossfade_seconds, sample_rate, bitrate...)`
- **Output**: `mix_path: Path`（中间 wav/mp3），以及渲染摘要（实际时长、事件表）
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - `src/podcast_ai/infra/audio_backend.py`
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 06, Task 12, Task 13
- **Estimated complexity**: L

## Task 15 - 模块 6：母带处理（Loudness normalization）
- **Task name**: MasteringService.apply_mastering（ffmpeg loudnorm）
- **Description**: 对混音结果做整体 loudness 标准化（目标 LUFS 配置化），避免削波并保持输出可发布；输出最终音频文件。
- **Input**: `mix_path`；`AudioRenderConfig(loudness_target_lufs, bitrate...)`
- **Output**: `final_audio_path: Path`
- **Files involved**:
  - `src/podcast_ai/modules/mastering/processor.py`
  - `src/podcast_ai/infra/audio_backend.py`
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 06, Task 14
- **Estimated complexity**: M

## Task 16 - 模块 7：导出（MP3 + Show Notes）
- **Task name**: Exporter.export_episode（输出文件与发布信息）
- **Description**: 生成最终 MP3（确保采样率/码率一致），输出 Show Notes（包含主题、段落、曲目清单、时间戳可选）；产出 `EpisodeResult`。
- **Input**: `final_audio_path`；`EpisodePlan`；`list[SelectedTrack]`；输出目录与命名规范
- **Output**: `EpisodeResult(audio_path, show_notes, tracks, duration...)`；落盘的 `show_notes.md`
- **Files involved**:
  - `src/podcast_ai/modules/exporter/exporter.py`
  - `src/podcast_ai/infra/storage/paths.py`
  - `src/podcast_ai/core/models.py`
- **Dependencies**: Task 04, Task 05, Task 15
- **Estimated complexity**: M

## Task 17 - 应用层流水线（两阶段：plan_episode / create_episode）
- **Task name**: Pipeline 编排与进度/耗时统计
- **Description**: 在 `core/pipeline.py` 实现两阶段 API：`plan_episode` 与 `create_episode`；串联 7 模块并统一错误处理、日志、耗时统计；支持从 plan 文件继续。
- **Input**: `EpisodeRequest` 或 `plan_path + music_dir`；配置对象
- **Output**: 阶段一产出 plan 文件；阶段二产出 `EpisodeResult`；控制台输出摘要
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/core/models.py`
  - `src/podcast_ai/core/logging_config.py`
  - `src/podcast_ai/modules/**`
  - `src/podcast_ai/infra/**`
- **Dependencies**: Task 03, Task 04, Task 05, Task 09, Task 11, Task 12, Task 13, Task 14, Task 15, Task 16
- **Estimated complexity**: L

## Task 18 - CLI 命令集（init-config / plan-episode / create-episode / scan-library）
- **Task name**: Typer CLI 落地与帮助信息
- **Description**: 实现架构文档建议的命令：初始化配置、规划、制作、扫库；提供清晰 help、参数校验、错误提示；输出路径可复制。
- **Input**: 用户命令行参数；config.yaml/.env
- **Output**: 可用 CLI；帮助信息；错误提示明确
- **Files involved**:
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/infra/config.py`
- **Dependencies**: Task 02, Task 03, Task 10, Task 17（或至少部分能力）
- **Estimated complexity**: M

## Task 19 - 单元测试：选曲/混音/流水线的关键断言
- **Task name**: 关键模块自动化测试（pytest）
- **Description**: 为核心易回归点加测试：时长计算含 crossfade、选曲排序稳定性、EpisodePlan 序列化、Pipeline 在 mock LLM/TTS 下可跑通。
- **Input**: 核心模块实现；测试用小音频样本（可用生成的短 wav）
- **Output**: `tests/` 下测试通过；CI/本地可一键运行
- **Files involved**:
  - `tests/test_pipeline.py`
  - `tests/test_selection.py`
  - `tests/test_mixing.py`
- **Dependencies**: Task 04, Task 12, Task 14, Task 17
- **Estimated complexity**: M

## Task 20 - 文档与上手体验（Windows 优先）
- **Task name**: README 安装/配置/运行指南（含 FFmpeg）
- **Description**: 补齐安装步骤（Python/venv、依赖安装、FFmpeg 安装与验证）、配置说明、示例命令、常见错误排查；确保 MVP 可自助跑通。
- **Input**: 最终 CLI 与配置项；FFmpeg 检测逻辑与错误信息
- **Output**: `README.md` 可直接指导从零运行；包含示例输出与目录约定
- **Files involved**:
  - `README.md`
  - `config.example.yaml`
  - `.env.example`
- **Dependencies**: Task 06, Task 18
- **Estimated complexity**: S

---

## 建议交付里程碑（MVP）
- **M0（地基可跑）**：Task 01–06
- **M1（规划可用）**：Task 07–10
- **M2（制作打通）**：Task 11–17
- **M3（可用性与稳定性）**：Task 18–20
