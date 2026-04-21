# Tasks：theme 模块 Agent 文件统一（v3.0 代码结构）

本文档为**唯一执行方案**，按顺序完成；完成后删除本文档中已过时说明或保留作归档均可。

---

## 目标

将 `src/podcast_ai/modules/theme/` 下阶段一多 Agent 实现统一为：**每个 Agent 独占一个 Python 文件，且该文件内包含「Agent 类 + 对应 sanitize/校验逻辑」**。移除聚合文件 `agents.py`、移除 `sanitizers/` 包、移除仅含 sanitize 的旧 `music_curator.py` / `script_writer.py`，并将 `critic.py` 重命名为 `critic_agent.py` 以保持命名一致。

---

## Task 1：新建 `planner_agent.py`

1. 新建 `src/podcast_ai/modules/theme/planner_agent.py`。
2. 将 `agents.py` 中的 `PlannerAgent` 类**完整**迁入该文件（含 `run` 内 LLM 调用、`merge_plan_state`、`last_updated_by` 等逻辑不变）。
3. 将 `sanitizers/planner.py` 中的 `sanitize_planner_patch` 及模块内常量 `_PLANNER_SEGMENT_ALLOWED_KEYS` 迁入**同一文件**（放在类上方或下方均可，保持可读即可）。
4. `PlannerAgent.run` 仅调用本文件内的 `sanitize_planner_patch`，不得再 `from podcast_ai.modules.theme.sanitizers...`。
5. 文件顶部 import 保持与迁出代码一致：`json`、`AIServiceError`、`Settings`/`load_settings`、`LLMClient`/`get_default_llm_client`、`build_planner_agent_messages`、`PlanState`/`assert_plan_state_valid`/`merge_plan_state`。

**验收**：`pytest tests/test_planner_agent.py` 在更新 import 前可暂红；Task 7 后全绿。

---

## Task 2：新建 `music_curator_agent.py`

1. 新建 `src/podcast_ai/modules/theme/music_curator_agent.py`。
2. 将 `agents.py` 中的 `MusicCuratorAgent` 类迁入该文件。
3. 将当前 `music_curator.py` 中的 `_sanitize_curator_patch` 及 `_CURATOR_SEGMENT_ALLOWED_KEYS`、`_PLAYLIST_ALLOWED_KEYS` 迁入**同一文件**。
4. 删除 `MusicCuratorAgent.run` 内的 `from podcast_ai.modules.theme.music_curator import _sanitize_curator_patch`，改为调用本文件中的 `_sanitize_curator_patch`。
5. 移除迁入后不再使用的 import（原 `music_curator.py` 中未再使用的 `EpisodeRequest`、`LLMClient` 等勿复制进新文件）。

**验收**：逻辑与现 `agents.py` + `music_curator.py` 行为一致；Task 7 后 `pytest tests/test_music_curator_agent.py` 通过。

---

## Task 3：新建 `script_writer_agent.py`

1. 新建 `src/podcast_ai/modules/theme/script_writer_agent.py`。
2. 将 `agents.py` 中的 `ScriptWriterAgent` 类迁入该文件。
3. 将当前 `script_writer.py` 中的 `_sanitize_script_writer_patch`、`_text_matches_language`、正则 `_ZH_RE`/`_EN_RE` 及 `_SCRIPT_*` / `_BETWEEN_*` 常量迁入**同一文件**。
4. 删除 `ScriptWriterAgent.run` 内的 `from podcast_ai.modules.theme.script_writer import _sanitize_script_writer_patch`，改为本文件内调用。
5. 清理无用 import。

**验收**：与现行为一致；Task 7 后 `pytest tests/test_script_writer_agent.py` 通过。

---

## Task 4：重命名 `critic.py` → `critic_agent.py`

1. 将 `src/podcast_ai/modules/theme/critic.py` 重命名为 `src/podcast_ai/modules/theme/critic_agent.py`。
2. 保持 `CriticAgent` 类名与 `_sanitize_critic_patch` 及文件内实现**不变**（已满足「类 + sanitize 同文件」）。
3. 全仓库替换 import：
   - `from podcast_ai.modules.theme.critic import` → `from podcast_ai.modules.theme.critic_agent import`
   - 字符串形式的 mock/patch 路径（若有）同步更新。

**验收**：`grep -r "modules.theme.critic"` 在 `src/` 与 `tests/` 下无残留（除注释或文档若存在则一并改）。

---

## Task 5：更新 `orchestrator.py`

