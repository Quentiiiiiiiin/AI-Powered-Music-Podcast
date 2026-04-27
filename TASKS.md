## 版本 v4.4（迭代二十二：`estimate_track_intro_seconds` 判定逻辑增强）

基于 PRD v4.4：本迭代仅增强 `src/podcast_ai/modules/mixing/intro_align.py` 中 `estimate_track_intro_seconds` 的内部判定逻辑，
保持对外契约不变（返回字段仍为 `intro_seconds` / `confidence` / `reason`），不改上游调用接口与阶段二主流程。

核心方向：
- **R1（抗噪）**：RMS 由“首帧超阈值”升级为“连续 N 帧 + 最短稳定时长”成立；
- **R2（抗装饰音）**：onset 候选需满足强度分位阈值 + 后续短窗能量支持，避免把装饰音当主段进入点；
- 对低动态曲风提供可解释的阈值放宽/自适应路径；失败分支继续稳定回退并给出明确 `reason`。

---

### Task 01 - 规则梳理与参数口径定稿
- **Task name**: v4.4 - intro 判定规则与参数基线
- **目标**: 将 v4.4 的 R1/R2 规则映射为可编码参数（如 `stable_frames`、`min_stable_ms`、`onset_percentile`、`support_window_ms`、低动态放宽条件），形成单一判定流程，避免分散 if-else。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 定义默认值（建议与 PRD 对齐：`N≈4`、`min_stable_ms≈120`、`onset_percentile≈70~80`、`support_window≈300ms`）。
  - 约定低动态曲风判定信号（例如 RMS 动态范围阈值）和放宽策略（降低 onset 分位阈值或稳定帧要求）。
  - 明确 `reason` 命名规范，确保后续日志/测试可区分主分支。
- **Input**: PRD v4.4 条目 + 现有 `intro_align.py`
- **Output**: 可直接实现的判定流程与参数表
- **Files involved**:
  - `src/podcast_ai/modules/mixing/intro_align.py`
- **Estimated complexity**: S（0.5-1 小时）

---

### Task 02 - 实现 R1：RMS 连续帧稳定判定（含快速通道）
- **Task name**: v4.4 - RMS 稳定抬升检测
- **目标**: 将 RMS 触发逻辑改为“连续 N 帧超阈值 + 最短稳定时长”判定，显著减少单帧噪声触发；并在满足条件时提供谨慎的快速通道，避免过晚。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 在 RMS 序列上实现连续段扫描，输出候选时间点与稳定性评分。
  - 快速通道仅在“前段突增且后续有持续支撑”场景触发，避免放大噪声。
  - 失败回退仍返回 `intro_seconds=None` 并带 `reason`，不抛异常中断主流程。
- **Input**: 当前 `rms`、阈值参数
- **Output**: 更稳健的 `rms_lift_t` 候选
- **Files involved**:
  - `src/podcast_ai/modules/mixing/intro_align.py`
- **Estimated complexity**: M（1.5-2 小时）

---

### Task 03 - 实现 R2：Onset 强度过滤 + 后续能量支持
- **Task name**: v4.4 - Onset 候选双重过滤
- **目标**: 不再直接使用首个 onset；仅接受强度超过分位阈值的 onset，且其后约 0.3s 窗口内有持续能量支持，降低装饰音误触发。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 从 onset 候选中筛掉低强度点，再做后续 RMS 支撑校验。
  - 对低动态场景应用自适应放宽，避免系统性过晚。
  - 记录分支 `reason`（如 `onset_filtered_out`、`onset_no_energy_support`、`low_dynamic_relaxed`）。
- **Input**: onset_strength / onset_frames / rms
- **Output**: 更可信的 `onset_t` 候选
- **Files involved**:
  - `src/podcast_ai/modules/mixing/intro_align.py`
- **Estimated complexity**: M（1.5-2 小时）

---

### Task 04 - 候选融合、confidence 重标定与 reason 体系
- **Task name**: v4.4 - 候选融合与可解释输出
- **目标**: 在保持返回结构不变的前提下，统一融合 `rms_lift_t` 与 `onset_t`，并重构 `confidence` 计算与 `reason` 分类，使其可用于后续策略分流。
- **类型**: backend
- **依赖关系**: Task 02, Task 03
- **Description**:
  - 融合策略保持简洁（例如按稳定度和一致性打分，而非复杂模型）。
  - `confidence` 与失败/降级原因保持一致性（高置信度必须有足够证据）。
  - 严格保持函数签名与返回字段不变（满足 PRD 验收 1）。
- **Input**: R1/R2 候选与评分
- **Output**: 稳定且可解释的 `IntroEstimate`
- **Files involved**:
  - `src/podcast_ai/modules/mixing/intro_align.py`
- **Estimated complexity**: S（1 小时）

---

### Task 05 - 单测扩展：噪声/装饰音/低动态三类场景
- **Task name**: v4.4 - `estimate_track_intro_seconds` 回归测试
- **目标**: 为 v4.4 的 R1/R2 规则补充可重复测试，验证过早/过晚误判下降，且失败分支与 `reason` 可追溯。
- **类型**: backend
- **依赖关系**: Task 02, Task 03, Task 04
- **Description**:
  - 场景覆盖：
    1) 单帧/短突增噪声不应触发过早 intro；
    2) 首个装饰音应被过滤，主段前后能量支持更优先；
    3) 低动态样本在自适应放宽下不应系统性过晚；
    4) librosa 不可用/加载失败/近静音等失败路径 reason 稳定。
  - 优先使用可控合成波形或 mock，避免引入大体积测试素材。
- **Input**: 现有测试框架
- **Output**: `pytest` 通过，覆盖 PRD 验收 2/3/4/5
- **Files involved**:
  - `tests/test_mixing.py`（若现有集中在此）
  - `tests/test_intro_align.py`（建议新增，保持职责清晰）
- **Estimated complexity**: M（2-3 小时）

---

### Task 06 - 冗余清理与最小文档同步
- **Task name**: v4.4 - 判定路径清理与注释更新
- **目标**: 清理 v4.2 留下的重复/弱判定分支，保留单一路径；补充简明注释，说明 R1/R2 与低动态放宽逻辑，避免未来继续堆叠条件分支。
- **类型**: backend
- **依赖关系**: Task 04
- **Description**:
  - 移除不再使用的阈值常量与分支。
  - 在函数 docstring 中写明“输入输出契约不变 + 主要判定流程”。
  - 如需，补一条 README 中的实现说明（可选，最小改动）。
- **Input**: v4.4 最终实现
- **Output**: 代码更短、更可维护、行为更可解释
- **Files involved**:
  - `src/podcast_ai/modules/mixing/intro_align.py`
  - `README.md`（可选）
- **Estimated complexity**: S（0.5-1 小时）
