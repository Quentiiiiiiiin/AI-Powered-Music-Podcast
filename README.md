# podcast-ai

AI 驱动的音乐 Podcast 自动生成工具（MVP）。根据主题与时长生成节目规划与目标歌单，在用户准备好歌曲后自动完成选曲、混音、主持 TTS、母带与导出。

---

## 环境要求

- **Python 3.10+**
- **FFmpeg**（音频处理必需，需加入系统 PATH）
- 支持：Windows、macOS

---

## 安装步骤

### 1. 创建虚拟环境（推荐）

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -e .
```

### 3. 安装 FFmpeg（Windows）

1. 下载：[https://ffmpeg.org/download.html](https://ffmpeg.org/download.html) 或使用 `winget install ffmpeg`
2. 将 `ffmpeg/bin` 所在目录添加到系统环境变量 `PATH`
3. 验证：

```powershell
ffmpeg -version
ffprobe -version
```

---

## 快速开始

### 初始化配置

```bash
podcast-ai init-config
```

生成 `config.yaml` 与 `.env.example`。将 `.env.example` 复制为 `.env`，填入 LLM / TTS API Key。

### 配置 LLM 与 TTS

在 `.env` 中（或通过环境变量）：

```
PODCAST_AI_LLM__API_KEY=sk-xxx
PODCAST_AI_LLM__BASE_URL=https://api.openai.com/v1
PODCAST_AI_LLM__MODEL=gpt-4o-mini

# v4.6：经 OpenRouter 时可选供应方路由（仅当 base_url 为 OpenRouter 时写入请求体；留空=自动选路）
# PODCAST_AI_LLM__OPENROUTER_PROVIDER=anthropic
# 或 JSON：PODCAST_AI_LLM__OPENROUTER_PROVIDER={"order":["openai","anthropic"]}

# v4.1：TTS 支持 edge / elevenlabs / minimax
PODCAST_AI_TTS__PROVIDER=elevenlabs
PODCAST_AI_TTS__ELEVENLABS__API_KEY=your_elevenlabs_api_key_here
PODCAST_AI_TTS__ELEVENLABS__VOICE_ID=your_elevenlabs_voice_id_here

