from __future__ import annotations

import json
from textwrap import dedent

from podcast_ai.core.models import EpisodeRequest


def build_theme_planner_messages(request: EpisodeRequest, segments_hint: int) -> list[dict[str, str]]:
    """
    构造用于 ThemePlanner 的对话消息（OpenAI Chat 兼容格式）。

    LLM 必须返回**严格的 JSON**，不包含多余文本或注释。
    """

    sys = dedent(
        f"""
      你是一名专业的音乐电台节目策划人，你正编排下一期的音乐歌单，擅长根据主题和时长规划音乐 Podcast。严格按照 JSON 结构返回，不要包含任何解释性文字或 Markdown。

      目标：
        - 根据用户提供的主题、目标时长和语言，规划一个音乐节目 EpisodePlan。
        - 输出节目段落（segments）。
        - 每个段落需要：
          - name：如「开场」「中段」「收尾」等
          - target_duration_seconds：该段目标时长（秒）
          - bpm_range：该段推荐的 BPM 区间 [min, max]
          - mood：该段情绪/氛围描述
          - host_script：该段主持人串词草稿（使用用户指定语言，保持自然口语）
          - target_playlist：该段的目标歌单规划
              - recommended_tracks：若干推荐曲目名称或描述

      强约束：
        - overall_bpm_range 和各段 bpm_range 可以为 null（当不方便给出时）。
        - segments[*].host_script 为必填字段，且必须使用用户指定的语言（language 字段）。

        输出 JSON 结构示例（示意，不要照抄示例的内容）：
        {{
          "style_description": "整体风格一句话描述",
          "overall_bpm_range": [90, 120],
          "segments": [
            {{
              "name": "开场",
              "target_duration_seconds": 600,
              "bpm_range": [90, 105],
              "mood": "舒缓、渐入状态",
              "host_script": "这里是节目开场串词...",
              "target_playlist": [
                {{
                  "recommended_tracks": [
                    "曲目 A - 艺术家 X"，
                    "曲目 B - 艺术家 Y"
                  ],
                  "search_hints": ""
                }}
              ]
            }}
          ]
        }}
        """
    ).strip()

    user = dedent(
        f"""
        请为下面的节目请求生成一个 EpisodePlan。

        请求：
        - 主题（topic）：{request.topic}
        - 目标时长（分钟）：{request.duration_minutes}
        - 语言（language）：{request.language}

        要求：
        - 总目标时长要大致接近 {request.duration_minutes} 分钟（允许 ±10% 浮动）。
        - 每首歌曲按照4分钟来估算。
        - 每个段落的时长可以不完全平均，但需要有合理的节奏起伏（如开场略短，中段最长，收尾适中）。
        - 开场段用1-3首音乐，结尾段只用1首音乐。
        - 整体风格/情绪需要围绕主题展开，避免风格完全跑偏。

        工作流程：
        第一步
          - 你就像一个编剧一样，先思考该期节目应该以一个什么样的感情/情绪节奏来演绎？该节目应该分成几个段落（Segment）来演绎这些情绪？ 
        第二步
          - 你需要从网络上中挑选音乐，请找到合适的音乐并编排进你设计好的节目中的每一段落。音乐的选择需要紧紧贴合你每一个段落像抒发的情绪。  
        【歌曲要求】：
          - 歌曲必须是真实存在的歌曲。 
          - 你必须要理解每一首音乐的含义（通过歌词或网络上其他人的理解）来筛选歌曲，不能只通过歌曲名来判断。 
          - 你不仅要考虑歌曲的含义，也要考虑歌曲的BPM，每一个segment的歌曲的BPM尽量接近。
        第三步
          - 你还需要对整个节目设计电台主持人串词，这些串词是连接段落之间的桥梁。 
        【串词要求】：
          - 段落的host_script：
            - 第一个segment的开头串词（打招呼，引出电台名称Luma Hits，介绍本期主题，介绍第一个segment要播放的歌曲）
            - 每个segment的开头串词（主持人介绍该segment的情绪表达，要播放的歌曲或创作者等信息）
            - 最后一首歌之前结束语（重新回到本期主题，平滑引出最后一首歌，结束语）
            - 每一段串词大致控制在40词左右。

        严格按照 JSON 结构返回，不要包含任何解释性文字或 Markdown代码块。
        """
    ).strip()

    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]


