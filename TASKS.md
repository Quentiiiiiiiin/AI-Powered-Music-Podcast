## 版本 v4.6（迭代二十四：OpenRouter 可配置 LLM provider）

基于 PRD v4.6：在经 **OpenRouter**（`llm.base_url` 指向 `openrouter.ai`）调用时，除现有 **`model`** 外，增加可在 **`config.yaml`** 中编辑的 **OpenRouter 供应方路由**配置；请求层读取后写入 OpenRouter **官方文档约定的** `chat/completions` 请求体字段（与 `messages`、`response_format` 等并列）。

**语义约定（与 PRD 对齐）**：
- **留空 / 未配置**：请求体**不**携带 OpenRouter 的 `provider` 路由对象（或按官方文档等价省略），由 OpenRouter **自动选择** provider；不得因未填而报错。
- **非空**：按配置路由；若 OpenRouter 返回 **provider 非法或与 model 不兼容** 等错误，**原样透出**可读错误，**不静默回退**到未知默认。

**约束**：不改变阶段一各 Agent 业务契约；**v3.4** `response_format` / `json_schema` 严格输出语义保持不变。

---

### Task 01 - 配置模型与命名（避免与 `llm.provider` 混淆）
- **Task name**: v4.6 - LLMConfig 增加 OpenRouter 路由字段
- **目标**: 在 `LLMConfig` 增加**独立字段**（建议 `openrouter_provider: str = ""`），表示 OpenRouter 的供应方路由；**不要**复用现有 `llm.provider`（该字段表示客户端实现类型，如 `openai_compatible`）。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 支持 `config.yaml` 与 `.env` 嵌套（如 `PODCAST_AI_LLM__OPENROUTER_PROVIDER`）加载。
  - 默认值空串，保证「迭代前默认行为」：不传 OpenRouter `provider` 对象。
- **Input**: PRD v4.6 验收 1、5
- **Output**: 可序列化、可校验的配置字段
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
- **Estimated complexity**: S（0.5–1 小时）

---

### Task 02 - 请求层：按 OpenRouter 文档组装 `provider` 请求体
- **Task name**: v4.6 - OpenAICompatibleLLMClient 写入 provider
- **目标**: 在 `OpenAICompatibleLLMClient.generate` 构建 `payload` 时：若 `base_url` 判定为 OpenRouter 且 `openrouter_provider` 非空，则附加官方约定的 **`provider` 对象**（例如 `only` / `order` 等，以实现为准并对照当前 OpenRouter 文档）；否则**完全不加入** `provider` 键。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 非 OpenRouter 网关（`base_url` 不含 openrouter）时，**忽略** `openrouter_provider`，避免向非 OpenRouter 服务发送未知字段。
  - 脱敏日志：`safe_payload` 中对 `provider` 做摘要（避免日志爆炸），与现有 `response_format` 脱敏风格一致。
  - `kwargs`（含各 Agent 传入的 `response_format`）与 `model`/新增字段合并顺序保持不变，满足 PRD 验收 4。
- **Input**: `src/podcast_ai/infra/llm_client.py`、OpenRouter 官方 provider routing 文档
- **Output**: 所有经该客户端的 OpenRouter 请求统一携带正确 `provider` 行为
- **Files involved**:
  - `src/podcast_ai/infra/llm_client.py`
- **Estimated complexity**: M（1.5–2.5 小时）

---

### Task 03 - 错误信息与向后兼容验收
- **Task name**: v4.6 - Provider 相关 4xx 提示与兼容
- **目标**: 当 OpenRouter 因 `provider` 与 `model` 不兼容返回 400 等错误时，错误信息足够定位（可附带响应体片段，已有逻辑上扩展即可）；**provider 为空**路径下现有成功调用不受影响（PRD 验收 3、5）。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 仅在确有必要时扩展 `AIServiceError` 提示文案（避免过度分支）。
  - 确认多 Agent 路径均通过 `get_default_llm_client` → 同一 `generate`，无旁路重复实现。
- **Input**: 现有 `AIServiceError` 抛出点
- **Output**: 调试时可读、空 provider 不回归
- **Files involved**:
  - `src/podcast_ai/infra/llm_client.py`
  - （只读核对）`src/podcast_ai/modules/theme/*_agent.py`、`llm_planner.py`
- **Estimated complexity**: S（0.5–1 小时）

---

### Task 04 - 示例配置与文档
- **Task name**: v4.6 - README / init-config 示例
- **目标**: 在 `README.md` 与 `cli.py` 内 `_INIT_CONFIG_YAML` 的 `llm:` 段补充 `openrouter_provider`（注释说明：留空=自动路由；非空=指定供应方 slug，具体形态以 OpenRouter 文档为准）。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 若仓库存在 `config.example.yaml` / `.env.example`，同步一行说明即可，不展开长篇文档。
- **Input**: PRD 验收 1
- **Output**: 用户可复制即用的最小示例
- **Files involved**:
  - `README.md`
  - `src/podcast_ai/cli.py`
  - （若存在）`config.example.yaml`、`.env.example`
- **Estimated complexity**: S（0.5 小时）

---

### Task 05 - 单测：请求体是否包含 `provider`
- **Task name**: v4.6 - LLM 请求 payload 断言
- **目标**: 使用 `httpx` mock / 拦截，验证：① `openrouter_provider` 为空时 payload **无** `provider`；② 非空时 payload **有**符合实现约定的 `provider`；③ 非 OpenRouter `base_url` 时不发送 `provider`。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 不发起真实外网请求。
  - 可选：断言 `response_format` 仍存在（与 kwargs 合并，满足验收 4）。
- **Input**: `tests/` 现有 pytest 风格
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_llm_client.py`（新建）或并入现有测试文件
- **Estimated complexity**: M（1–2 小时）
