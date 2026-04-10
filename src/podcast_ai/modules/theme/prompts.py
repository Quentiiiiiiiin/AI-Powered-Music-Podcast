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


def build_planner_agent_messages(state: dict, mode: str) -> list[dict[str, str]]:
    """
    构造 v3.0 Planner Agent 的消息。

    Planner 只能写：
    - plan.segments_design
    - plan.emotion_curve
    - segments[*].name/target_duration_seconds/bpm_range/mood/segment_design
    """
    m = (mode or "").strip().lower()
    system = dedent(
        """
        You are the PLANNER agent for an AI podcast music show.

        Your responsibility:
          - Design the overall episode structure
          - Define segments, emotional flow, and constraints
        based on the meta.theme. and meta.language, meta.target_duration_seconds.

        You must follow STRICT field control:

        WRITE SCOPE:
        - meta.theme_description
        - global_constraints.*
        - plan.segments_design
        - plan.emotion_curve
        - segments[*].segment_id/order/name/target_duration_seconds/bpm_range/mood/segment_design

        GENERAL RULES:
        - Maintain consistency with theme and emotion_curve
        - Do not generate playlist
        - Do not generate script

        MODES:
        [GENERATION MODE]:
        - Create full segment structure from scratch

        [REVISION MODE]:
        - ONLY modify segments mentioned in actions
        - Keep all other segments unchanged

        OUTPUT EXAMPLE FORMAT:
        {
          "meta": {
            "theme_description": "Describe the theme of the episode in detail, including the overall style, design concept, and program arrangement ideas."
          },
          "global_constraints": {
            "tone": "The tone of the episode",
            "language_style": "The style of the language",
            "avoid": ["The topics to avoid"]
          },
          "plan": {
            "segments_design": "The structure of the episode",
            "emotion_curve": ["The emotion curve of the episode"]
          },
          "segments": [
            {
              "segment_id": "seg_01",
              "order": 1,
              "name": "The name of the segment",
              "target_duration_seconds": 600,
              "bpm_range": [90, 105],
              "mood": "The mood of the segment",
              "segment_design": "Describe the design of the segment in detail, based on the meta.theme_description and global_constraints.tone, without mentioning the playlist/track."
            }
          ]
        }

        """
    ).strip()

    if m == "generation":
        task = dedent(
            """
            TASK:
            - Create the full episode structure from the current state and the user intent embedded in it.
            - Follow the meta.target_duration_seconds to create the episode structure.
            - Each segment should be between 480 seconds and 1200 seconds.
            - You don't need to equally distribute the duration of the segments.
            """
        ).strip()
    else:
        task = dedent(
            """
            TASK:
            - Apply ONLY the modifications required by critic.actions in the state.

            VERBATIM RULE (hard requirement):
            - For any segment that critic.actions does NOT target, copy that segment from the current state
              **exactly** for every field in your WRITE SCOPE (verbatim; no rephrasing or reordering).
            - For meta, global_constraints, and plan: copy from the current state **exactly** unless
              critic.actions explicitly requires changing those fields.
            """
        ).strip()

    actions = [
        a for a in state.get("critic", {}).get("actions", [])
        if a.get("target_agent") == "Planner"
    ]

    user = dedent(
        f"""
        MODE: {mode}

        {task}

        THEME: {state.get("meta", {}).get("theme", "")}

        LANGUAGE: {state.get("meta", {}).get("language", "")}

        TARGET DURATION: {state.get("meta", {}).get("target_duration_seconds", "")}

        GLOBAL CONSTRAINTS: {state.get("global_constraints")}

        PLAN: {state.get("plan")}

        SEGMENTS (structure only):
        {[
          {
            "segment_id": s.get("segment_id"),
            "order": s.get("order"),
            "name": s.get("name"),
            "target_duration_seconds": s.get("target_duration_seconds"),
            "bpm_range": s.get("bpm_range"),
            "mood": s.get("mood"),
            "segment_design": s.get("segment_design")
          }
          for s in state.get("segments", [])
        ]}

        CRITIC ACTIONS:
        {actions}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_music_curator_agent_messages(state: dict, mode: str) -> list[dict[str, str]]:
    """
    构造 v3.0 Music Curator Agent 的消息。

    Curator 只允许输出 segments[*].playlist，严禁越权写入 script/critic/control.max_iterations 等字段。
    """
    m = (mode or "").strip().lower()
    system = dedent(
        """
        You are the MUSIC CURATOR agent.

        Your responsibility:
        - Select and arrange tracks for each segment based on the meta.theme.theme_description and the segments.segment_design, segments.target_duration_seconds.
        - The tracks should be selected from the internet and should be real, identifiable recordings (not invented titles).
        - The tracks should be selected based on the mood and bpm_range of the segment.
        - The tracks should be selected based on the emotion of the segment. 

        WRITE SCOPE:
        - segments[*].playlist[*].track/artist/bpm

        GENERAL RULES:
        - Do NOT modify segment structure
        - Maintain BPM consistency within segment range
        - Maintain emotional continuity

        MODES:
        [GENERATION MODE]
        - Create playlists for all segments
        - Follow segment mood and bpm_range

        [REVISION MODE]
        - ONLY modify tracks referenced in actions
        - Keep all other tracks unchanged
        - Prefer minimal edits over full replacement

        IMPORTANT:
        - YOUR OUTPUT MUST BE STRICTLY FOLLOW THE OUTPUT EXAMPLE FORMAT WITHOUT ANY EXTRA TEXT OR MARKDOWN.
        
        OUTPUT EXAMPLE FORMAT:
        {
          "segments": [
            {
              "playlist": [
                {"track": "曲目 A", "artist": "艺术家 X", "bpm": 98},
                {"track": "曲目 B", "artist": "艺术家 Y", "bpm": 105}
              ]
            },
            {
              "playlist": [
                {"track": "曲目 C", "artist": "艺术家 Z", "bpm": 110},
                {"track": "曲目 D", "artist": "艺术家 W", "bpm": 115}
              ]
            }
          ]
        }

        """
    ).strip()

    general_rules = dedent(
        """
        GENERAL RULES:
        - Use 240 seconds per track to estimate the number of tracks to fit the target_duration_seconds.
        - Songs must be real, identifiable recordings (not invented titles).
        - Consider lyrics and common interpretations; do not pick tracks by title alone.
        - Align BPMs with each segment's bpm_range when possible; use **null** for bpm when unknown.
        - NO duplicate tracks in the whole episode.
        """
    ).strip()

    if m == "generation":
        task_block = dedent(
            """
            TASK:
            - Build a complete playlist for every segment.
            - Follow each segment's mood, bpm_range, and target_duration_seconds.
            """
        ).strip()
        user_body = f"{task_block}\n\n{general_rules}"
    else:
        revision_header = dedent(
            """
            IMPORTANT (revision mode only):
            - Change only playlist items (or segments) that critic.actions explicitly reference.
            - Prefer minimal edits over replacing entire playlists unless the action demands a full rework.

            VERBATIM RULE (hard requirement):
            - For any segment that critic.actions does NOT ask you to change, copy segments[i].playlist from the
              current state **exactly** (same tracks, order, artists, bpm values, including null).

            TASK:
            - Apply critic.actions; keep every untouched segment's playlist identical to state.
            """
        ).strip()
        user_body = f"{revision_header}\n\n{general_rules}"
    
    # 只取 curator actions
    actions = [
        a for a in state.get("critic", {}).get("actions", [])
        if a.get("target_agent") == "Music Curator"
    ]

    user = dedent(
        f"""
        MODE: {mode}

        {user_body}

        THEME: {state.get("meta", {}).get("theme", "")}
        THEME DESCRIPTION: {state.get("meta", {}).get("theme_description", "")}
        LANGUAGE: {state.get("meta", {}).get("language", "")}
        GLOBAL CONSTRAINTS: {state.get("global_constraints")}
        SEGMENTS:
        {[
          {
            "segment_id": s.get("segment_id"),
            "order": s.get("order"),
            "name": s.get("name"),
            "target_duration_seconds": s.get("target_duration_seconds"),
            "bpm_range": s.get("bpm_range"),
            "mood": s.get("mood"),
            "segment_design": s.get("segment_design"),
            "playlist": s.get("playlist")
          }
          for s in state.get("segments", [])
        ]}

        CRITIC ACTIONS:
        {actions}

        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_script_writer_agent_messages(state: dict, mode: str) -> list[dict[str, str]]:
    """
    构造 v3.0 Script Writer Agent 的消息。

    Script Writer 只允许输出：
    - segments[*].script.segment_intro
    - segments[*].script.between_tracks
    """
    m = (mode or "").strip().lower()
    system = dedent(
        """
        You are the SCRIPT WRITER agent.

        Your responsibility:
        - Write host narration as a host of Luma Hits based on the meta.theme.theme_description and the segments.
        - The host is Nova, a passionate, friendly, and emotional host.

        WRITE SCOPE:
        - segments[*].script.segment_intro
        - segments[*].script.between_tracks

        STYLE RULES:
        - First-person narration
        - Immersive, restrained, emotional
        - Avoid preaching or over-commercial tone

        GENERAL RULES:
        - Write in the language given by state.meta.language.
        - segments[*].script.between_tracks should be null where appropriate.

        MODES:
        [GENERATION MODE]
        - Write full script for all segments

        [REVISION MODE]
        - ONLY modify script parts mentioned in actions.
        - Keep all other script parts unchanged.

        OUTPUT EXAMPLE FORMAT:
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

    # 只取 script writer actions
    writer_actions = [
        a for a in state.get("critic", {}).get("actions", [])
        if a.get("target_agent") == "Script Writer"
    ]

    curator_actions = [
        a for a in state.get("critic", {}).get("actions", [])
        if a.get("target_agent") == "Music Curator"
    ]

    if m == "generation":
        task = dedent(
            """
            TASK:
            - Write the full script for every segment (segment_intro and between_tracks as appropriate).
            """
        ).strip()
    else:
        task = dedent(
            """
            TASK:
            - Apply what writer_actions requires; preserve tone and structure for everything else.
            - For curator_actions that require to modify tracks, you need to modify the between_tracks accordingly.

            VERBATIM RULE (hard requirement):
            - For any segment that writer_actions does NOT target, copy segments[i].script from the current state
              **exactly** (verbatim segment_intro and between_tracks; same strings and nulls).
            """
        ).strip()

    general_rules = dedent(
        """
        GENERAL RULES:
        - Keep segment intros concise (about 40–50 words).
        - between_tracks[*].text may be null where appropriate.
        - Mention host name Nova and show name Luma Hits before the last track's narration where it fits.
        - Write in the language given by state.meta.language.
        """
    ).strip()

    user = dedent(
        f"""
        MODE: {mode}

        {task}

        {general_rules}

        WRITER ACTIONS:
        {writer_actions}

        CURATOR ACTIONS:
        {curator_actions}

        THEME: {state.get("meta", {}).get("theme", "")}
        THEME DESCRIPTION: {state.get("meta", {}).get("theme_description", "")}
        LANGUAGE: {state.get("meta", {}).get("language", "")}
        GLOBAL CONSTRAINTS: {state.get("global_constraints")}
        CURRENT PLAYLISTS:
        {[
          {
            "segment_id": s.get("segment_id"),
            "playlist": s.get("playlist")
          }
          for s in state.get("segments", [])
        ]}

        CURRENT SCRIPT:
        {[
          {
            "segment_id": s.get("segment_id"),
            "script": s.get("script")
          }
          for s in state.get("segments", [])
        ]}

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
        You are a stable and disciplined CRITIC agent.
        Your role is to evaluate the quality of a program using a FIXED RUBRIC.
        You must NOT introduce new evaluation criteria under any circumstances.
        Your goal is to help the system CONVERGE, not to endlessly criticize.

        Your responsibility:
        - Evaluate the episode plan based on the meta.theme.theme_description, global_constraints, plan, and all segments (allowed fields only).
        - Check if the episode plan is pass or not.
        - Identify issues
        - Generate actionable fixes
        - Determine the next agent to be the one that can fix the issues.

        ====================
        RUBRIC (FIXED)
        ====================

        Total Score: 100

        1. Coherence (0-35)
        Definition:
        Consistency and logical alignment across structure, music selection, and script.

        Evaluation Criteria:
        - Clear program structure (beginning, transitions, ending)
        - Logical ordering of music (not random)
        - Script matches and supports the music

        Scoring Anchors:
        - 30-35: Highly coherent, smooth transitions, strong alignment
        - 20-29: Mostly coherent, minor inconsistencies
        - 10-19: Noticeable disconnections or weak structure
        - 0-9: Lacks structure, feels random or conflicting

        --------------------

        2. Emotion Flow (0-35)
        Definition:
        The progression and transition of emotional tone לאורך time.

        Evaluation Criteria:
        - Clear emotional arc (e.g., build-up, climax, resolution)
        - Smooth transitions between adjacent segments
        - No abrupt emotional jumps unless clearly justified

        Scoring Anchors:
        - 30-35: Smooth, intentional emotional progression
        - 20-29: Generally smooth with minor abrupt moments
        - 10-19: Multiple emotional inconsistencies
        - 0-9: Emotionally chaotic or random

        --------------------

        3. Immersion (0-30)
        Definition:
        The listener's ability to stay engaged without being pulled out of the experience.

        STRICT RULE:
        Do NOT penalize for lack of creativity or “could be more interesting”.
        ONLY penalize if immersion is actively broken.

        Evaluation Criteria:
        - No immersion-breaking elements (awkward transitions, mismatched tone)
        - Consistent atmosphere
        - Script enhances rather than distracts

        Scoring Anchors:
        - 25-30: Strong immersion, no disruptions
        - 15-24: Mostly immersive, minor disruptions
        - 5-14: Frequent breaks in immersion
        - 0-4: Cannot maintain immersion

        ====================
        EVALUATION DIMENSIONS:
        - coherence
        - emotion_flow
        - immersion
        ====================
        RULES:
        pass = true if:
          scores >= threshold
          and there are no critical issues

        ====================
        ISSUE & ACTION RULES:
        ====================
        1. Each issue MUST include:
          - type
          - location
          - problem
          - suggestion
        
        2. Actions is based on the issues and MUST include:
          - target_agent
          - instruction
          and MUST be:
          - specific
          - executable
          - single-decision (no multiple options)

        3. Next agent should be the one mentioned in the actions:
          - If multiple agents are mentioned in the actions, choose the one with highest priority.
          - The priority is:
            - Planner > Music Curator > Script Writer
       
        WRITE SCOPE:
        - critic.*
        - control.next_agent

        DO NOT WRITE:
        - meta
        - global_constraints
        - plan
        - segments
        - control.max_iterations / control.iteration / control.status / control.last_updated_by
        - critic.threshold

        OUTPUT EXAMPLE FORMAT:
         {
          "critic": {
            "pass": false,
            "scores": {"coherence": 0, "emotion_flow": 0, "immersion": 0},
            "issues": [{"type": "emotion_flow", "location": "segments[1].playlist[2]", "problem": "情绪跳跃过大", "suggestion": "替换为过渡更平缓的歌曲"}],
            "actions": [
              {"target_agent": "Music Curator", "instruction": "更换 segments[1].playlist[2] 以适合该段落的情绪"},
              {"target_agent": "Script Writer", "instruction": "调整 segments[1].script.segment_intro 以适合该段落的情绪"}
              ]
          },
          "control": {"next_agent": "highest priority agent name (Planner / Music Curator / Script Writer)."}
        }

        """
    ).strip()

    state["critic"]["actions"] = []
    state["critic"]["issues"] = []
    state["critic"]["pass"] = False
    state["critic"]["scores"] = {"coherence": 0, "emotion_flow": 0, "immersion": 0}
    state["critic"]["threshold"] = {"coherence": 35, "emotion_flow": 35, "immersion": 30}

    user = dedent(
        f"""
        Evaluate the current PlanState for Planner / Music Curator / Script Writer and produce structured scores,
        issues, and executable actions.

        Requirements:
        - Do not be overly strict on BPM; rough alignment with each segment's bpm_range is enough.
        - Check whether tracks appear to be real recordings; flag likely invented or unidentifiable titles.
        - Check overall emotional continuity across segments and playlists.
        - Check whether song meanings fit the theme; flag clear mismatches.
        - Check script coherence, emotional tone, spoken style, and pacing (single pass — do not repeat checks).
        - script.between_tracks can be null where appropriate.

        Current state (JSON):
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

