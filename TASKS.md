## 版本 v7.1（迭代三十九：Console 调试可观测性增强 + 联网搜索控件）

基于 PRD **v7.1**：补齐 Console 与 CLI `--log-level DEBUG` 对等的排查能力；修复 Agent **解析失败时审计无原文**的缺口；在运行态洞察展示 `web_search_requests`；Console 暴露 `engine` / `max_results` / `max_uses`（默认 `auto` / `5` / 不限制）。

**硬约束**：
- 不破坏 v7.0 联网搜索主路径、结构化输出、snapshot / 终态 `state` 契约。
- Console 仍只装配 Settings / pipeline，不复制 Agent 业务逻辑。
- 避免过度设计：DEBUG 用现有 logging；用量优先复用日志/轻量回传，不另造监控子系统。

---

### Task 01 - Console DEBUG 全量日志 + 详细报错
- **Task name**: v7.1 - Console DEBUG log level
- **目标**: Console 提供 DEBUG（或等价）控件；开启后 plan / stage2 / stage3 / create 等 Run 可在日志区看到 DEBUG 级全量日志；失败时错误展示比默认更详细（含异常链/关键上下文，以实现简洁为准）。
- **类型**: frontend
- **依赖关系**: 无
- **Description**:
  - 现状：`runner._run_command` 将 root logger 提到 INFO 并缓冲 handler 行。
  - 扩展：`ConsoleParams` 增加 `log_level`/`debug`；Run 期间 `root.setLevel(DEBUG)`（结束后恢复）；日志框展示缓冲全文。
  - 错误路径：DEBUG 下 `error`/`summary` 可附 `repr(exc)` 或 `traceback` 末段（注意勿泄露 api_key）。
  - 覆盖所有经 `_run_command` 的入口；Preset 可纳入该字段（可选）。
- **Input**: 现有 `console/runner.py`、`app.py`
- **Output**: 与 CLI DEBUG 相当的 Console 日志可观测性
- **Files involved**:
  - `src/podcast_ai/console/runner.py`
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/presets.py`（可选）
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 02 - 解析失败仍落盘 Agent 原始返回
- **Task name**: v7.1 - audit raw on parse failure
- **目标**: 任一 Agent（Planner / Curator / Writer / Critic，含 staged）在 JSON/结构化解析失败时，审计目录仍写出该次 **原始返回**（与既有 `write_agent_artifact` 约定对齐）；不得仅有错误摘要而无原文。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 现状：多数 Agent 在 `parse_agent_json_response` **成功后**才 `write_agent_artifact`；解析抛错则跳过。
  - 改法（选简单一种并统一四 Agent）：
    1. `try/except`：失败时仍调用 audit，`parsed_patch=None` 或带 `parse_error`；或
    2. 在 `parse_agent_json_response` 接受可选 `on_raw`/`audit` 回调——**优先方案 1**，少改公共解析器签名。
  - `parsed_patch` 缺失时文件名/内容仍可复查 `raw_llm_text`；写盘失败不掩盖原解析错误。
  - 单测：mock 非法 raw + FilePlanAuditSink 临时目录，断言文件存在且含原文片段。
- **Input**: 各 `*_agent.py`、`plan_audit.write_agent_artifact`
- **Output**: 解析失败可对照审计原文
- **Files involved**:
  - `src/podcast_ai/modules/theme/planner_agent.py`
  - `src/podcast_ai/modules/theme/music_curator_agent.py`
  - `src/podcast_ai/modules/theme/script_writer_agent.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/plan_audit.py`（若需允许 parsed 为空）
  - `tests/test_audit_parse_failure_v71.py`（新建）
- **Estimated complexity**: M（2 小时）

---

### Task 03 - 运行态洞察展示 `web_search_requests`
- **Task name**: v7.1 - insight web_search_requests
- **目标**: 阶段一运行态洞察展示当前/最近一次 Agent 相关的 `web_search_requests`；有用量显示次数，未启用或无字段时显示明确占位（如 `0` / `N/A` / `—`），不静默空白。
- **类型**: frontend
- **依赖关系**: 无（可与 Task 04 并行；若需更准的「当前 Agent」次数，可轻触 llm_client 日志格式）
- **Description**:
  - v7.0 已有日志：`LLM usage server_tool_use.web_search_requests=%d`。
  - 优先：在 `run_progress.py` 从缓冲日志解析最近次数 → 写入 `ConsoleRunResult` → `_insight_md` 展示。
  - 可选增强：`LLMClient` 记录 `last_web_search_requests` 并在 DEBUG/INFO 用固定前缀日志，便于解析（仍不改 Agent I/O）。
  - 流式 progress yield 时尽量更新该字段（有则更新，无则占位）。
- **Input**: 现有 insight / run_progress / llm 用量日志
- **Output**: 洞察区可见搜索次数或占位
- **Files involved**:
  - `src/podcast_ai/console/run_progress.py`
  - `src/podcast_ai/console/runner.py`
  - `src/podcast_ai/console/app.py`
  - （可选）`src/podcast_ai/infra/llm_client.py`
- **Estimated complexity**: S–M（1–2 小时）

---

### Task 04 - Console 联网搜索参数：engine / max_results / max_uses
- **Task name**: v7.1 - Console web_search params
- **目标**: 在已有「联网搜索开关」旁增加：`engine` 下拉（`auto`/`native`/`exa`/`firecrawl`/`parallel`/`perplexity`）、`max_results`（默认 5）、`max_uses`（默认不限制，空/0/null 表示不写入限制）；经 `settings_from_params` 写入 `llm.web_search`，实际作用于 OpenRouter tool parameters。
- **类型**: frontend
- **依赖关系**: 无（依赖 v7.0 已有 `WebSearchConfig`）
- **Description**:
  - 扩展 `ConsoleParams` / `defaults_from_settings` / `settings_from_params`（今日仅覆盖 `enabled`）。
  - `max_uses` UI：Number 可空，或 Checkbox「限制次数」+ Number；映射为 `None` vs int。
  - 纳入 `PRESET_KEYS`；中文 info 简短说明费用与默认值。
  - 非 OpenRouter 时控件可仍可编辑，但实际不注入（与 v7.0 行为一致，可用 Markdown 提示）。
- **Input**: `WebSearchConfig`、现有 `web_search_enabled` 控件
- **Output**: Console 改参无需改 YAML 即可影响本次 Run
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/runner.py`
  - `src/podcast_ai/console/presets.py`
- **Estimated complexity**: S（1–1.5 小时）

---

### Task 05 - 轻量回归
- **Task name**: v7.1 - smoke tests
- **目标**: ① `settings_from_params` 正确合并 web_search 三参数；② 解析失败审计测（Task 02）通过；③ 日志解析能抽出 `web_search_requests`（可用假日志行）；④ 不破坏既有 Console preset 往返关键键。
- **类型**: backend
- **依赖关系**: Task 01（可选）、Task 02, Task 03, Task 04
- **Description**:
  - 不强制 Gradio E2E / 真实 OpenRouter。
- **Input**: Task 02–04
- **Output**: `pytest` 相关用例通过
- **Files involved**:
  - `tests/test_console_web_search_v71.py`（新建，建议）
  - Task 02 测试文件
- **Estimated complexity**: S（1 小时）
