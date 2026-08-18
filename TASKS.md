## 版本 v5.0（迭代二十五：Developer Console — Gradio 本地开发者控制台）

基于 PRD v5.0 与 ARCHITECTURE **AD-v5.0**：交付本机 **Gradio Developer Console**（开发者调试/实验入口，**非正式 C 端前端**）。默认本机浏览器访问（如 `http://localhost:7860`）。

**硬约束**：
- Console **只装配与展示**，业务一律调用现有 `core.pipeline`（`plan_episode` / `create_episode` / `create_episode_stage2` / `finalize_episode_stage3` 等）与 `Settings`；**禁止**在 UI 层重写选曲 / 混音 / TTS。
- **不引入 React** 或独立 Web 产品栈；CLI 必须保留可用。
- 本轮目标是**最大化实验效率**（清晰分区、少干扰），不做视觉复杂度；完整 Experiment 对比可后续迭代。

**本轮能力（验收对齐）**：
1. 可启动 Gradio，端口可配置；启动失败有明确错误。
2. 核心参数可视化编辑 + 参数集**保存/加载**（快照级）+ 触发 Run。
3. Run 后结构化展示 Pipeline 状态、相关阶段产物（路径/可预览）、日志/错误、关键耗时。
4. 复用现有 Backend；CLI 不回退。

---

### Task 01 - Console 脚手架、依赖与启动入口
- **Task name**: v5.0 - Gradio console scaffold + launch
- **目标**: 新增 `src/podcast_ai/console/` 包（建议 `app.py` + `launch`），可本地启动 Gradio；将 `gradio` 加入依赖清单；启动失败（缺依赖、端口占用、导入失败）给出明确可读错误。
- **类型**: frontend
- **依赖关系**: 无
- **Description**:
  - 端口默认 `7860`，可从 CLI 参数或简单配置覆盖。
  - 入口保持薄：组装 Gradio Blocks/Tabs，不含业务逻辑。
  - 在 `pyproject.toml`（及 README）声明依赖与启动方式。
- **Input**: AD-v5.0、现有包布局
- **Output**: `podcast-ai console`（或等价）可打开空白/占位 UI
- **Files involved**:
  - `src/podcast_ai/console/__init__.py`（新建）
  - `src/podcast_ai/console/app.py`（新建）
  - `src/podcast_ai/cli.py`（新增启动子命令）
  - `pyproject.toml`
- **Estimated complexity**: S（1–2 小时）

---

### Task 02 - 核心参数表单（覆盖常用 CLI/config 项）
- **Task name**: v5.0 - 参数可视化编辑区
- **目标**: 在 Console 中提供一组**核心运行参数**的可视化输入/选择（不必穷尽所有 config 字段），足以驱动现有 Pipeline 常用路径。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - 建议最小集（可按实现微调）：
    - 阶段一：`topic`、`duration_minutes`、`language`、`agent_mode`、`output_dir`
    - LLM：`model`、`openrouter_provider`（可空）、可选 `base_url`（只读或可编辑）
    - 阶段二/三：`snapshot/plan` 路径、`music_dir`、`tts_provider`、`mix_params` JSON 路径
    - 音频常用：`crossfade_seconds`、`voice_music_crossfade_seconds`、intro 对齐开关/上限（若已在 Settings）
  - 表单值 → 组装为调用 `pipeline` / `Settings` 的参数；**不**在 Console 内复制校验规则，尽量复用现有模型校验/抛错。
- **Input**: 现有 CLI 参数与 `Settings`
- **Output**: 可编辑表单区块
- **Files involved**:
  - `src/podcast_ai/console/app.py`（或拆 `params_ui.py`，避免过度拆分）
- **Estimated complexity**: M（2–3 小时）

---

