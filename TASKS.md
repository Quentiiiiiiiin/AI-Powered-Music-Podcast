## 版本 v3.8（迭代十六：阶段一统一新文件产物）

基于 PRD v3.8：阶段一在 `single_agent` 与 `multi_agent` 下统一输出文件集：
1) 保留 `state` 文件（当前为 `state.json`）；
2) 新增一个以 `episode_id` 命名的新版 JSON 文件（简称“新文件”）；
3) 移除单 agent 历史 `playlist` 文件产物；
4) 本轮仅改阶段一生成/映射/校验/写盘契约，不要求阶段二立即消费该新文件。

---

### Task 01 - 明确“新文件”命名与路径规范（storage 层）
- **Task name**: v3.8 - 新文件路径工具与命名规则
- **目标**: 在存储层明确并固化“以 `episode_id` 命名的新 JSON 文件”路径，避免各处拼路径导致不一致。
- **类型**: backend
- **依赖关系**: 无
- **Description**:
  - 在 `src/podcast_ai/infra/storage/paths.py` 新增（或补充）路径函数，例如：
    - `get_episode_state_snapshot_path(episode_root: Path, episode_id: str) -> Path`
  - 文件名规则固定为：`{episode_id}.json`
  - 放置目录与 `state.json` 的关系需一次性约定（推荐同在 `plans/` 下，便于阶段一产物聚合）。
  - 增加读写 helper（可选但推荐）：
    - `save_episode_state_snapshot(...)`
    - `load_episode_state_snapshot(...)`
- **Input**: `output_dir`、`episode_id`、state 数据
- **Output**: 稳定的新文件路径与写盘入口
- **Files involved**:
  - `src/podcast_ai/infra/storage/paths.py`
- **Estimated complexity**: S（1-2 小时）

---

### Task 02 - 阶段一写盘统一：输出 `state.json` + `episode_id.json`
- **Task name**: v3.8 - `plan_episode` 统一文件产物
- **目标**: 在 `plan_episode` 中，无论单/多 agent 都统一产出两份 JSON（`state.json` 与新文件），并确保内容一致性可追溯。
- **类型**: api
- **依赖关系**: Task 01
- **Description**:
  - 修改 `src/podcast_ai/core/pipeline.py::plan_episode`：
    - 保持现有 `save_state_json(...)`；
    - 新增写盘 `episode_id.json`（由 Task 01 的路径函数生成）；
    - 两个文件均来自同一份内存 state（避免双源漂移）。
  - 返回值同步更新（如需）：让调用方拿到新文件路径，避免 CLI 重新推导。
  - 保持多 agent 行为不变；single agent 与 multi agent 仅在 state 内容上不同，不在文件集合上分叉。
- **Input**: `EpisodeRequest`、`agent_mode`
- **Output**: 阶段一统一 JSON 产物集合
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/infra/storage/paths.py`
- **Estimated complexity**: M（2 小时）

---

### Task 03 - 移除阶段一 `playlist` 文件产物与相关文案
- **Task name**: v3.8 - 清理历史 playlist 产物
- **目标**: 去除单 agent 历史 `playlist` 文件写盘逻辑，避免与 v3.8 统一产物目标冲突。
- **类型**: api
- **依赖关系**: Task 02
- **Description**:
  - 在 `pipeline.plan_episode` 删除 `playlist` 生成与写入代码（当前使用 `get_playlist_markdown_path` + markdown 组装）。
  - 更新 CLI 输出文案，删除“目标歌单（Markdown）”相关输出，改为展示两份 JSON 路径。
  - 清理未使用 import（`get_playlist_markdown_path` 等）与注释，提升可读性。
- **Input**: `plan_episode` 结果
- **Output**: 无 `playlist.md` 产物的统一阶段一输出
- **Files involved**:
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/cli.py`
  - `src/podcast_ai/infra/storage/paths.py`（仅清理引用）
- **Estimated complexity**: S（1-2 小时）

---

### Task 04 - 阶段一契约同步：校验/映射/读取接口一致
- **Task name**: v3.8 - 统一阶段一内部契约
- **目标**: 确保阶段一涉及的“生成、映射、校验、写盘”链路在单/多 agent 下都能按统一文件契约结束，不引入分支特判。
- **类型**: backend
- **依赖关系**: Task 02、Task 03
- **Description**:
  - 保持 `validate_state_conforms_to_schema(...)` 在写盘前统一执行；
  - 如 `ThemePlanner.generate_plan_and_state(...)` / `generate_plan_state(...)` 的返回契约受影响，做最小同步（不引入新模型层）；
  - 明确调用方读取契约：阶段一标准入口应优先面向 `state.json` / `episode_id.json`，避免继续依赖历史 `playlist`。
  - 本轮不改阶段二消费，仅确保阶段一产物契约清晰且可被后续迭代接入。
- **Input**: single/multi agent 阶段一输出
- **Output**: 阶段一统一契约稳定运行
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`
  - `src/podcast_ai/core/pipeline.py`
  - `src/podcast_ai/modules/theme/state.py`
- **Estimated complexity**: M（2 小时）

---

### Task 05 - 测试回归：统一文件集与模式一致性
- **Task name**: v3.8 - 文件产物统一回归测试
- **目标**: 自动化验证 v3.8 验收点：单/多 agent 都输出相同文件类型集合（`state.json` + `episode_id.json`），且不再输出 `playlist.md`。
- **类型**: backend
- **依赖关系**: Task 01-04
- **Description**:
  - 更新/新增测试：
    1) `plan_episode(single_agent)`：存在 `state.json` 与 `episode_id.json`，不存在 `playlist.md`；
    2) `plan_episode(multi_agent)`：同样文件集合；
    3) 新文件命名与 `episode_id` 一致；
    4) 两文件内容字段契约符合预期（至少校验顶层结构与关键字段）。
  - 优先扩展：
    - `tests/test_pipeline.py`
    - 必要时新增 `tests/test_stage1_outputs.py`
- **Input**: stub LLM / tmp_path / 两种 agent_mode
- **Output**: `pytest` 通过，v3.8 产物契约受控
- **Files involved**:
  - `tests/test_pipeline.py`
  - （可选）`tests/test_stage1_outputs.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 06（可选）- README 与命令行帮助同步
- **Task name**: v3.8（可选）- 产物说明文档对齐
- **目标**: 避免文档继续描述旧的 `playlist` 产物，降低使用误解成本。
- **类型**: backend（文档）
- **依赖关系**: Task 03
- **Description**:
  - 更新 `README.md` 的阶段一输出说明：
    - 删除 `playlist.md`
    - 增加 `state.json` + `episode_id.json` 说明与示例路径
  - 同步 `cli.py` 子命令 help 文案。
- **Input**: v3.8 新产物契约
- **Output**: 文档与实际行为一致
- **Files involved**:
  - `README.md`
  - `src/podcast_ai/cli.py`
- **Estimated complexity**: XS（0.5-1 小时）

