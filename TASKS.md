## 版本 v7.0（迭代三十八：OpenRouter Server Tool 联网搜索接入全 Agent）

基于 PRD **v7.0** 与 ARCHITECTURE **AD-v7.0**：经 OpenRouter 时，在 LLM 请求中统一附带 Server Tool **`openrouter:web_search`**，覆盖阶段一**每一个** Agent 调用（`staged` / `legacy` / 各阶段 Critic；同客户端的 `single_agent` 一并生效）。模型按需搜索；OpenRouter 服务端执行。

**选型硬约束**：
- **只用** `tools: [{ "type": "openrouter:web_search", "parameters": {…} }]`
- **禁止**弃用路径：`plugins: [{ id: "web" }]`、模型名 `:online` 后缀
- 参考：`OpenRouter_Web_Search_Guide.md`

**架构原则（避免过度设计）**：
- 能力落在 **`infra/llm_client.py` + `infra/config.py`**；Agent / Orchestrator / sanitize **不改**业务 I/O
- 与既有 `response_format` / json_schema **并存**；不改 snapshot、终态 `state` 精简、编排 FSM
- Console 暴露开关

---

### Task 01 - 配置：`web_search` 开关与参数
- **Task name**: v7.0 - llm.web_search Settings
- **目标**: 在 Settings / `config.example.yaml` / init-config 模板增加联网搜索配置；非法值明确报错；环境变量可覆盖（与现有 pydantic-settings 风格一致）。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 建议挂在 `llm` 下，例如 `llm.web_search`：
    - `enabled: bool`（必有；默认值实现选定并在 README 写清，建议默认 `false` 控成本，或 `true` 开箱可用——二选一）
    - `engine: str = "auto"`
    - `max_results: int = 5`（建议校验 1–25）
    - `max_uses: int | null = None`（可选；有值才写入 parameters）
  - 关闭或非 OpenRouter 时下游不注入 tool（逻辑在 Task 02）。
- **Input**: PRD/AD-v7.0、Guide 参数表、现有 `LLMConfig`
- **Output**: 可开关、可调参的配置契约
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `config.example.yaml`
  - `src/podcast_ai/cli.py`（init-config 模板，若有）
  - `README.md`（一行说明）
- **Estimated complexity**: S（1 小时）

---

### Task 02 - `LLMClient` 统一注入 `openrouter:web_search`
- **Task name**: v7.0 - inject Server Tool in llm_client
- **目标**: `OpenAICompatibleLLMClient.generate` 在 **OpenRouter + `web_search.enabled`** 时，向 `/chat/completions` payload 注入官方形态的 `tools` 项；否则不注入。与 `response_format` / `provider` 并存；不实现插件或 `:online`。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 注入时机：与 `provider` 类似，在 `payload.update(kwargs)` **之后**写入（避免被 Agent kwargs 误覆盖）；若调用方显式传入 `tools`，定义简单策略（推荐：合并或配置优先——选一种并注释，避免双 web 表面）。
  - tool 对象形态：
    ```json
    { "type": "openrouter:web_search", "parameters": { "engine": "auto", "max_results": 5 } }
    ```
    仅包含已配置的非空参数；勿塞弃用字段。
  - 判定 OpenRouter：复用 `is_openrouter_base_url`。
  - 响应：继续以 `choices[0].message.content` 为主路径（Server Tool 由网关侧完成搜索回路）；content 缺失时给出可读错误（可附带 status/usage 线索）。
  - **不**在各 Agent 文件复制注入逻辑。
- **Input**: Task 01、现有 `llm_client.py`、Guide
- **Output**: 全 Agent 经同一客户端自动带上/不带上 web_search
- **Files involved**:
  - `src/podcast_ai/infra/llm_client.py`
- **Estimated complexity**: M（1.5–2 小时）

---

### Task 03 - 可观测：启用标记 + 搜索用量日志
- **Task name**: v7.0 - web_search observability
- **目标**: 每次 LLM 调用日志可区分是否启用 `web_search`；若响应含 `usage.server_tool_use.web_search_requests`（或 Guide/文档等价字段）则记录次数，便于成本排查。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 在现有 sanitized request debug 日志中体现 `web_search=on|off`（及 engine/max_results 摘要即可）。
  - 解析 `resp.json()` 的 usage 子树；字段缺失不报错。
  - 审计落盘：若改动成本低，可在 Agent audit 元数据带 `web_search_enabled`；**非必须**深改 `plan_audit`——日志满足 AC 即可。
- **Input**: Task 02 响应解析点
- **Output**: 开发者可从日志判断启用与用量
- **Files involved**:
  - `src/podcast_ai/infra/llm_client.py`
- **Estimated complexity**: S（≤1 小时）

---

### Task 04 - 单测：注入条件 + 禁止弃用路径 + 与 response_format 共存
- **Task name**: v7.0 - llm web_search unit tests
- **目标**: 用 mock httpx/transport 锁定：① OpenRouter+enabled → payload 含 `openrouter:web_search` 且 parameters 正确；② enabled=false 或不含 openrouter host → 无 `tools`（或无该 tool）；③ 同时带 `response_format` 时二者皆在；④ 代码/payload **不出现** `plugins` web 或 model `:online`。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 不强制真实 OpenRouter 联调；可选手工验收清单写在测试注释或 README。
- **Input**: Task 01–02
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_llm_web_search_v70.py`（新建）
- **Estimated complexity**: M（1–2 小时）

---

### Task 05 -（可选）Console / README 可观测开关
- **Task name**: v7.0 - docs + optional Console toggle
- **目标**: README 说明如何开关与调参、费用注意、与 Guide 的对应关系；Console 若改成本低可加只读展示或 Checkbox（非 AC 必须）。
- **类型**: frontend
- **依赖关系**: Task 01
- **Description**:
  - 不做完整搜索结果 UI；不改三阶段主路径。
- **Input**: Task 01 配置字段
- **Output**: 开发者可按文档启用联网搜索
- **Files involved**:
  - `README.md`
  - `src/podcast_ai/console/app.py` / `runner.py` / `presets.py`
- **Estimated complexity**: S（≤1 小时）