### Task 03 - 参数快照：保存 / 加载
- **Task name**: v5.0 - Parameter Snapshot save/load
- **目标**: 支持将**当前参数集**保存为本地 JSON 快照，并加载回表单；为后续 Preset/Experiment 预留清晰命名，本轮不做完整 Experiment 对比 UI。
- **类型**: frontend
- **依赖关系**: Task 02
- **Description**:
  - 快照目录建议：`{output_dir}/console_presets/` 或用户指定路径；文件内容仅为 Console 表单字段（不含密钥明文优先：API Key 可不写入快照或脱敏）。
  - UI：Save / Load（路径选择或下拉最近快照）；非法 JSON 明确报错。
- **Input**: Task 02 表单状态
- **Output**: 可重复的参数集文件
- **Files involved**:
  - `src/podcast_ai/console/`（小模块如 `presets.py` 可选）
- **Estimated complexity**: S（1–2 小时）

---

### Task 04 - Run 动作：接线现有 Pipeline（不复制业务）
- **Task name**: v5.0 - Console Run → pipeline
- **目标**: UI 提供明确 Run 按钮（可按阶段拆分：Plan / Stage2 / Stage3 / 一键 Create），调用现有 `pipeline` API；捕获 `PodcastAIError` 等并回显到 UI。
- **类型**: frontend
- **依赖关系**: Task 02
- **Description**:
  - 推荐最小可用：至少覆盖 **plan-episode** + **create-episode-stage2** + **finalize-episode-stage3**（与 CLI 对齐）；可选保留「一键 create-episode」。
  - Run 期间展示「running」状态；结束后写回结构化结果对象（供 Task 05 展示）。
  - 长任务：Gradio 同步调用即可（本轮不做复杂队列/多 worker）；若阻塞过久，可后续再加进度。
- **Input**: 表单参数 + `core.pipeline`
- **Output**: 可触发的 Run + 结果数据结构
- **Files involved**:
  - `src/podcast_ai/console/app.py`（或 `runner.py`：仅做参数映射与异常包装）
  - （只读）`src/podcast_ai/core/pipeline.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 05 - Pipeline 结构化观察（状态 / 产物 / 日志 / 耗时）
- **Task name**: v5.0 - 结果与阶段状态面板
- **目标**: Run 过程或结束后，UI 结构化展示 Pipeline Status，以及与当前流程相关的 LLM / TTS / Music / Audio / Final Output 状态与产物（文本摘要、音频路径或可播放预览、日志/错误、关键耗时）。
- **类型**: frontend
- **依赖关系**: Task 04
- **Description**:
  - 优先展示：**产物路径**（state / snapshot / mix_params / final mp3）、**错误信息**、**耗时**（可复用现有 `log_timing` 日志抓取，或在 runner 内简单计时包装——**不要**为此大改 pipeline）。
  - 音频：Gradio `Audio` 组件播放本地最终/中间文件（若路径存在）。
  - 文本：展示 Show Notes 片段、关键 JSON 路径链接式文本即可；避免在 UI 内嵌巨型 JSON 编辑器（阶段三微调仍可用外部编辑器打开 mix_params）。
- **Input**: Task 04 运行结果
- **Output**: 清晰分区的观察面板
- **Files involved**:
  - `src/podcast_ai/console/app.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 06 - 文档与冗余约束收尾
- **Task name**: v5.0 - README 启动说明 + 边界声明
- **目标**: 更新 README：如何安装 `gradio`、如何启动 Console、默认 URL/端口；明确「开发者调试工具，非正式产品 UI」；确认 CLI 命令列表未删减。
- **类型**: backend
- **依赖关系**: Task 01, Task 04
- **Description**:
  - 不新增多余文档文件；不在 ARCHITECTURE 外再写长文（除非已有惯例需一行同步）。
  - 清理 Console 内未使用的占位组件/死代码。
- **Input**: 完成态 Console
- **Output**: 文档与实现一致
- **Files involved**:
  - `README.md`
  - `src/podcast_ai/console/`
- **Estimated complexity**: S（0.5–1 小时）