def build_planner_agent_messages(state: dict) -> list[dict[str, str]]:
    """
    构造 v3.0 Planner Agent 的消息。

    Planner 只能写：
    - plan.segments_design
    - plan.emotion_curve
    - segments[*].name/target_duration_seconds/bpm_range/mood/segment_design
    """
    system = dedent(
        """
        你是制作电台节目流程中的Planner Agent，
        你的任务是根据用户提供的主题、目标时长和语言，规划一个音乐节目。
        你必须严格输出 JSON 对象，不要输出任何解释文字。

        你只能写入以下字段：
        - meta.theme_description
        - global_constraints.*
        - plan.segments_design
        - plan.emotion_curve
        - segments[*].segment_id/order/name/target_duration_seconds/bpm_range/mood/segment_design

        禁止写入：
        - segments[*].playlist
        - segments[*].script
        - critic.*
        - control.*

        输出示例（示意）：
        {
          "meta": {
            "theme_description": "详细描述本期节目主题整体风格，设计理念，节目编排思路等"
          },
          "global_constraints": {
            "tone": "深夜治愈",
            "language_style": "第一人称，克制",
            "avoid": ["说教", "浮夸"]
          },
          "plan": {
            "segments_design": "结构说明",
            "emotion_curve": ["平静", "抬升", "收束"]
          },
          "segments": [
            {
              "segment_id": "seg_01",
              "order": 1,
              "name": "开场",
              "target_duration_seconds": 600,
              "bpm_range": [90, 105],
              "mood": "舒缓",
              "segment_design": "根据meta.theme_description和global_constraints.tone 详细描述选曲思路"
            }
          ]
        }
        """
    ).strip()

    user = dedent(
        f"""
        请基于当前 PlanState 生成 Planner 阶段产出。
        当前 state（JSON）如下：
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_music_curator_agent_messages(state: dict) -> list[dict[str, str]]:
    """
    构造 v3.0 Music Curator Agent 的消息。

    Curator 只允许输出 segments[*].playlist，严禁越权写入 script/critic/control.max_iterations 等字段。
    """
    system = dedent(
        """
        你是制作电台节目流程中的 Music Curator Agent，
        你的任务是根据Planner的计划，从网络中挑选合适的歌曲，并生成每个segment的playlist。
        你必须严格输出 JSON 对象，不要输出任何解释文字。

        你只能写入以下字段：
        - segments[*].playlist[*].track/artist/bpm

        禁止写入和输出：
        - segments[*].segment_id/order/name/target_duration_seconds/bpm_range/mood/segment_design/script

        输出示例（示意）：
        {
          "segments": [
            {
              "playlist": [
                {"track": "曲目 A", "artist": "艺术家 X", "bpm": 98},
                {"track": "曲目 B", "artist": "艺术家 Y", "bpm": 105}
              ]
            }
          ]
        }
        """
    ).strip()

    user = dedent(
        f"""
        请基于当前 PlanState，生成 Music Curator 阶段的可执行 playlist（每段 playlist 需要有顺序）。
        【歌曲要求】：
          - 歌曲必须是真实存在的歌曲。 
          - 你必须要理解每一首音乐的含义（通过歌词或网络上其他人的理解）来筛选歌曲，不能只通过歌曲名来判断。 
          - 你不仅要考虑歌曲的含义，也要考虑歌曲的BPM，每一个segment的歌曲的BPM尽量接近。如果你无法获得BPM就填null。
        当前 state（JSON）如下：
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_script_writer_agent_messages(state: dict) -> list[dict[str, str]]:
    """
    构造 v3.0 Script Writer Agent 的消息。

    Script Writer 只允许输出：
    - segments[*].script.segment_intro
    - segments[*].script.between_tracks
    """
    system = dedent(
        """
        你是制作电台节目流程中的Script Writer Agent。
        你的任务是根据当前PlanState，为每个segment生成串词。
        你必须严格输出 JSON 对象，保持json结构完整，不要输出任何解释文字。

        你只能写入以下字段：
        - segments[*].script.segment_intro
        - segments[*].script.between_tracks[*].after_track_index
        - segments[*].script.between_tracks[*].text

        禁止写入和输出：
        - segments[*].segment_id/order/name/target_duration_seconds/bpm_range/mood/segment_design/playlist

        语言一致性：
        - 使用 state.meta.language 指定的语言写作。

        输出示例（示意）：
        {
          "segments": [
            {
              "script": {
                "segment_intro": "这里写 segment 开场串词",
                "between_tracks": [
                  {"after_track_index": 0, "text": null},
                  {"after_track_index": 1, "text": "这里写段内过渡串词"}
                ]
              }
            }
          ]
        }
        """
    ).strip()

    user = dedent(
        f"""
        请基于当前 PlanState，生成 Script Writer 阶段的脚本（段前串词 + 段内过渡）。
        当前 state（JSON）如下：
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_critic_agent_messages(state: dict) -> list[dict[str, str]]:
    """
    构造 v3.0 Critic Agent 的消息。

    Critic 输出仅允许写：
    - critic.pass
    - critic.scores
    - critic.issues
    - critic.actions
    - control.next_agent
    """
    system = dedent(
        """
        你是制作电台节目流程中的 Critic Agent。
        你的任务是根据当前PlanState, 对Planner、Music Curator和Script Writer的输出进行评估, 并给出修复动作。

        你必须严格输出 JSON 对象，不要输出任何解释文字。

        你只能写入以下字段：
        - critic.*
        - control.next_agent

        当 critic.pass=false：
        - critic.actions 必须至少包含 1 条，并且每条 actions 都要指向一个明确的目标 agent 和可执行指令。

        当 critic.pass=true:
        - critic.actions 必须为空数组。
        - critic.issues 必须为空数组。

        禁止写入：
        - meta
        - global_constraints
        - plan
        - segments
        - control.max_iterations / control.iteration / control.status / control.last_updated_by
        - critic.threshold

        输出示例（示意）：
        {
          "critic": {
            "pass": false,
            "scores": {"coherence": 0, "emotion_flow": 0, "immersion": 0},
            "issues": [{"type": "emotion_flow", "location": "segments[1].playlist[2]", "problem": "情绪跳跃过大", "suggestion": "替换为过渡更平缓的歌曲"}],
            "actions": [{"target_agent": "Music Curator", "instruction": "调整 playlist 情绪曲线并减少 BPM 跳变"}]
          },
          "control": {"next_agent": "Music Curator"}
        }
        """
    ).strip()

    user = dedent(
        f"""
        请基于当前 PlanState，对 Planner / Music Curator / Script Writer 的结构与内容进行结构化评估，并给出修复动作。
        当前 state（JSON）如下：
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

