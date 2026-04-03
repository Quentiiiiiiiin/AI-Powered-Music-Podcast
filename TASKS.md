## 版本 v3.4（迭代十二：OpenRouter 结构化输出 response_format）

基于 PRD v3.4：在 **经 OpenRouter** 调用大模型时，请求体在 `messages` 之外携带 **`response_format`**（形态对齐仓库内 `openrouter_structured_output.json`：`type: json_schema`、`json_schema.strict: true`、内嵌 `schema`），使 Planner / Music Curator / Script Writer / Critic 各自按契约输出 JSON，降低后续对 v3.2/v3.3 修复链路的依赖（修复保留为可选兜底）。非 OpenRouter 或未启用结构化输出时，行为须与现状兼容、不强行附加 `response_format`。

---

### Task 01 - 四份 Agent 输出 JSON Schema（strict 对齐契约）
- **Task name**: v3.4 - Agent 专用 `response_format` 内联 schema
- **目标**: 为四个 Agent 各定义一份与其 **sanitize / patch 契约**一致的 JSON Schema（字段名 snake_case、允许写的键与 `planner_agent` / `music_curator_agent` / `script_writer_agent` / `critic_agent` 中的越权校验一致），并满足 OpenRouter/OpenAI 系 **strict json_schema** 常见要求（如对象 `additionalProperties: false`、`required` 覆盖需约束的 property 等，按官方文档逐条自检）。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 新增模块（建议）：`src/podcast_ai/modules/theme/agent_response_schemas.py`
  - 导出例如：`PLANNER_RESPONSE_SCHEMA`、`CURATOR_RESPONSE_SCHEMA`、`SCRIPT_WRITER_RESPONSE_SCHEMA`、`CRITIC_RESPONSE_SCHEMA`，以及 `build_openrouter_response_format(name: str, schema: dict) -> dict`（返回 `openrouter_structured_output.json` 中与 `messages` 并列的 `response_format` 对象：`type`、`json_schema.name` / `strict` / `schema`）。
  - 与 `state_schema.json`、PRD「6.3 Agent 契约」对照，避免字段冲突；**不要求**整份 state，仅覆盖各 Agent **增量 JSON** 的根结构。
