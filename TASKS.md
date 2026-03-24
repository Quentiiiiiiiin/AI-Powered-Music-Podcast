## 版本 v1.4（迭代四：TTS 供应商切换到 ElevenLabs）

基于 PRD/ARCHITECTURE 更新：主持语音默认 TTS 从 Edge 路径切换为 ElevenLabs；需打通可用调用链路（SDK 或 HTTP 任一），失败时清晰报错；并保持 v1.3 的串词时序与边界逻辑不回退。

---

### Task 01 - ElevenLabs 配置项落地与校验
- **Task name**: 增加 ElevenLabs 配置模型与必填校验
- **Description**: 在配置层新增/确认 ElevenLabs 所需配置（`api_key`、`voice_id`、`model`、`output_format`），并在启动/调用前进行最小校验，缺失配置时给出明确错误信息，避免运行期隐式失败。
- **Input**:
  - 现有 `Settings` 配置结构
  - `.env` / `config.yaml` 中的 TTS 配置
- **Output**:
  - 可被 `infra/tts_client.py` 直接消费的 ElevenLabs 配置对象
  - 缺失配置时的清晰错误提示
- **Files involved**:
  - `src/podcast_ai/infra/config.py`
  - `.env.example` / `config.example.yaml`（若仓库中存在）
- **Dependencies**: 无
- **Estimated complexity**: S（1 小时）
- **Type**: backend

---

### Task 02 - TTSClient 默认实现切换为 ElevenLabs
- **Task name**: 在 `tts_client.py` 实现 ElevenLabs 主路径
- **Description**: 在 `infra/tts_client.py` 实现 ElevenLabs 调用（SDK/HTTP 二选一即可），并将 `get_default_tts_client(...)` 默认返回 ElevenLabs 实现；保留统一接口 `synthesize(text, voice, use_cache)`，输出可供混音链路使用的音频文件。
- **Input**:
  - Task 01 的配置对象
  - `text/voice/use_cache` 调用参数
- **Output**:
  - ElevenLabs 生成的本地音频文件路径
  - 默认 TTS 路径已从 Edge 切换到 ElevenLabs
- **Files involved**:
  - `src/podcast_ai/infra/tts_client.py`
- **Dependencies**: Task 01
- **Estimated complexity**: M（2-3 小时）
- **Type**: backend

---

### Task 03 - 失败处理与错误信息标准化
- **Task name**: ElevenLabs 调用失败时提供清晰错误
- **Description**: 对 ElevenLabs 的鉴权失败、配额不足、网络超时、返回格式异常等场景做统一错误包装，确保上层能得到“可定位原因”的信息（不静默、不吞错）。
- **Input**:
  - ElevenLabs 调用异常/错误码
  - 现有异常体系（`AIServiceError` / `PodcastAIError`）
- **Output**:
  - 标准化错误消息（可直接在 CLI 展示）
  - 失败时流程按预期中断
- **Files involved**:
  - `src/podcast_ai/infra/tts_client.py`
  - `src/podcast_ai/core/exceptions.py`（如需补充错误类型）
- **Dependencies**: Task 02
- **Estimated complexity**: S（1 小时）
- **Type**: backend

---

### Task 04 - Voiceover 兼容新 TTS 且不破坏时序
- **Task name**: `VoiceoverService` 对接 ElevenLabs 默认客户端
- **Description**: 校验并调整 `modules/voiceover/tts_service.py` 调用，确保仍通过统一 `TTSClient` 接口生成音频；不得改动 v1.3 的插入点计算与顺序规则（仅替换语音供应商，不改时序策略）。
- **Input**:
  - `EpisodePlan` + 现有边界/顺序策略
  - Task 02 的默认 TTS 客户端
- **Output**:
  - 主持语音可通过 ElevenLabs 正常生成并进入后续混音
  - 串词顺序与边界对齐逻辑保持 v1.3
- **Files involved**:
  - `src/podcast_ai/modules/voiceover/tts_service.py`
  - `src/podcast_ai/core/pipeline.py`（仅在注入路径有调整时）
- **Dependencies**: Task 02, Task 03
- **Estimated complexity**: S（1 小时）
- **Type**: backend

---

### Task 05 - 测试与验收（供应商切换回归）
- **Task name**: 增加 ElevenLabs 路径测试与关键回归验证
- **Description**: 增加/更新测试：1）默认客户端为 ElevenLabs；2）`synthesize` 产出文件可被后续链路消费；3）调用失败时抛出清晰错误；4）v1.3 时序相关测试继续通过（防回归）。
- **Input**:
  - mock 的 ElevenLabs 响应/错误
  - 现有 voiceover/mixing/pipeline 测试用例
- **Output**:
  - 自动化测试覆盖 v1.4 验收点
  - 回归结果可证明“供应商切换未破坏时序逻辑”
- **Files involved**:
  - `tests/test_pipeline.py`
  - `tests/test_mixing.py`
  - `tests/test_voiceover.py`（若不存在则新增）
  - `tests/conftest.py`（如需补 fixture）
- **Dependencies**: Task 04
- **Estimated complexity**: M（2-3 小时）
- **Type**: backend

---

### Task 06 - 文档与运行指引更新
- **Task name**: 更新 README/示例配置到 ElevenLabs 主路径
- **Description**: 更新文档中的 TTS 说明、环境变量示例、常见错误排查（鉴权、voice_id、配额、网络），明确 ElevenLabs 为默认路径，Edge 不再作为主路径说明。
- **Input**:
  - 最终实现后的配置字段与错误信息
- **Output**:
  - 可直接照文档配置并跑通 ElevenLabs
- **Files involved**:
  - `README.md`
  - `.env.example` / `config.example.yaml`
- **Dependencies**: Task 01, Task 03
- **Estimated complexity**: S（1 小时）
- **Type**: backend

