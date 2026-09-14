## 版本 v6.2（迭代三十一：Developer Console 参数面板扩展）

基于 PRD **v6.2**：在 Developer Console 补齐可视化配置——混音音频参数（中文说明）、`orchestration_mode` 与 `agent_mode` 关联、LLM/TTS 级联下拉（可选手输）。选项清单在**代码层**维护，不追求完整配置中心。

**硬约束**：
- 不破坏既有三阶段 Tabs / Run 主路径；密钥仍来自 `.env` / `config.yaml`（Preset 不存 Key）。
- Console **只映射到 Settings / pipeline**，不复制混音/TTS/编排业务逻辑。
- 本轮不引入 React；不强制持久写回 `config.yaml`（以「本次 Run 生效 + Preset 可保存」为主即可）。

---

### Task 01 - 代码维护的 LLM / TTS 选项目录
- **Task name**: v6.2 - console option catalogs
- **目标**: 在 `console/` 内用简洁常量表维护「接口→base_url」「模型→供应商候选」「TTS provider→model/voice 候选」，供 UI 级联读取；改表无需改业务模块。
- **类型**: frontend
- **依赖关系**: 无
- **Description**:
  - **LLM**：接口本轮仅 `openrouter` → 固定展示对应 `base_url`（与现网默认一致）；模型预设含 PRD 示例（如 `deepseek/deepseek-v3.2`、`openai/gpt-5.6-luna`、`deepseek/deepseek-v4-pro` 等）；`openrouter_provider` 按模型给出候选（可空列表；GPT 类可含 `azure/eu` 等），允许空串与自定义。
  - **TTS**：ElevenLabs 模型 `eleven_v3` / `eleven_multilingual_v2`，voice 含 `Fc5CaIGWKvLHapoOSM2K`；MiniMax 模型 `speech-2.8-hd`，voice 含 `Chinese (Mandarin)_Crisp_Girl`、`English_Sharp_Commentator`；`edge` 可仅保留 provider、model/voice 留空或沿用 `tts.voice`。
  - 实现建议：新建 `console/option_catalogs.py`（或等价），纯数据 + 小函数 `providers_for_model` / `models_for_tts` / `voices_for_tts`，无 I/O。
- **Input**: PRD v6.2 示例清单、现有 Settings 默认值
- **Output**: 可被 Gradio 引用的选项 API
- **Files involved**:
  - `src/podcast_ai/console/option_catalogs.py`（新建）
- **Estimated complexity**: S（1 小时）

---

### Task 02 - 音频四参数：表单 + Settings 映射 + Preset
- **Task name**: v6.2 - audio normalize/gain/overlay 控件
- **目标**: Console（建议阶段二混音区 Accordion）暴露并中文标注：`per_track_normalize_enabled`、`voice_gain_db`、`voice_music_overlay_music_max_db`、`voice_music_post_overlay_ramp_seconds`；改参后经 `settings_from_params` 作用于 Stage2/3/one-shot；纳入 Preset 键。
- **类型**: frontend
- **依赖关系**: 无（可与 Task 01 并行）
- **Description**:
  - 扩展 `ConsoleParams` / `defaults_from_settings` / `settings_from_params` 写入 `Settings.audio` 对应字段。
  - UI 标签用中文释义（对齐 `config.example.yaml` 注释语义）；保留现有 crossfade / intro_align 控件，避免重复造参。
  - 更新 `PRESET_KEYS` 与 save/load 控件列表，保证快照往返不断键。
- **Input**: 既有 `audio` Settings、阶段二表单
- **Output**: 四项可配且本次 Run 生效
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/runner.py`
  - `src/podcast_ai/console/presets.py`
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 03 - `orchestration_mode` 可选 + 与 `agent_mode` 关联
- **Task name**: v6.2 - orch mode UI coupling
- **目标**: 阶段一将只读 Markdown 改为可切换 `staged` / `legacy`；`single_agent` 时强制 `legacy` 且控件禁用（灰色）；`multi_agent` 可自由切换；值写入本次 Run 的 Settings。
- **类型**: frontend
- **依赖关系**: 无
- **Description**:
  - `ConsoleParams` 增加 `orchestration_mode`；`settings_from_params` 写入 `app.orchestration_mode`。
  - Gradio：`agent_mode.change` → 更新 orch 控件 `interactive` / `value=legacy`。
  - Run 前再做一次防御：若 `agent_mode==single_agent` 仍提交了 `staged`，强制改 `legacy` 或明确报错（二选一，推荐静默纠正并在 insight 展示实际 mode）。
  - Preset 纳入该字段。
- **Input**: v6.0 orchestration 契约、现有阶段一 UI
- **Output**: 关联行为符合 PRD AC2
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/runner.py`
  - `src/podcast_ai/console/presets.py`
- **Estimated complexity**: S（1–1.5 小时）

---

### Task 04 - LLM：接口 / 模型 / 供应商级联下拉
- **Task name**: v6.2 - LLM cascading dropdowns
- **目标**: 阶段一 LLM 区改为：接口下拉（本轮仅 openrouter）→ 只读展示 base_url（无需手填）；模型 Dropdown（`allow_custom_value`）；供应商 Dropdown（可空 + 可选手输），选项随模型联动。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - 去掉「必填手输 base_url」路径：选中接口后自动填/展示 catalog 中的 base_url；高级手改非本轮重点（可不提供或收进 Accordion）。
  - `model.change` 刷新 provider choices；保留空供应商 = OpenRouter 自动路由。
  - 缺模型等必填时 Run 前明确提示；非法接口提示清晰。
- **Input**: Task 01 catalog
- **Output**: 下拉为主、可选手输的 LLM 选配
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/runner.py`（沿用 llm 字段映射即可）
- **Estimated complexity**: M（2 小时）

---

### Task 05 - TTS：model / voice_id 选择并写入 Settings
- **Task name**: v6.2 - TTS model & voice controls
- **目标**: 阶段二在 `tts_provider` 外增加 model、voice_id 下拉（可选手输）；随 provider 联动候选；覆盖写入 `tts.elevenlabs` / `tts.minimax`（或等价）供本次 Stage2/Create 使用。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - 扩展 `ConsoleParams`：`tts_model`、`tts_voice_id`（命名以实现为准）。
  - `settings_from_params` / runner：在选定 provider 时 patch 对应子配置的 `model`/`voice_id`；`default` 不覆盖 config。
  - `edge`：可不强制 model/voice UI，或仅提示使用 config `tts.voice`。
  - Preset 纳入新键；密钥仍不进 Preset。
- **Input**: Task 01、现有 `_tts_override`
- **Output**: 可选预设 + 可手输未列出值；Run 使用覆盖后的 TTS 配置
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/runner.py`
  - `src/podcast_ai/console/presets.py`
- **Estimated complexity**: M（2 小时）

---

### Task 06 - 校验提示与最小回归（可选单测）
- **Task name**: v6.2 - validation + smoke
- **目标**: 非法组合/缺必填有明确中文或可读错误；核心映射（audio 四字段、orch 纠正、TTS patch）有轻量单测或手工验收清单；三阶段布局与既有 Run 不回归。
- **类型**: backend
- **依赖关系**: Task 02, Task 03, Task 04, Task 05
- **Description**:
  - 优先测 `settings_from_params` / catalog 纯函数，不启 Gradio。
  - 不强制 E2E 浏览器自动化。
- **Input**: Task 02–05 完成物
- **Output**: 映射行为可重复验证
- **Files involved**:
  - `tests/test_console_params_v62.py`（新建，建议）
- **Estimated complexity**: S（1 小时）
