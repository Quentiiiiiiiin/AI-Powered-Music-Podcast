## 版本 v5.2（迭代二十七：阶段一 Console — 运行态洞察 + Snapshot 可读编辑）

基于 PRD v5.2 与 ARCHITECTURE **AD-v5.2**：在 Developer Console **阶段一面板**增强两类能力（仍为开发者调试中心，非 C 端产品）：

1. **运行态洞察**：除完整日志外，固定展示当前 **iteration**、正在运行的 **Agent**、失败时的 **失败原因**。
2. **Snapshot 可读编辑**：加载阶段一 snapshot JSON，按播出时间线展示并轻改——**章节 →【章节串词 → 音乐1 → 音乐1后串词 → 音乐2 → …】**；保存时按原 `Stage2Snapshot` schema 写回，**同目录新文件**（默认不覆盖源文件）。

**硬约束**：
- 能力收敛在 `console/`；可选轻量触达 `orchestrator` 可观测性钩子，**不改变**编排语义与 `Stage2Snapshot` 契约。
- 禁止把选曲/混音/业务校验搬进 UI；写回前用既有 Pydantic/`Stage2Snapshot` 校验。
- 信息密度与可观测性优先，不做营销式 UI。

---

### Task 01 - 运行态事件抽取（日志解析 和/或 可选进度钩子）
- **Task name**: v5.2 - plan 运行态事件源
- **目标**: 为阶段一 Run 提供可解析的「iteration / current_agent / failure_reason」事件流；优先从现有日志或 `audit/multi_agent` 抽取；若解析脆弱，在 `PlanOrchestrator` 增加**可选、无副作用**的进度回调或结构化日志字段。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 现状：`orchestrator` 已有 `iteration=%d, start_agent=%s`、`running agent=%s`、错误日志；`runner.run_plan` 已用 `_BufferLogHandler` 抓日志。
  - 在 `console/` 新增轻量解析器（如 `run_progress.py`）：从日志文本提取最新 iteration / agent / error。
  - 若日志不稳定：给 `PlanOrchestrator.run`（或 `_run_round_from`）增加可选 `on_progress(dict)` 回调，由 `runner` 注入并写入内存列表供 UI 读取——**默认不传回调时行为与现网一致**。
- **Input**: `orchestrator.py` 日志/`runner.py` 缓冲日志
- **Output**: 结构化进度数据（可测的纯函数 + 可选钩子）
- **Files involved**:
  - `src/podcast_ai/console/run_progress.py`（新建，建议）
  - `src/podcast_ai/console/runner.py`
  - （可选）`src/podcast_ai/modules/theme/orchestrator.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 02 - 阶段一面板：运行态洞察 UI
- **Task name**: v5.2 - 阶段一运行态展示
- **目标**: 在阶段一面板固定展示「当前 iteration / 当前 Agent / 失败原因」，并保留完整日志区；运行中与结束后均可读。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - 与现有 Status / logs 区并列，避免再堆一整页无关模块。
  - 失败时 **失败原因必填可见**（来自 `PodcastAIError` / AIServiceError / 解析到的 error）。
  - single_agent 模式：iteration/Agent 可显示「N/A 或 single_agent」，不得崩溃。
- **Input**: Task 01 进度数据 + `ConsoleRunResult`
- **Output**: 阶段一洞察区块可用
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/runner.py`（扩展 result 字段若需要）
- **Estimated complexity**: S（1–2 小时）

---

### Task 03 - Snapshot ↔ 播出时间线适配器（纯函数）
- **Task name**: v5.2 - timeline 视图 ⇄ Stage2Snapshot
- **目标**: 在 `console/` 实现双向适配：将 `Stage2Snapshot` 展开为可读时间线条目列表；编辑后再组装回合法 snapshot dict/模型。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01 并行）
- **Description**:
  - 时间线顺序（每段）：`segment_intro` → `(track_i → between_tracks after i)` 交错；`between_tracks` 按 `after_track_index` 对齐曲目。
  - 字段对齐现有契约：`schema`/`meta`/`segments[*].segment_id/name/target_duration_seconds/playlists/script`（以 `Stage2Snapshot` 与 `build_episode_snapshot_from_state` 为准）。
  - 可编辑字段最小集：至少串词文本（intro / between text）、曲目 `track`/`artist`；其余只读展示即可。
  - 写回前：`Stage2Snapshot.model_validate`；失败抛明确错误，不写盘。
- **Input**: snapshot JSON / `Stage2Snapshot`
- **Output**: timeline DTO + `to_snapshot` / `from_snapshot`
- **Files involved**:
  - `src/podcast_ai/console/snapshot_timeline.py`（新建）
  - （只读）`src/podcast_ai/core/models.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 04 - Snapshot 可读编辑 UI + 同目录新文件保存
- **Task name**: v5.2 - Snapshot 编辑器面板
- **目标**: 阶段一面板支持：加载 snapshot 路径 → 时间线可读展示与轻改 → 保存为同目录新文件（默认不覆盖）；保存失败明确报错。
- **类型**: frontend
- **依赖关系**: Task 03
- **Description**:
  - Load：读取路径，解析失败则错误提示。
  - 展示：按章节分组的时间线（Markdown + 可编辑文本框，或简化列表编辑；避免整段原始 JSON 输入框了事）。
  - Save：新文件名建议 `{stem}_edited_{timestamp}.json` 或用户可填后缀；写盘前校验；成功后可回填路径供阶段二使用。
  - Gradio 限制下可先「按段折叠 + 文本域编辑」实现，不做复杂拖拽排序（避免过度设计）。
- **Input**: Task 03 适配器
- **Output**: 可读编辑 + 新文件路径
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/snapshot_timeline.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 05 - 契约回归测试与最小说明
- **Task name**: v5.2 - timeline 往返 + 进度解析测试
- **目标**: 用单测锁定：timeline ↔ snapshot 往返不丢关键字段；进度解析能从样例日志抽出 iteration/agent；非法 snapshot 保存失败不落盘。
- **类型**: backend
- **依赖关系**: Task 01, Task 03（Task 04 后可补 UI smoke，非必须）
- **Description**:
  - fixture 可用精简 snapshot（对齐 `Sample_EpisodePlan_NEW` / 真实 `Stage2Snapshot` 字段名）。
  - 可选：断言保存文件可被 `Stage2Snapshot.model_validate_json` 通过。
  - README 补 2–3 行阶段一洞察与 snapshot 编辑说明即可。
- **Input**: 适配器与解析器
- **Output**: `pytest` 通过；文档一句对齐
- **Files involved**:
  - `tests/test_snapshot_timeline.py`（新建）
  - `tests/test_run_progress.py`（新建，可选合并）
  - `README.md`
- **Estimated complexity**: M（1.5–2.5 小时）
