# TASKS.md — 版本 v1.1（迭代一：主持串词插入时序修正）

基于 PRD 迭代记录「v1.1（迭代一：主持串词插入时序修正）」：串词插入在**每个 segment 之前**，整期开头先串词再播音乐；时间线顺序为 **串词1 → segment 1 → 串词2 → segment 2 → … → 串词N → segment N**；主持期间不播背景音乐；歌曲-歌曲转场保留 crossfade。

---

## Task 01 - Voiceover 与 segment 一一对应（含无串词占位）

- **目标**：保证主持列表与 `plan.segments` 一一对应，顺序固定为 串词1→segment1→串词2→segment2→…；对**无** `host_script` 的 segment 输出占位（静音或零时长），使 `len(voiceovers) == len(plan.segments)`，混音可按索引对齐「串词_i → 组_i」。
- **类型**：backend
- **依赖**：无
- **涉及文件**：`src/podcast_ai/modules/voiceover/tts_service.py`

---

## Task 02 - Mixer 时间线改为「串词1→组1→…→串词N→组N」

- **目标**：时间线严格为 串词1 → segment1 的歌曲组 → 串词2 → segment2 的歌曲组 → … → 串词N → segmentN 的歌曲组。将 `selected_tracks` 按 `N = len(voiceovers)` 均分为 N 组，按顺序拼接；组内歌曲 crossfade，组与串词之间不 crossfade，主持期间无背景音乐。若某段主持为占位（静音/零长），照常占时间线位置或拼静音，保持顺序不变。
- **类型**：backend
- **依赖**：无（与 Task 01 配合后顺序与 PRD 一致）
- **涉及文件**：`src/podcast_ai/modules/mixing/mixer.py`

---

## Task 03 - 单元测试：v1.1 顺序与无重叠

- **目标**：验证整期开头为串词1、顺序为 串词_i → segment_i 的歌曲、串词与歌曲不重叠、相邻 segment 之间的歌曲-歌曲转场仍为 crossfade、总时长在既有容差内。
- **类型**：backend
- **依赖**：Task 01、Task 02
- **涉及文件**：`tests/test_mixing.py`（及如需 `tests/conftest.py`）

---

## 建议里程碑（当前迭代）

| 里程碑   | 内容 |
|----------|------|
| v1.1-M0  | Task 01 + Task 02 完成，现有混音/流水线测试通过 |
| v1.1-M1  | Task 03 通过，满足 PRD v1.1 验收标准 |
