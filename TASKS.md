## 版本 v7.2（迭代四十：Agent 输出校验重试，与 HTTP retry 分离）

基于 PRD **v7.2** 与 ARCHITECTURE **AD-v7.2**：在各 Agent `run()`（及同路径 `single_agent`）内，对「generate → JSON 解析 → sanitize」增加**第二层重试**。解析或 sanitize 失败时，用**同一套** messages / `response_format` / tools 再 generate（不回灌错误、不改 temperature）。

**两层重试勿混用**：
- **HTTP**：现有 `llm.max_retries`，仅 `infra/llm_client.py` 覆盖传输/网关失败。
- **输出校验**：独立配置 `llm.output_max_retries`（建议名），默认额外 **2** 次（合计最多 3 次 generate）；**`0` 关闭**，行为与现网一致。

**硬约束 / 非目标**：
- 不改编排 FSM、不放宽 sanitize/`response_format`、不把校验失败当 Critic revision。
- **禁止**用 `llm.max_retries` 兼做输出校验重试。
- 本轮不做 Console/CLI 新控件；不作用于 TTS/混音。
- HTTP 失败不计入输出校验次数；成功路径不增加额外 generate。

---

### Task 01 - 配置：`llm.output_max_retries` 与 HTTP retry 分计
- **Task name**: v7.2 - Settings output_max_retries
- **目标**: 在 `LLMConfig` / `config.example.yaml` / init-config 增加独立字段（建议 `llm.output_max_retries`）；默认 **2**；`0` 合法且表示关闭；非法值（负数、非 int）明确报错；环境变量可覆盖。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 语义：额外 generate 次数（不含首次）。默认 2 → 最多 3 次 generate。
  - README 一两句标明与 `llm.max_retries` 的区别（网络 vs 输出校验）。
  - 本轮不暴露 Console/CLI 开关。
- **Input**: PRD/AD-v7.2、现有 `LLMConfig.max_retries`
- **Output**: 可配、默认同 PRD、与 HTTP retry 字段分离
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `config.example.yaml`
  - `src/podcast_ai/cli.py`（init-config 模板，若有）
  - `README.md`
- **Estimated complexity**: S（≤1 小时）

---

### Task 02 - 薄封装：generate → parse → sanitize 循环（避免四 Agent 复制）
- **Task name**: v7.2 - output validation retry helper
- **目标**: 在 `modules/theme` 内提供极小 helper：按 `output_max_retries` 循环同一 generate kwargs；parse 或 sanitize 失败则再试；成功返回 parsed+sanitized；耗尽抛最后一次 `AIServiceError`。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 建议新文件 `agent_output_retry.py`（或等价），**不**引入新服务层、不改编排。
  - 回调签名保持简单：`generate()` / `parse(raw)` / `sanitize(data)`；失败阶段记为 `parse` 或 `sanitize`。
  - HTTP/`AIServiceError` 来自 `generate`：**直接上抛**，不消耗输出校验次数（与 HTTP retry 分计）。
  - 日志：尝试序号（1-based）、失败阶段、最终成功或耗尽。
  - 每次失败尝试通过可选 `on_attempt_failed(raw, stage, exc)` 落盘审计（`parsed_patch=None`）；成功尝试仍按现有约定写审计。
- **Input**: Task 01 配置、现有 parse/sanitize
- **Output**: 可被四 Agent + single_agent 复用的循环
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_output_retry.py`（新建，建议）
- **Estimated complexity**: M（1.5–2 小时）

---

### Task 03 - 四 Agent + single_agent 接线
- **Task name**: v7.2 - wire retry into Agent.run
- **目标**: Planner / Music Curator / Script Writer / Critic 的 `run()` 用 helper 包住 generate→parse→sanitize；`llm_planner` 的 `single_agent` 若走同一环路则一并接入。`output_max_retries=0` 时与现网一致（一次失败即抛）。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 保持 messages / `response_format` / tools / temperature 在重试间不变。
  - 成功即既有 `merge_plan_state`；耗尽仍 `AIServiceError`，Orchestrator 现有 error 路径不变（`control.status=error`、不进入下一 Agent）。
  - sanitize 失败也要审计 raw（与 parse 失败对称）。
  - **不改** orchestrator FSM、sanitize 规则、schema。
- **Input**: Task 02 helper、现有各 `*_agent.py`、v7.1 解析失败审计
- **Output**: 全 Agent（含 staged/legacy Critic、single_agent）具备输出校验重试
- **Files involved**:
  - `src/podcast_ai/modules/theme/planner_agent.py`
  - `src/podcast_ai/modules/theme/music_curator_agent.py`
  - `src/podcast_ai/modules/theme/script_writer_agent.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/llm_planner.py`（single_agent 路径，若适用）
- **Estimated complexity**: M（2 小时）

---

### Task 04 - 单测：关闭、成功补抽、耗尽、与 HTTP retry 分计
- **Task name**: v7.2 - output retry unit tests
- **目标**: mock LLM，不调真实 API。锁定：① `output_max_retries=0` 一次 parse/sanitize 失败即抛、只 generate 1 次；② 首次非法、第二次合格 → 成功且 state 用合格结果、generate=2；③ 连续失败用尽 → 抛错、generate=1+N；④ `generate` 抛 HTTP/`AIServiceError` 不额外循环输出校验；⑤ 失败尝试均触发审计 raw。
- **类型**: backend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - 优先测 helper；再选 1 个 Agent（如 Planner）做接线冒烟即可。
  - 不强制改 orchestrator 测试（FSM 未变）。
- **Input**: Task 02–03
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_agent_output_retry_v72.py`（新建）
- **Estimated complexity**: M（1.5–2 小时）