# MiniMax（同步非流式）
# PODCAST_AI_TTS__PROVIDER=minimax
# PODCAST_AI_TTS__MINIMAX__API_KEY=your_minimax_api_key_here
# PODCAST_AI_TTS__MINIMAX__MODEL=speech-2.8-hd
# PODCAST_AI_TTS__MINIMAX__VOICE_ID=Chinese (Mandarin)_Crisp_Girl
```

---

## 三阶段流程（v4.5）

### 阶段一：规划（生成目标歌单）

```bash
podcast-ai plan-episode "Chill and Relax R&B from 1950s till now" 60 --agent-mode multi_agent -l en
```

- 调用 LLM 生成节目结构与目标歌单规划
- 输出：
  - `output/episodes/<episode_id>/plans/state.json`（与 `state_schema.json` 同结构的统一状态）
  - `output/episodes/<episode_id>/plans/<episode_id>.json`（阶段一 state 快照，文件名与 `episode_id` 一致）

**v3.6（multi-agent）可审计落盘：** 在 `计划 output_dir` 下额外写入 `audit/multi_agent/<request_id>/`。其中 `iteration{i}` 与编排器本轮外层层级一致；`iteration{i}_{planner|music_curator|script_writer|critic}.json` 含该步原始 LLM 文本与解析后的 patch，`iteration{i}_state.json` 为该行结束后的完整 `PlanState`。写盘失败只记日志，不影响规划成功/失败判定。可在配置中关闭 `app.multi_agent_audit_enabled`。

根据歌单到各平台搜索、下载歌曲，放入指定目录（如 `./music/本期节目`）。

**plan-episode 示例输出：**

```
规划完成：
- Episode ID: ep_20250309T120000Z_abc12345
- Plan ID（内存标识，未单独落盘）：ep_xxx_plan_yyyyyy
- state.json（统一状态）：output/episodes/ep_xxx/plans/state.json
- ep_xxx.json（阶段一快照）：output/episodes/ep_xxx/plans/ep_xxx.json
```

### 阶段二：生成可编辑混音参数（MixParamsJSON）

```bash
# 阶段二读取阶段一产物 `<episode_id>.json`
podcast-ai create-episode-stage2 output/episodes/ep_xxx/plans/ep_xxx.json D:/Music/本期节目
# 指定 TTS 供应商（覆盖配置）
podcast-ai create-episode-stage2 output/episodes/ep_xxx/plans/ep_xxx.json D:/Music/本期节目 --tts-provider minimax
```

- 扫描音乐库 → 选曲 → 主持 TTS → 计算转场参数
- 产出并落盘可编辑 JSON：`output/episodes/<episode_id>/mix_params/<episode_id>_mix_params.json`
- 该 JSON 含 `tracks`、`voiceovers`、`transitions`（如 `vm_seconds`、`vm_candidate_seconds`、`intro_seconds`），可人工微调后再进入阶段三

**create-episode-stage2 示例输出：**

```
阶段二完成：
- MixParamsJSON：output/episodes/ep_xxx/mix_params/ep_xxx_mix_params.json
```

### 阶段三：读取编辑后的 MixParamsJSON 并导出最终音频

```bash
podcast-ai finalize-episode-stage3 output/episodes/ep_xxx/mix_params/ep_xxx_mix_params.json
```

- 阶段三会严格校验 JSON（非法数值/边界映射不一致会直接失败，不静默回退）
- 成功后导出：
  - `output/episodes/<episode_id>/mix/mix.wav`
  - `output/episodes/<episode_id>/final/<episode_id>.mp3`
  - `output/episodes/<episode_id>/final/<episode_id>_show_notes.md`

### 一键模式（兼容旧用法）

```bash
podcast-ai create-episode output/episodes/ep_xxx/plans/ep_xxx.json D:/Music/本期节目
```

- `create-episode` 仍保留，等价于“阶段二 + 阶段三”一键执行。
- 适合不需要人工编辑 `mix_params` 的场景。

### 其他命令

```bash
podcast-ai scan-library D:/Music                               # 扫描音乐库并缓存元数据
podcast-ai init-config --force                                 # 强制覆盖配置文件
podcast-ai plan-episode "主题" 30 -l en                        # 英文规划
podcast-ai plan-episode "主题" 30 --agent-mode single_agent    # 单 agent 生成（输出 state 子集）
podcast-ai console                                             # 启动 Developer Console（v5.0）
```

---

## Developer Console（v5.0）

本机 **开发者调试控制台**（非正式创作者产品 UI）。用浏览器改参、分阶段 Run、查看产物/日志；**复用现有 pipeline**，不替代 CLI。

```bash
pip install -e .                 # 已包含 gradio
podcast-ai console               # 默认 http://127.0.0.1:7860
podcast-ai console --port 7861   # 端口占用时换端口
podcast-ai console --no-browser  # 不自动打开浏览器
```

启动失败（未安装 gradio、端口占用、导入错误）会打印明确错误。API Key 仍只放在 `.env`，参数快照（`{output_dir}/console_presets/`）不写入密钥。Console 按 **阶段一 / 阶段二 / 阶段三** 顶部切换展示功能区（Preset 与观察面板跨阶段共用）。

阶段三微调 mix_params 请用外部编辑器改 JSON，再在 Console 里填路径并 Run Stage 3。

## 配置说明


| 配置项                          | 说明                  | 默认值          |
| ---------------------------- | ------------------- | ------------ |
| `app.music_dir`              | 音乐目录                | `./music`    |
| `app.output_dir`             | 输出目录                | `./output`   |
| `audio.crossfade_seconds`    | 曲目过渡时长（秒）           | `8.0`        |
| `audio.voice_music_crossfade_seconds` | 串词->歌基础叠化时长下限（秒） | `3.0` |
| `audio.voice_music_intro_align_enabled` | 是否启用串词->下一首 intro 对齐（v4.2） | `true` |
| `audio.voice_music_intro_align_max_seconds` | 串词->歌动态叠化上限（秒，v4.2） | `3.0` |
| `audio.loudness_target_lufs` | 母带响度目标（LUFS）        | `-14.0`      |
| `llm.base_url`               | LLM API 地址          | 需配置          |
| `llm.api_key`                | LLM API Key         | 建议用环境变量      |
| `llm.model`                  | 模型 id               | 见 `init-config` 默认 |
| `llm.openrouter_provider`    | **v4.6** 经 OpenRouter 时的供应方路由：留空则请求体**不**带 `provider`（自动选路）；非空为单个 slug（实现为 `{"only":[slug]}`）或与官方一致的 JSON 对象字符串 | `""` |
| `llm.structured_output`      | **v3.4** 是否附带 `response_format`（`null`/省略时仅当 `base_url` 为 OpenRouter 自动开启） | 省略（即 `null`） |
| `tts.provider`               | TTS 供应商（`edge`/`elevenlabs`/`minimax`） | `elevenlabs` |
| `tts.elevenlabs.voice_id`    | ElevenLabs Voice ID | 需配置          |
| `tts.minimax.voice_id`       | MiniMax Voice ID | `male-qn-qingse` |


---

## 输出目录约定

```
output/
  episodes/
    ep_YYYYMMDDTHHMMSSZ_xxxxxxxx/
      plans/           # 阶段一统一规划目录
        state.json     # 与 state_schema.json 同结构的统一状态
        <episode_id>.json  # 与 state 内容一致的快照（文件名 = episode_id）
      mix_params/      # 阶段二输出：可编辑混音参数 JSON
        <episode_id>_mix_params.json
      mix/             # 中间混音 wav
      final/           # 最终 MP3、Show Notes
