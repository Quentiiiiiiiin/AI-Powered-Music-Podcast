## 版本 v5.1（迭代二十六：Developer Console 按三阶段拆分布局）

基于 PRD v5.1：v5.0 Console 已可用，但**单页全量堆叠**导致界面混乱、难定位。本轮只做**信息架构拆分与导航**——同一页面顶部切换「阶段一 / 二 / 三」，下方仅展示当前选中阶段的功能区；**不**深度重设计各阶段内部控件（留给后续迭代）。

**阶段划分（与流水线对齐）**：
1. **阶段一**：计划生成（plan / state / episode snapshot、LLM 相关参数等）
2. **阶段二**：准备与中间产物（snapshot、music_dir、TTS、mix_params、音频相关参数等）
3. **阶段三**：最终混音与导出（mix_params → finalize）

**硬约束**：仍 Gradio + 现有 `pipeline`；不引入 React；不丢失 v5.0 已有能力（改参、Preset、Run、观察面板）。

---

### Task 01 - 三阶段导航骨架（Tabs / 顶部切换）
- **Task name**: v5.1 - Console 三阶段导航骨架
- **目标**: 在 `build_app()` 中用 Gradio Tabs（或等价顶部切换）将主内容区拆为三个阶段页；任一时刻仅展示一个阶段；默认选中阶段一。
- **类型**: frontend
- **依赖关系**: 无
- **Description**:
  - 顶部切换控件文案清晰：阶段一 / 阶段二 / 阶段三（可带简短副标题）。
  - 切换交互稳定：不崩溃、不出现不可恢复空白。
  - 本任务只搭骨架与空分区边界，控件搬迁可在后续 Task 完成。
- **Input**: 当前 `src/podcast_ai/console/app.py` 单页 Accordion 堆叠布局
- **Output**: 三阶段导航可用的骨架 UI
- **Files involved**:
  - `src/podcast_ai/console/app.py`
- **Estimated complexity**: S（1 小时）

---

### Task 02 - 按阶段归位现有控件与 Run（不丢能力）
- **Task name**: v5.1 - 控件与 Run 按阶段归位
- **目标**: 将 v5.0 已有参数、Run 按钮、结果展示按阶段放入对应页，保证三阶段入口均可访问、能力不回退。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - **阶段一**：topic / duration / language / agent_mode / output_dir / LLM 字段；Run Plan；阶段一相关结果（state / snapshot 路径等）。
  - **阶段二**：snapshot_path / music_dir / tts_provider / 音频参数（crossfade、intro 对齐等）；Run Stage 2；mix_params 产物展示。
  - **阶段三**：mix_params_path（可回填）；Run Stage 3；最终音频 / Show Notes / 错误与日志。
  - 「One-shot Create」：可放阶段二底部或独立小入口（不强制单独第四 Tab）；须仍可访问。
  - Preset 保存/加载：可保留页顶公共区（跨阶段共享），或每阶段可见同一公共条——二选一，避免重复三份控件。
  - **禁止**改动 `runner.py` / `pipeline` 业务逻辑（除非仅修接线）。
- **Input**: Task 01 骨架 + 现有 form/click 绑定
- **Output**: 三阶段均能完整完成对应 Run 闭环
- **Files involved**:
  - `src/podcast_ai/console/app.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 03 - 跨阶段字段与结果接线整理（去冗余）
- **Task name**: v5.1 - 跨阶段共享状态与输出整理
- **目标**: 理清跨阶段共享字段（如 `snapshot_path`、`mix_params_path`、`output_dir`）与结果面板，避免重复组件/重复绑定导致状态错乱；切换阶段后关键路径与观察区仍可读。
- **类型**: frontend
- **依赖关系**: Task 02
- **Description**:
  - Stage1 成功后回填 snapshot → Stage2 可用；Stage2 回填 mix_params → Stage3 可用（保持现有 `_ui_pack` 回填语义）。
  - 观察面板：可「每阶段一份精简结果区」或「公共结果区 + 当前阶段摘要」；优先简洁，删除无用重复 Markdown/Textbox。
  - 清理因拆分产生的死代码、重复 `form_inputs` 列表、未绑定按钮。
- **Input**: Task 02 归位后的布局
- **Output**: 共享状态正确、代码更短、无重复堆叠
- **Files involved**:
  - `src/podcast_ai/console/app.py`
- **Estimated complexity**: S（1–2 小时）

---

### Task 04 - 冒烟验收与最小文档同步
- **Task name**: v5.1 - 三阶段导航冒烟 + README 一句说明
- **目标**: 手工/轻量冒烟确认：切换稳定、三阶段能力仍在、默认阶段一；README 补一句「Console 按阶段一/二/三切换展示」。
- **类型**: frontend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - 验收对照 PRD：顶部切换、单阶段可见、能力不丢、无 React、分区边界清晰。
  - 可选：对 `build_app` 做「可 import / 返回 Blocks」的极小 smoke test（不强制起服务器）。
- **Input**: 完成态 Console
- **Output**: 文档与实现一致；冒烟通过
- **Files involved**:
  - `README.md`
  - `src/podcast_ai/console/app.py`
  - （可选）`tests/test_console_app.py`
- **Estimated complexity**: S（0.5–1 小时）
