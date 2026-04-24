## 版本 v4.1（迭代十九：新增 MiniMax TTS + TTS 模块重构）

基于 PRD v4.1 与 ARCHITECTURE 中 AD-2026-04-v4.1，本迭代目标是：
- 在不改变现有分层与主流程（pipeline/mixer/exporter）的前提下，新增 `minimax` TTS 供应商；
- 统一 `edge` / `elevenlabs` / `minimax` 三供应商调用抽象与公共逻辑；
- CLI 支持供应商选择，模型均从 `.env` 生效；
- 清理冗余分支，保持 TTS 代码简洁可维护。

MiniMax 本轮采用「同步语音合成（HTTP 非流式）」稳定主路径；`voice_setting` / `audio_setting` 先使用代码内默认值，不提前过度配置化。

---

### Task 01 - 统一 TTS 配置模型（含 MiniMax）
- **Task name**: v4.1 - TTS 配置契约扩展与校验
- **目标**: 在 `Settings/TTSConfig` 中正式支持三供应商配置，尤其补齐 MiniMax 必要字段（如 `api_key`、`model`、query 轮询参数）；并提供最小必填校验函数，非法配置时尽早报错。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 扩展 `src/podcast_ai/infra/config.py`：新增 `MiniMaxConfig`，并挂到 `TTSConfig`。
  - 统一 provider 合法值为 `edge` / `elevenlabs` / `minimax`（兼容历史别名可在路由层做 normalize）。
  - 新增 `require_minimax_tts_config(...)`（类似现有 `require_elevenlabs_tts_config`）。
  - 更新默认配置模板来源（`cli.py` 的 `init-config` 内容、`.env.example` 说明）以反映 v4.1。
- **Input**: PRD v4.1 + `MiniMax API Doc.md`
- **Output**: 可被客户端直接消费的统一 TTS 配置契约
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `src/podcast_ai/cli.py`（init-config 模板）
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 重构 `infra/tts_client.py`：抽取公共骨架与供应商路由
- **Task name**: v4.1 - TTS 客户端架构收敛
- **目标**: 将当前分散在各实现中的公共逻辑（缓存 key、重试、日志、错误包装、响应校验、落盘）抽成统一 helper/base，保证三供应商行为一致，降低新增 provider 成本。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 在 `tts_client.py` 内抽取可复用 helper：`_build_cache_path`、`_retry_synthesize`、`_validate_audio_bytes`、统一错误转换函数。
  - 保持外部接口不变：`TTSClient.synthesize(text, *, voice, language, use_cache) -> Path`。
  - `get_default_tts_client(...)` 改为统一 provider 路由，错误信息明确列出支持值。
  - 尽量删除重复代码块，避免 edge/elevenlabs/minimax 各自复制重试与落盘逻辑。
- **Input**: 现有 edge + elevenlabs 实现
- **Output**: 统一的 TTS 客户端骨架与清晰路由
- **Files involved**:
  - `src/podcast_ai/infra/tts_client.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 03 - 新增 MiniMax 同步 TTS 客户端
- **Task name**: v4.1 - MiniMax provider 实现（同步 HTTP 非流式）
- **目标**: 在 `tts_client.py` 内新增 `MiniMaxTTSClient`，完整打通：同步调用接口 -> 解析响应 `data.audio`（hex）-> 落盘缓存。
- **类型**: api
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 按 `MiniMax API Doc.md` 封装同步非流式接口（`/v1/t2a_v2`，`stream=false`）。
  - 解析返回体 `data.audio` 的 hex 编码并转为音频 bytes，校验后写入缓存。
  - 对 `base_resp.status_code != 0`、缺字段、hex 解码失败等场景返回清晰 `TTSServiceError`。
  - `voice_setting` / `audio_setting` 使用代码内默认值（v4.1 约束），并在注释中标注后续配置化点。
- **Input**: MiniMax API 文档、统一 TTS 配置
- **Output**: 可在主流程直接使用的 `MiniMaxTTSClient.synthesize`
- **Files involved**:
  - `src/podcast_ai/infra/tts_client.py`
- **Estimated complexity**: L（3-4 小时）

---

### Task 04 - CLI 增加 `tts_provider` 选择并透传到阶段二
- **Task name**: v4.1 - create-episode 增加 TTS 供应商参数
- **目标**: `create-episode` 命令支持用户显式选择 `edge` / `elevenlabs` / `minimax`，并透传到 `VoiceoverService` 实际生效；非法值直接阻断。
- **类型**: backend
- **依赖关系**: Task 01, Task 02
- **Description**:
  - 在 `src/podcast_ai/cli.py` 的 `create_episode_cli` 新增 `--tts-provider`（可选，覆盖配置）。
  - 在 `src/podcast_ai/core/pipeline.py` 增加 provider 覆盖入口（可通过注入构造好的 `tts_client` 或显式 provider 参数）。
  - 保持默认行为兼容：未传 CLI 参数时继续读取配置中的 `tts.provider`。
- **Input**: CLI 当前参数与 pipeline 调用链
- **Output**: 命令级供应商切换能力
- **Files involved**:
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/voiceover/tts_service.py`（如需增加 provider override）
- **Estimated complexity**: S（1-2 小时）