```

---

## 日志与调试

运行时可通过以下方式控制日志，便于排查 Bug：

### 命令行参数

```bash
# 输出 DEBUG 级别日志（更详细，含 LLM/TTS 调用、BPM 分析、缓存命中等）
podcast-ai --log-level DEBUG plan-episode "主题" 30
podcast-ai --log-level DEBUG create-episode plans/ep_xxx.json ./music

# 将日志同时写入文件（追加，UTF-8）
podcast-ai --log-level DEBUG --log-file ./logs/podcast.log plan-episode "The Weeknd Songs" 60 --agent-mode multi_agent -l en
```

### 环境变量

```bash
# 持久生效（适合长期调试）
set PODCAST_AI_LOG_LEVEL=DEBUG
set PODCAST_AI_LOG_FILE=./logs/podcast.log
podcast-ai plan-episode "主题" 30
```

或在 `.env` 中增加：

```
PODCAST_AI_LOG_LEVEL=DEBUG
PODCAST_AI_LOG_FILE=./logs/podcast.log
```

### 日志等级说明


| 等级        | 适用场景                                         |
| --------- | -------------------------------------------- |
| `DEBUG`   | 调试：LLM 请求/响应、TTS 缓存命中、FFmpeg 命令、BPM 估计、选曲详情等 |
| `INFO`    | 默认：阶段进度、耗时统计、扫描/选曲数量等                        |
| `WARNING` | 异常但不中断：单段 TTS 失败、LLM 重试等                     |
| `ERROR`   | 严重错误：FFmpeg 失败、依赖缺失等                         |


---

## 常见错误排查


| 错误                            | 可能原因                                            | 处理                                                                                         |
| ----------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `未检测到可用的 FFmpeg/ffprobe`      | FFmpeg 未安装或未加入 PATH                             | 安装 FFmpeg，将 `ffmpeg/bin` 加入 PATH                                                           |
| `LLM base_url 未配置`            | 未配置 `llm.base_url` 或 `PODCAST_AI_LLM__BASE_URL` | 在 `config.yaml` 或 `.env` 中配置                                                               |
| `ElevenLabs TTS 配置不完整`        | 未配置 `tts.elevenlabs.api_key` 或 `voice_id`       | 在 `.env` 设置 `PODCAST_AI_TTS__ELEVENLABS__API_KEY` 与 `PODCAST_AI_TTS__ELEVENLABS__VOICE_ID` |
| `MiniMax TTS 配置不完整`        | 未配置 `tts.minimax.api_key`       | 在 `.env` 设置 `PODCAST_AI_TTS__MINIMAX__API_KEY`（可选设置 `VOICE_ID`） |
| `ElevenLabs TTS 调用失败：鉴权失败`    | API Key 无效或过期                                   | 检查 ElevenLabs key 是否正确                                                                     |
| `ElevenLabs TTS 调用失败：资源不存在`   | `voice_id` 不正确                                  | 到 ElevenLabs 控制台复制正确 Voice ID                                                              |
| `ElevenLabs TTS 调用失败：配额或频率受限` | 配额不足或触发限流                                       | 稍后重试或提升套餐                                                                                  |
| `音乐目录为空或扫描失败`                 | 目录不存在或无 mp3/wav                                 | 检查路径，确保有音频文件                                                                               |
| `选曲结果为空`                      | 曲库中无符合 BPM 区间的曲目                                | 放宽规划中的 BPM 范围或准备更多曲目                                                                       |
| `读取或校验 mix_params_json 失败` | JSON 文件不存在、格式错误、字段缺失 | 确认使用阶段二输出的 JSON，或按 schema 修正编辑内容 |
| `stage3 渲染失败` | `vm_seconds` 非法（负数、超上限、与边界映射不一致） | 按 `transitions` 逐条修正：保证 `voice_segment_id` 与 `next_music_first_track_file_path` 映射一致，`vm_seconds` 合法 |


---

## 运行测试

```bash
pip install ".[test]"
pytest tests/ -v
```

---

## 项目结构

```
src/podcast_ai/
  cli.py           # 命令行入口（含 console 子命令）
  console/         # Gradio Developer Console（只装配/展示，调用 pipeline）
  core/            # 流水线、模型、日志、异常
  infra/           # 配置、LLM/TTS 客户端、音频后端、存储
  modules/         # 主题生成、扫库、选曲、混音、主持、母带、导出
```

详见 `ARCHITECTURE.md`、`PRD.md`。