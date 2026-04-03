## 版本 v3.3（迭代十一：JSON 修复 — 结构闭合与完整性）

基于 PRD v3.3：在 v3.2 已有「标准化 + 子串提取 + 尾随逗号」之上，增加对 **未闭合 `{` / `[`** 的检测与在可安全推断时的 **补全 `}` / `]`**；补全后仍无法 `json.loads` 或存在歧义时，按 v3.2 规则 **明确报错 + debug dump**，不静默写坏结构。四个 Agent 的 `run` 仍统一经增强后的 `repair_and_standardize_json`（或等价入口）。

---

### Task 01 - 结构闭合与完整性算法（栈式配对补全）
- **Task name**: v3.3 - JSON 括号/方括号闭合补全
- **目标**: 在纯文本层面实现「未闭合对象/数组」的检测与最小补全：按从左到右扫描，尊重字符串与转义，维护 `{` `[` 栈；在文本末尾若栈非空，按逆序补全缺失的 `}` / `]`。
- **类型**: backend
- **依赖关系**: 无（可与现有 `json_repair.py` 内新增函数或独立小函数并存）
- **Description**:
  - 在 `src/podcast_ai/modules/theme/json_repair.py` 中新增（或抽取）例如 `close_unclosed_json_structures(text: str) -> str`：
    - 仅在 **字符串外** 统计括号；与 `_extract_first_json_substring` 使用一致的字符串/转义语义，避免在引号内误补。
    - **安全边界**（满足 PRD「可安全推断」）：若扫描中出现 **不匹配的 `}`/`]`**（栈顶与当前闭合不符），视为无法安全修复，**不强行补全**，返回原文本或由调用方走失败路径（与验收标准 3 一致）。
    - 歧义场景（多种补全方式）：最小实现可约定「只追加末尾缺失闭合符、不插入中间字符」；若仍 `json.loads` 失败，交给外层报错。
- **Input**: 已通过 `standardize_llm_json_text` 且已截取到 JSON 子串后的 `str`（或完整候选 JSON 文本）
- **Output**: 可能补全了末尾 `}`/`]` 的 `str`
- **Files involved**:
  - `src/podcast_ai/modules/theme/json_repair.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 02 - 将结构闭合接入 `repair_and_standardize_json` 管道
- **Task name**: v3.3 - 修复管道顺序与行为约定
- **目标**: 固定 v3.3 修复顺序，保证 v3.2 能力不退化：`standardize` → 提取首个 JSON 子串 → 去尾随逗号 → **结构闭合补全** → 返回供 `json.loads`；调用方逻辑不变。
- **类型**: backend
- **依赖关系**: Task 01
- **Description**:
  - 更新 `repair_and_standardize_json`：在现有步骤之后调用 Task 01 的闭合逻辑（注意：**闭合应作用于当前用于解析的那段文本**，避免对整段 raw 误补）。
  - 不引入「静默成功」：若闭合补全后 `json.loads` 仍失败，行为与 v3.2 一致（Agent 抛 `AIServiceError`、`dump_json_repair_debug` 含 `raw` 与 `repaired`）。
  - 可选：在 debug payload 中增加字段标记「是否应用了结构补全」，便于线上排查（非必须，保持最小改动时可省略）。
- **Input**: Agent `raw`
- **Output**: 增强后的修复文本；解析失败仍走原有错误路径
- **Files involved**:
  - `src/podcast_ai/modules/theme/json_repair.py`
  - （只读确认）`planner_agent.py`、`music_curator_agent.py`、`script_writer_agent.py`、`critic_agent.py` 已统一调用 `repair_and_standardize_json`，无需重复改四处若已一致
- **Estimated complexity**: S（1-2 小时）

---

### Task 03 - 单测：结构截断 + v3.2 回归
- **Task name**: v3.3 - `test_json_repair` 扩展与回归
- **目标**: 覆盖 PRD 验收 1–3：缺末尾 `}` / `]` 可修复并成功 `json.loads`；v3.2 用例（code fence、前后杂质、智能引号、尾随逗号）**行为不退化**；歧义或非法结构 **不静默通过**（补全后仍非法则 `json.loads` 失败）。
- **类型**: backend
- **依赖关系**: Task 01、Task 02
- **Description**:
  - 扩展 `tests/test_json_repair.py`（或同级文件）：
    - 用例：`{"a":1` → 补全后可解析；`{"a":[1,2` → 补全 `]}`；嵌套对象截断等。
    - 负例：栈不匹配、引号未闭合导致「无法安全推断」时，不期望仅靠补全变合法（断言 `json.loads(repair_and_standardize_json(raw))` 仍失败或明确不补全策略与 PRD 一致）。
  - 若有 Agent 级集成测试，可增加 1 条 stub LLM 返回「缺结尾括号」的用例，断言能进入后续 sanitize（与验收 4、5 一致时可与现有 `test_theme_agents_json_repair` 合并）。
- **Input**: 构造的 malformed / 截断 JSON 字符串
- **Output**: `pytest` 通过
- **Files involved**:
  - `tests/test_json_repair.py`
  - （可选）`tests/test_theme_agents_json_repair.py`
- **Estimated complexity**: M（2-3 小时）

---

### Task 04（可选）- single_agent 路径确认走增强修复
- **Task name**: v3.3（可选）- `llm_planner` 与多 Agent 同源修复
- **目标**: 若 `ThemePlanner` / `llm_planner.py` 已调用 `repair_and_standardize_json`，确认 v3.3 合并后 **无需改代码** 即自动获得结构闭合；若尚未接入，则补一行与 v3.2 可选 task 一致。
- **类型**: backend
- **依赖关系**: Task 02
- **Description**:
  - 阅读 `src/podcast_ai/modules/theme/llm_planner.py` 中 raw → JSON 路径；已统一则本 task 记为「验收勾选」即可。
- **Input**: N/A
- **Output**: single_agent 与 multi_agent 在 JSON 修复层行为一致（PRD 当前规则侧重多 agent，此项为一致性加固）
- **Files involved**:
  - `src/podcast_ai/modules/theme/llm_planner.py`（按需）
- **Estimated complexity**: XS（≤1 小时）
