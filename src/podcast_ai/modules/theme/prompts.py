from __future__ import annotations

import json
from textwrap import dedent

from podcast_ai.core.models import EpisodeRequest


def build_theme_planner_messages(request: EpisodeRequest, segments_hint: int) -> list[dict[str, str]]:
    """
    构造用于 ThemePlanner 的对话消息（OpenAI Chat 兼容格式）。

    LLM 必须返回**严格的 JSON**，不包含多余文本或注释。
    """

    EXAMPLE_OUTPUT = dedent(
        """
        {
          "schema_version": "v3.0",
          "meta": {
            "request_id": "4bd9d522-40cb-4123-8c2e-f60710d547a2",
            "theme": "",
            "theme_description": "Describe the theme of the episode in detail, including the overall style, design concept, and program arrangement ideas.",
            "language": "en-US",
            "target_duration_seconds": 3600,
            "overall_bpm_range": [The minimum BPM of the episode, The maximum BPM of the episode]
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
              "target_duration_seconds": 660,
              "bpm_range": [The minimum BPM of the segment, The maximum BPM of the segment],
              "mood": "The mood of the segment",
              "segment_design": "Describe the design of the segment in detail, based on the meta.theme_description and global_constraints.tone, without mentioning the playlist/track.",
              "playlist": [
                {
                  "track": "The track name",
                  "artist": "The artist name",
                  "bpm": The BPM of the track
                },
                {
                  "track": "The track name",
                  "artist": "The artist name",
                  "bpm": The BPM of the track
                }
              ],
              "script": {
                "segment_intro": "The intro of the segment, host script for the segment",
                "between_tracks": [
                  {
                    "after_track_index": 0,
                    "text": "The text between the tracks"
                  }
                ]
              }
            }
          ]
        }
        """
    ).strip()

    sys = dedent(
        f"""
      You are a professional music podcast episode planner.
      You are constructing a complete episode plan for a music podcast called Luma Hits.
      The host of the podcast is Nova.
      Your responsibility is to generate a complete episode plan based on the user request.
      You must output a JSON object that strictly follows the schema below.

      OUTPUT EXAMPLE FORMAT:
      {EXAMPLE_OUTPUT}

        """
    ).strip()

    user = dedent(
        f"""
        YOUR RESPONSIBILITY: Generate a complete episode plan based on the user request.

        USER REQUEST:
        THEME: {request.topic}
        TARGET DURATION SECONDS: {request.duration_minutes * 60}
        LANGUAGE: {request.language}

        GENERAL RULES:
        - The total target duration should be roughly close to TARGET DURATION SECONDS (allow ±10% floating).
        - Each song should be estimated to be 4 minutes.
        - The duration of each segment can be uneven, but it needs to have a reasonable rhythm
        - The overall style/emotion should be based on the theme.

        WORKFLOW:
         1. Construct the structure of the episode based on the theme and the target duration seconds.
         2. Select music from the internet, and arrange it into each segment of the episode. The music should be tightly connected to the emotion of each segment.  
         3. Design the host script for the entire episode, including segment intro and between tracks script.

        MUSIC REQUIREMENTS:
          - The music should be real, identifiable recordings (not invented titles).
          - You should understand the meaning of each song (through lyrics or other people's understanding on the internet) to select the music, not just by the song name.
        
        HOST SCRIPT REQUIREMENTS:
          - The first segment's host script should introduce the podcast name Luma Hits, the theme of the episode, and the first segment's music.
          - The host script for each segment should introduce the segment's emotion, the music or artist information.
          - The host script before the last song should introduce the podcast name Luma Hits, the theme of the episode, and the last segment's music.
          - The segment intro should be around 50 words, while the between tracks script should be shorter.
          - Segment intro is compulsory, between tracks script is optional, keep the flow of the episode naturally.
          
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
        - No ASCII " inside any JSON string. Use 「」 instead.
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
        - No ASCII " inside any JSON string. Use 「」 instead.
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
        You are the host of Luma Hits, a music podcast.
        Your name is Nova, a passionate, friendly, energetic and emotional host.
        Your responsibility is to write host scripts based on the meta.theme.theme_description and the segments.segment_design. 

        WRITE SCOPE:
        - segments[*].script.segment_intro
        - segments[*].script.between_tracks

        STYLE RULES:
        - First-person narration
        - Immersive, restrained, emotional
        - Avoid preaching or over-commercial tone

        GENERAL RULES:
        - Write in the language given by state.meta.language.
        - No ASCII " inside any JSON string. Use 「」 instead.
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


def build_critic_agent_messages(state: dict, mode: str) -> list[dict[str, str]]:
    """
    构造 v3.0 Critic Agent 的消息。

    Critic 输出仅允许写：
    - critic.pass
    - critic.scores
    - critic.issues
    - critic.actions
    - control.next_agent
    """

    RUBRIC = dedent(
        """
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
        """
    ).strip()

    m = (mode or "").strip().lower()

    if m == "generation":
        RESPONSIBILITY = dedent(
          """
          ====================
          GENERATION MODE
          ====================
          Your responsibility:
          - Evaluate the overall quality of the episode plan based on the RUBRIC.
          - Check if the episode plan is PASS or not.
          - Identify ISSUES as many as possible.
          - Generate ACTIONS based on the issues.
          - Determine the NEXT AGENT to be the one that can fix the issues.

          ====================
          ISSUE & ACTION RULES:
          ====================
         Each ISSUE MUST include:
          - type
          - location
          - ONE problem
          - ONE suggestion
        
         Actions is based on the issues and MUST include:
          - target_agent
          - instruction (specific, executable, single-decision)
         If an issue requires multiple agents to fix, you need to generate multiple actions.

         Next agent should be the one mentioned in the actions:
          - If multiple agents are mentioned in the actions, choose the one with highest priority.
          - The priority is:
            - Planner > Music Curator > Script Writer
          ====================
          """
        ).strip()
    else:
        RESPONSIBILITY = dedent(
          """
          ====================
          REVISION MODE
          ====================
          Your responsibility:
          - Assess the remaining ISSUES to see if they are resolved, drop the resolved ISSUES, keep the unresolved ones.
          - DO NOT generate new ISSUES or modify the existing ISSUES. You can only drop the resolved ISSUES.
          - Generate ACTIONS based on the ISSUES.
          - Evaluate the overall quality of the episode plan based on the RUBRIC. The score should be higher than the current score if the number of ISSUES is reduced.
          - Determine the NEXT AGENT to be the one that can fix the ISSUES.
          ====================
          ACTION RULES:
          ====================
          Each ACTION MUST include:
          - target_agent
          - instruction
          and MUST be:
          - specific
          - executable
          - single-decision (no multiple options)

          Next AGENT should be the one mentioned in the actions:
          - If multiple agents are mentioned in the actions, choose the one with highest priority.
          - The priority is:
            - Planner > Music Curator > Script Writer
          ====================
          """
        ).strip()

    EXAMPLE_OUTPUT = dedent(
        """
        {
          "critic": {
            "pass": false,
            "scores": {"coherence": 0, "emotion_flow": 0, "immersion": 0},
            "issues": [{"type": "emotion_flow", "location": "segments[1].playlist[2]", "problem": "情绪跳跃过大", "suggestion": "替换为过渡更平缓的歌曲"}],
            "actions": [
              {"target_agent": "Music Curator", "instruction": "替换segments[1].playlist[2] 为更平缓的歌曲"},
              {"target_agent": "Music Curator", "instruction": "删除segments[1].playlist[4] 以缩短时长"}
              ]
          },
          "control": {"next_agent": "highest priority agent name (Planner / Music Curator / Script Writer)."}
        }
        """
    ).strip()

    system = dedent(
        f"""
        GENERAL RULES:
        You are a stable and disciplined CRITIC agent for the Music Podcast.
        Your role is to improve the quality of the Music Podcast EpisodePlan.
        You must NOT introduce new evaluation criteria under any circumstances.
        Your goal is to help the system CONVERGE, not to endlessly criticize.
        No ASCII " inside any JSON string. Use 「」 instead.

        MODE: {mode}

        YOUR RESPONSIBILITY:
        {RESPONSIBILITY}

        EVALUATION DIMENSIONS:
        - coherence
        - emotion_flow
        - immersion

        RUBRIC:
        {RUBRIC}

        PASS RULES:
        pass = true if:
          scores >= threshold
       
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
        {EXAMPLE_OUTPUT}

        """
    ).strip()

    state["critic"]["actions"] = []

    user = dedent(
        f"""
        Evaluate the current PlanState for Planner / Music Curator / Script Writer and produce structured scores,
        issues, and executable actions.

        Requirements:
        - Do not be overly strict on BPM; rough alignment with each segment's bpm_range is enough.
        - Check whether tracks appear to be real recordings; flag likely invented or unidentifiable titles.
        - Check whether song meanings fit the theme; flag clear mismatches.
        - Check script coherence, tone, style, and pacing, make sure they look like a real radio host.
        - Check overall script length, it should be reasonable and not too long or too short.
        - Mentioning host name Nova and show name Luma Hits in the script in where it fits.
        - script.between_tracks can be null where appropriate, keep the flow of the episode naturally.

        Current state (JSON):
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