- **Input**: 现有 Agent sanitize 白名单与 PRD 契约
- **Output**: 可被序列化进 HTTP body 的 schema 字典 + 组装好的 `response_format` 工厂函数
- **Files involved**:
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`（新增）
  - 参考：`src/podcast_ai/modules/theme/{planner_agent,music_curator_agent,script_writer_agent,critic_agent}.py`
  - 参考：`openrouter_structured_output.json`、`state_schema.json`
- **Estimated complexity**: L（2-3 小时，strict 下 property 枚举需仔细对齐）

---

### Task 02 - 配置与路由：何时对 OpenRouter 启用结构化输出
- **Task name**: v3.4 - OpenRouter 检测 + `structured_output` 开关
- **目标**: 明确仅在与 OpenRouter 集成时发送 `response_format`；避免对其它 OpenAI 兼容网关误发导致 400。支持显式配置覆盖「自动检测」。
- **类型**: backend
- **依赖关系**: 无（可与 Task 01 并行）
- **Description**:
  - 在 `src/podcast_ai/infra/config.py`（及 `config.yaml` / env 文档注释）增加例如：`llm.structured_output: bool | None`（`None` = 按 base_url 识别 OpenRouter，如 host 含 `openrouter.ai` 则启用；`true`/`false` 强制开/关）。
  - 提供小函数：`def should_use_structured_output(cfg: LLMConfig) -> bool`，供 Agent 与 client 调用。
  - 验收对齐 PRD：非 OpenRouter 或未开启时不带 `response_format`；**不得**因默认值误伤现有部署。
- **Input**: `LLMConfig` / `Settings`
- **Output**: 可复用的布尔判定与配置项
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - （可选）`config.yaml` 示例、`README.md` 一句说明
- **Estimated complexity**: S（1 小时）

---

### Task 03 - 四个 Agent 在 `generate` 中附带 `response_format`
- **Task name**: v3.4 - Agent `run` 接入 OpenRouter 结构化请求体
- **目标**: 当 `should_use_structured_output` 为真时，Planner / Music Curator / Script Writer / Critic 调用 `self._llm.generate(messages, temperature=..., response_format=...)`，其中 `response_format` 由 Task 01 工厂按 Agent 类型生成；与 `openrouter_structured_output.json` 并列字段形态一致。
- **类型**: backend
- **依赖关系**: Task 01、Task 02
- **Description**:
  - 修改四个 Agent 文件：在 `generate(...)` 调用处按 Agent 选择对应 schema 名称与 `build_openrouter_response_format`。
  - 保持：`OpenAICompatibleLLMClient.generate` 已通过 `payload.update(kwargs)` 合并额外字段，**无需**为 v3.4 强行改 client 签名；若需统一日志脱敏，确保 debug 日志不打印完整 schema（可选 truncate）。
- **Input**: LLM messages + 配置
- **Output**: API 请求体含 `messages` + `response_format`（启用时）
- **Files involved**:
  - `src/podcast_ai/modules/theme/planner_agent.py`
  - `src/podcast_ai/modules/theme/music_curator_agent.py`
  - `src/podcast_ai/modules/theme/script_writer_agent.py`
  - `src/podcast_ai/modules/theme/critic_agent.py`
  - `src/podcast_ai/modules/theme/agent_response_schemas.py`
- **Estimated complexity**: M（1.5-2.5 小时）

---

### Task 04 - 失败路径：不支持结构化或响应不符合约定
- **Task name**: v3.4 - 结构化输出错误显式失败 + 可追踪日志
- **目标**: 满足 PRD 验收 3：若 API 因 `response_format` 返回 4xx、或响应 `content` 无法解析为与契约一致的 JSON，须 **明确 `AIServiceError`**，日志/异常信息中包含 **可定位标识**（如 `request_id`、`meta.request_id`、iteration、Agent 名），**禁止静默继续**。
- **类型**: backend
- **依赖关系**: Task 03
- **Description**:
  - 审视 `OpenAICompatibleLLMClient`：对 HTTP 4xx 若 body 提示 schema/response_format 不支持，错误文案可简短附带 `resp.text` 前缀（已有 300 字截断可沿用）。
  - Agent 侧：结构化主路径下若 `json.loads` 仍失败，沿用现有 `dump_json_repair_debug`（或与 v3.2 一致策略），并在 message 中标明 Agent 名称与「结构化输出仍解析失败」。
  - **不做**自动降级重试整段无 schema 的请求（除非 PRD 后续迭代要求），以免掩盖配置错误。
- **Input**: 失败 API 响应 / 异常 content
- **Output**: 可读的失败原因 + debug 工件路径（如有）
- **Files involved**:
  - `src/podcast_ai/infra/llm_client.py`（按需收紧错误信息）
  - `src/podcast_ai/modules/theme/{planner_agent,music_curator_agent,script_writer_agent,critic_agent}.py`
- **Estimated complexity**: S（1 小时）

---

### Task 05 - 单元测试：payload 形态与开关行为
- **Task name**: v3.4 - 结构化输出请求与回归测试
- **目标**: 自动化覆盖 PRD 验收 1、5：启用结构化时 POST body 含正确 `response_format`（`json_schema` + `strict: true`）；关闭或非 OpenRouter 时不含该字段；可对 `should_use_structured_output` 做参数化断言。
- **类型**: backend
- **依赖关系**: Task 01～04（至少 01～03 完成后编写）
- **Description**:
  - 使用 `httpx.MockTransport` 或 patch `httpx.Client.post` 捕获 JSON body，断言四个 Agent 在开启开关时各自传入对应 `json_schema.name` / 顶层 `type`。
  - 可选：快照或最小断言 `schema.properties` 中含关键键（如 Planner 的 `segments` / Critic 的 `critic`+`control`）。
- **Input**: mock LLM HTTP
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_llm_structured_output.py`（新建）或扩展现有 `tests/test_theme_*.py`
- **Estimated complexity**: M（2 小时）

---

### Task 06（可选）- 文档与示例对齐
- **Task name**: v3.4（可选）- README / PRD 侧配置说明
- **目标**: 简短说明如何通过 `config.yaml` 使用 OpenRouter + 结构化输出，并指向 `openrouter_structured_output.json`。
- **类型**: backend（文档）
- **依赖关系**: Task 02
- **Description**:
  - 仅在需要时更新 `README.md` 一段；若团队约定不写文档则可跳过。
- **Estimated complexity**: XS（≤0.5 小时）