1. 打开 `src/podcast_ai/modules/theme/orchestrator.py`。
2. 删除：`from podcast_ai.modules.theme.agents import MusicCuratorAgent, PlannerAgent, ScriptWriterAgent`。
3. 新增：
   - `from podcast_ai.modules.theme.planner_agent import PlannerAgent`
   - `from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent`
   - `from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent`
   - `from podcast_ai.modules.theme.critic_agent import CriticAgent`（若 Task 4 已完成，否则暂仍指向旧名会导致失败，须与 Task 4 同提交或紧接提交）。

**验收**：`orchestrator` 模块不依赖 `agents.py`；不新增对 `llm_planner` 的顶层 import。

---

## Task 6：删除废弃文件与包

1. 删除 `src/podcast_ai/modules/theme/agents.py`。
2. 删除目录 `src/podcast_ai/modules/theme/sanitizers/`（含 `planner.py`、`__init__.py`）。
3. 删除 `src/podcast_ai/modules/theme/music_curator.py`。
4. 删除 `src/podcast_ai/modules/theme/script_writer.py`。
5. 全仓库搜索并修复所有对上述路径的引用（import、文档、`__init__.py` 若存在导出）。

**验收**：`grep -r "theme.agents\|theme.sanitizers\|theme.music_curator\|theme.script_writer"` 在代码与测试中无有效引用（`critic` 旧路径已在 Task 4 处理）。

---

## Task 7：更新测试与其它引用

1. `tests/test_planner_agent.py`：`PlannerAgent` 从 `podcast_ai.modules.theme.planner_agent` import。
2. `tests/test_music_curator_agent.py`：`MusicCuratorAgent` 从 `music_curator_agent` import。
3. `tests/test_script_writer_agent.py`：`ScriptWriterAgent` 从 `script_writer_agent` import。
4. `tests/test_theme_orchestrator.py`：四个 Agent 分别从四个 `*_agent` 模块 import（与 `orchestrator` 一致）。
5. 若有测试直接从 `sanitizers.planner` 或 `music_curator` / `script_writer` import sanitize 函数，改为从新模块 import（或改为测 Agent 行为）。
6. 运行全量测试：`pytest`（项目根目录），全部通过。

**验收**：CI/本地 `pytest` 0 failure。

---

## Task 8：sanitize 可见性约定（按本任务固定）

1. `planner_agent.py`：保留 **公开** 函数名 `sanitize_planner_patch`（便于单测与对外 API 一致）。
2. `music_curator_agent.py`、`script_writer_agent.py`、`critic_agent.py`：sanitize 保持 **`_sanitize_*_patch`** 模块私有命名，不额外 re-export。
3. 不在任何 `__init__.py` 中再聚合导出四个 Agent（除非项目已有 `theme` 包 `__init__.py` 统一导出习惯；当前若无则不改）。

---

## Task 9：同步架构文档

1. 打开仓库根目录 `ARCHITECTURE.md`。
2. 将 **§5 项目目录结构** 中 `modules/theme/` 下列项更新为与本任务完成后的真实文件一致：
   - 列出 `planner_agent.py`、`music_curator_agent.py`、`script_writer_agent.py`、`critic_agent.py`
   - 列出 `llm_planner.py`（`ThemePlanner`、v2 单次规划、`PlanState → EpisodePlan`、`v3` 内懒加载 `PlanOrchestrator`）
   - 列出 `orchestrator.py`、`state.py`、`prompts.py`
   - **不得**再列出 `agents.py`、`sanitizers/`、`music_curator.py`、`script_writer.py`、`critic.py`（旧名）。
3. 若 §2 / §4 中提及 Agent 所在文件，改为上述新文件名。
4. 检查 `README.md` / `README2.md` 中若有旧路径示例，一并修正。

**验收**：`ARCHITECTURE.md` 与合并后代码树一致，无幽灵文件。

---

## 依赖关系约束（回归检查）

完成全部任务后须满足：

- `orchestrator.py` 仅 import 四个 `*_agent` 模块 + `state` 等，**不** import `llm_planner`。
- `llm_planner.py` 中 v3 路径仅在方法内 `from podcast_ai.modules.theme.orchestrator import PlanOrchestrator`（懒导入），**禁止**在模块顶层 import `orchestrator`。
- 四个 `*_agent` 模块**不** import `llm_planner`、`orchestrator`、`ThemePlanner`。

---

## 完成定义（Definition of Done）

- [ ] 四个新文件存在且单测通过  
- [ ] `agents.py`、`sanitizers/`、`music_curator.py`、`script_writer.py` 已删除  
- [ ] `critic_agent.py` 存在，`critic.py` 不存在  
- [ ] 全仓库无废弃 import  
- [ ] `pytest` 全绿  
- [ ] `ARCHITECTURE.md` §5（及必要时 §2/§4）已更新  

---

*文档版本：与 repo 当前 theme 重构方案对齐；执行完毕后以代码为准。*
