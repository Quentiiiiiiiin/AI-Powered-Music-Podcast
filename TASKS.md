## 版本 v2.0（迭代五：串词与歌曲边界 crossfade 试验）

基于 PRD 迭代记录：在完成「歌曲 + 主持语音」按顺序排布后，
1）保留相邻歌曲转场的 crossfade（6–10s，不回退）；
2）在 v2.0 增加“串词与相邻歌曲边界”的短时淡入淡出 crossfade（试验版，默认 2–4s，可配置），以改善衔接。

---

### Task 01 - 新增“串词-歌曲边界 crossfade”配置并透传
- **Task name**: `voice_music_crossfade_seconds` 配置字段落地
- **目标**: 为 v2.0 提供可配置的“串词-歌曲边界 crossfade 时长”（默认 2–4 秒），并在渲染链路中能被 `Mixer` 消费；不改变现有歌曲-歌曲 crossfade 的配置来源。
- **类型**: backend
- **Description**: 
  - 在 `core/models.py` 的 `AudioRenderConfig` 增加字段 `voice_music_crossfade_seconds: float`（默认例如 3.0）。
  - 在 `infra/config.py`（及 `config.yaml`/示例配置）新增对应配置项（或直接从现有 audio 节点透传）。
  - 在 `core/pipeline.py` 创建 `AudioRenderConfig` 时填充该字段。
- **Input**:
  - `config.yaml` / 环境变量中的 ElevenLabs/TTS 配置不相关；只需扩展 audio 相关配置
  - 现有 `AudioRenderConfig` 创建逻辑
- **Output**:
  - `AudioRenderConfig` 中可用 `voice_music_crossfade_seconds` 值
  - `Mixer.build_mix(...)` 能读取该参数
- **Files involved**:
  - `src/podcast_ai/core/models.py`
  - `src/podcast_ai/infra/config.py`
  - `src/podcast_ai/core/pipeline.py`
  - `config.yaml` / `config.example.yaml`（若存在）
- **Dependencies**: 无
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - Mixer 在“串词-歌曲边界”应用短时 crossfade
- **Task name**: 边界 fade-in/fade-out（v2.0 试验）
- **目标**: 在保持“串词期间背景音乐默认静音（仅在边界附近重叠 2–4s）”的前提下，让串词与相邻歌曲之间具备平滑过渡：
  - 串词开始附近：音乐淡出 + 语音淡入（重叠 crossfade）
  - 串词结束附近：语音淡出 + 音乐淡入（重叠 crossfade）
  同时保证歌曲-歌曲 crossfade 行为不被替换或回退。
- **类型**: backend
- **Description**:
  - 修改 `modules/mixing/mixer.py` 的最终拼接逻辑：
    - 仍保留“组内歌曲 crossfade”（使用 `config.crossfade_seconds`，不改）
    - 在拼接“音乐段组”与“主持语音段”之间使用 `config.voice_music_crossfade_seconds` 做短时 crossfade（仅发生在这两类段相邻的边界）
  - 确保中段语音不被背景音乐“拖带”（crossfade 只重叠边界时长，其余保持静音逻辑）。
- **Input**:
  - `selected_tracks`（已按 v2.0/前置版本确定顺序）
  - `voiceovers`（按 v1.3/v1.4 规则插入）
  - `AudioRenderConfig.crossfade_seconds`（歌曲-歌曲）
  - `AudioRenderConfig.voice_music_crossfade_seconds`（语音-歌曲边界）
  - `output_path`
- **Output**:
  - 生成的 `mix.wav` 具备串词-歌曲边界的短时 crossfade
  - `MixRenderSummary` 正确返回总时长统计
- **Files involved**:
  - `src/podcast_ai/modules/mixing/mixer.py`
  - （必要时）`src/podcast_ai/core/models.py`（类型字段使用）
- **Dependencies**: Task 01
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - 单测：验证串词-歌曲边界 crossfade 存在且中段不受污染
- **Task name**: v2.0 边界 crossfade 单元测试
- **目标**: 用确定性的音频样本验证：
  1. 串词开始边界附近存在重叠（混音片段能检测到背景音乐能量/变化）
  2. 串词中段（远离边界）背景音乐仍为静音（混音片段与 voice-only 基本一致）
  3. 歌曲-歌曲 crossfade 既有测试不回归
- **类型**: backend
- **Description**:
  - 在 `tests/test_mixing.py` 新增测试用例：
    - 用 `pydub.generators` 生成两段“音乐 tone”和一段“语音 tone”（导出 wav）
    - 构造 `SelectedTrack` 与 `VoiceoverSegment`，使时间线形成为：music1 -> voice -> music2
    - 设置 `voice_music_crossfade_seconds` 为较短值（例如 1.0s）以便断言窗口
    - 断言：混音结果在 voice 中段 dBFS/RMS 与 voice-only（同样归一化）接近；在 voice 边界重叠窗口 dBFS/RMS 与 voice-only 有明显差异
  - 如现有 mixer tests 期望 total duration，需要按新的边界 crossfade 长度调整断言容差。
- **Input**:
  - v2.0 修改后的 `Mixer.build_mix`
  - 可用 FFmpeg 的环境（该测试使用 wav 导出/读取时）
- **Output**:
  - `pytest` 通过
  - 单测覆盖 v2.0 的核心验收点（边界过渡存在，中段不污染）
- **Files involved**:
  - `tests/test_mixing.py`
  - （可能需要）`tests/conftest.py`（如新增 fixture）
- **Dependencies**: Task 02
- **Estimated complexity**: M（2-3 小时）

---

### Task 04 - 集成回归：pipeline 跑通并确保 v1.3 顺序/边界规则不被破坏
- **Task name**: pipeline 集成回归（v1.3 顺序保持）
- **目标**: 确认 v2.0 的边界 crossfade 不改变“串词-段落顺序”和 v1.x 的关键时序语义；至少跑通 `create_episode` 路径与现有关键用例。
- **类型**: backend
- **Description**:
  - 更新/补齐 `tests/test_pipeline.py` 中对 mixing 输出时长或元信息的断言容差（由于边界 crossfade 会让总时长略变）。
  - 保持断言聚焦在顺序与不中断，不引入过度音频级精度要求。
- **Input**:
  - 当前测试用例
  - v2.0 修改后的渲染链路（pipeline -> voiceover -> mixer）
- **Output**:
  - `pytest` 通过（全量）
  - 至少对序列/路径/不中断进行回归验证
- **Files involved**:
  - `tests/test_pipeline.py`
- **Dependencies**: Task 03
- **Estimated complexity**: S（1-2 小时）