---

### Task 05 - VoiceoverService 对齐统一 TTS 抽象并清理冗余逻辑
- **Task name**: v4.1 - 语音服务层收敛与简化
- **目标**: 保持 `VoiceoverService` 只关注“文本与插入时间线”，将供应商差异全部下沉到 `infra/tts_client.py`；移除服务层中与供应商耦合的冗余分支。
- **类型**: backend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - 统一 `generate_voiceovers*` 两条路径调用 `self._tts.synthesize(...)` 的参数语义（voice/language/use_cache）。
  - 清理不再需要的 provider 特判注释与历史命名（如仅 edge 生效的误导性描述）。
  - 保证错误策略一致：配置/调用错误明确抛出，不静默降级。
- **Input**: 当前 `tts_service.py`
- **Output**: 业务层更薄、职责更清晰
- **Files involved**:
  - `src/podcast_ai/modules/voiceover/tts_service.py`
- **Estimated complexity**: S（1 小时）

---

### Task 06 - 回归测试与新增 MiniMax 路径测试
- **Task name**: v4.1 - TTS 三供应商行为测试
- **目标**: 覆盖最小可执行测试，确保重构后 edge/elevenlabs 不回退，且 minimax 同步链路可被稳定验证（mock HTTP）。
- **类型**: backend
- **依赖关系**: Task 03, Task 04, Task 05
- **Description**:
  - 新增/更新 `tests`：
    1) provider 路由测试（`get_default_tts_client` 返回类型与非法 provider 报错）；
    2) MiniMax 同步接口成功链路测试（含 `data.audio` hex -> bytes 落盘）；
    3) MiniMax 返回失败码/缺字段/hex 非法场景错误测试；
    4) CLI `--tts-provider` 参数透传生效测试（或 pipeline 级单测）。
  - 使用 mock/stub，避免真实外部 API 调用。
- **Input**: 重构后的 TTS 模块
- **Output**: v4.1 验收点对应自动化测试
- **Files involved**:
  - `tests/test_tts_client.py`（可新增）
  - `tests/test_voiceover.py`（可新增）
  - `tests/test_pipeline.py` / `tests/test_cli.py`（按现有测试结构选择）
- **Estimated complexity**: M（2-3 小时）

---

### Task 07 - 文档与冗余收尾（去除历史噪音）
- **Task name**: v4.1 - 配置文档与代码清理
- **目标**: 更新 README/配置说明为 v4.1 新口径，并移除旧 provider 文案与未使用代码，避免后续误用。
- **类型**: backend
- **依赖关系**: Task 01, Task 04
- **Description**:
  - 更新 `README.md` 中 TTS 配置与命令示例：展示 `edge` / `elevenlabs` / `minimax` 切换方式。
  - 清理 `tts_client.py` 中历史 `edge_tts` 命名残留（例如 `edge_tts` vs `edge` 的文案混乱）与未使用 helper/import。
  - 对 MiniMax 默认参数加简短注释，说明“本轮固定默认值，后续再配置化”。
- **Input**: 代码完成态
- **Output**: 文档与实现一致、无冗余噪音
- **Files involved**:
  - `README.md`
  - `src/podcast_ai/infra/tts_client.py`
  - `src/podcast_ai/cli.py`
- **Estimated complexity**: S（1 小时）
