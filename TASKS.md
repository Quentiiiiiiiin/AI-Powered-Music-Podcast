## 版本 v5.3（迭代二十八：阶段二 Console — 节目时间线试听与转场 `vm_seconds` 编辑）

基于 PRD v5.3 与 ARCHITECTURE **AD-v5.3**：在 Developer Console **阶段二面板**增强转场校验效率（仍为开发者调试中心，非 C 端产品）：

1. **解析**阶段二产物 `MixParamsJSON`，按真实节目时间线展示：音乐 / 串词 / 转场节点交错（而非仅粘贴原始 JSON）。
2. 时间线上的**音乐 / 串词**节点可点击播放对应本地音频，并支持进度条拖动定位。
3. 点击**转场**节点可在线编辑 `transitions[*].vm_seconds`（直接影响阶段三转场效果）。
4. 编辑完成后按原契约保存为**同目录新文件**（默认不覆盖源文件），可供阶段三消费。

**硬约束**：
- 能力收敛在 `console/`；**不改** `MixParamsJSON` / `MixParamsTransition` schema、`create_episode_stage2` / `finalize_episode_stage3` 契约、`Mixer` 转场语义。
- 试听只读 JSON 中已有路径（曲目 `track.file_path`、串词 `audio_path`）；**不做**二次混音或重算 intro。
- 保存前用 `MixParamsJSON` 校验；非法值明确报错。
- 模式对齐 v5.2 的 snapshot timeline，避免再造一套业务层。

---

### Task 01 - MixParams ↔ 节目时间线适配器（纯函数）
- **Task name**: v5.3 - mix_params timeline ⇄ MixParamsJSON
- **目标**: 在 `console/` 实现双向适配：将 `MixParamsJSON`（`tracks` + `voiceovers` + `transitions`）展开为节目时间线条目列表；编辑 `vm_seconds` 后再写回合法 MixParams 结构。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 时间线构建规则：按 `SelectedTrack.start/end` 与 `VoiceoverSegment.insert_time_in_episode` 交错；在每条 **voice→music** 边界插入对应 `MixParamsTransition` 节点（按 `voice_segment_id` 关联）。
  - 节点类型建议：`music` / `voice` / `transition`；music/voice 携带可播放路径；transition 携带 `vm_seconds`、`vm_candidate_seconds`、intro 展示字段（只读对比即可）。
  - 写回：仅允许改 `vm_seconds`（本轮聚焦）；其余字段透传；写回前 `MixParamsJSON.model_validate`。
  - 负值 / 缺失 transition / 路径缺失：明确错误，不静默丢数据。
- **Input**: mix_params JSON / `MixParamsJSON`
- **Output**: timeline DTO + `from_mix_params` / `apply_vm_edits`（或等价）
- **Files involved**:
  - `src/podcast_ai/console/mix_params_timeline.py`（新建）
  - （只读）`src/podcast_ai/core/models.py`
- **Estimated complexity**: M（2–3 小时）

---

### Task 02 - 阶段二面板：加载与时间线可读展示
- **Task name**: v5.3 - 阶段二 MixParams 时间线 UI
- **目标**: 在阶段二面板支持加载 mix_params 路径，并以时间线形式展示音乐 / 串词 / 转场节点（非整段原始 JSON 输入框了事）。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - Load：校验文件存在并用 `MixParamsJSON` 解析；失败明确报错。
  - 展示：按播出顺序列出节点摘要（标题、时长或 insert_time、路径短名、transition 的 `vm_seconds`）。
  - 与现有 Stage2 Run 表单并存；Run Stage2 成功后可回填 `mix_params_path` 并一键加载（可选，不强制自动加载）。
  - Gradio 下可用 Markdown 列表 + Dropdown/Radio 选中节点，避免复杂可视化时间轴（过度设计）。
- **Input**: Task 01 适配器
- **Output**: 阶段二可读时间线区块
- **Files involved**:
  - `src/podcast_ai/console/app.py`
- **Estimated complexity**: M（2 小时）

---

### Task 03 - 音乐 / 串词节点试听（含进度条）
- **Task name**: v5.3 - 时间线节点本地音频播放
- **目标**: 选中音乐或串词节点后，用 Gradio Audio 播放对应本地文件，并支持进度条拖动定位。
- **类型**: frontend
- **依赖关系**: Task 02
- **Description**:
  - 播放源：music → `SelectedTrack.track.file_path`；voice → `VoiceoverSegment.audio_path`。
  - 文件不存在：明确错误，不崩溃。
  - 不调用 Mixer、不生成临时混音预览（本轮范围外）。
  - 转场节点无独立音频时可显示提示，引导编辑 `vm_seconds`。
- **Input**: 选中的 timeline 节点
- **Output**: 可拖动进度的 Audio 预览
- **Files involved**:
  - `src/podcast_ai/console/app.py`
- **Estimated complexity**: S（1 小时）

---

### Task 04 - 编辑 `vm_seconds` + 同目录新文件保存
- **Task name**: v5.3 - vm_seconds 在线编辑与另存
- **目标**: 选中转场节点后可编辑 `vm_seconds`；保存时输出与阶段二原契约兼容的 JSON，写到同目录新文件（默认不覆盖）；该文件可被阶段三消费。
- **类型**: frontend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 编辑控件：Number 输入；展示只读对照 `vm_candidate_seconds` / intro 字段（便于调试）。
  - 校验：`vm_seconds >= 0`；可选提示「过大可能超过串词时长」（若易从 voiceovers 时长得到则做，否则依赖阶段三既有校验，避免重复业务）。
  - Save：新文件名建议 `{stem}_edited_{timestamp}.json`；成功后回填路径供阶段三使用。
  - 解析/保存失败：明确错误，不静默损坏源文件。
- **Input**: Task 01 写回逻辑
- **Output**: 可编辑 + 新文件路径
- **Files involved**:
  - `src/podcast_ai/console/app.py`
  - `src/podcast_ai/console/mix_params_timeline.py`
- **Estimated complexity**: M（1.5–2.5 小时）

---

### Task 05 - 契约回归测试与最小说明
- **Task name**: v5.3 - mix_params timeline 往返测试
- **目标**: 单测锁定：时间线展开顺序正确；改 `vm_seconds` 往返不丢其它字段；非法值不落盘；保存结果可通过 `MixParamsJSON` 校验。
- **类型**: backend
- **依赖关系**: Task 01（Task 04 完成后可补路径回填冒烟，非必须）
- **Description**:
  - fixture：最小 `tracks` + `voiceovers` + `transitions`（可用临时静音文件路径或 mock Path）。
  - README 补 2–3 行：阶段二时间线试听与 `vm_seconds` 另存说明。
- **Input**: 适配器
- **Output**: `pytest` 通过；文档一句对齐
- **Files involved**:
  - `tests/test_mix_params_timeline.py`（新建）
  - `README.md`
- **Estimated complexity**: S–M（1–2 小时）
