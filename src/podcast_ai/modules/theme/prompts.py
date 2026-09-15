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
          "schema_version": "v4.0",
          "meta": {
            "request_id": "4bd9d522-40cb-4123-8c2e-f60710d547a2",
            "theme": "",
            "theme_description": "Describe the theme of the episode in detail.",
            "language": "en-US",
            "target_duration_seconds": 3600,
            "theme_type": "",
            "theme_subject": "",
            "theme_relationship": ""
          },
          "global_constraints": {
            "energy_strategy": "How energy varies across the episode",
            "sonic_world": ["sonic label A", "sonic label B"],
            "avoid": ["topics or styles to avoid"]
          },
          "plan": {
            "segment_count": 3,
            "episode_direction": "Overall narrative and energy arc",
            "segments_design": "High-level segment roles and connections"
          },
          "segments": [
            {
              "segment_id": "seg_01",
              "order": 1,
              "name": "The name of the segment",
              "target_duration_seconds": 660,
              "narrative_function": "What this segment does in the story",
              "scene": "Scene imagery",
              "sonic_direction": ["sonic cue"],
              "lyrical_direction": ["lyric theme"],
              "anchor_tracks": [{"track": "Track", "artist": "Artist", "required": true}],
              "reference_material": [{"track": "Ref", "artist": "Artist", "purpose": "why"}],
              "sequence_direction": [{"phase": "open", "function": "establish", "musical_direction": "spacious"}],
              "transition_to_next": "How to hand off to the next segment",
              "playlist": [
                {
                  "track": "The track name",
                  "artist": "The artist name",
                  "selection_reason": "Why this recording fits",
                  "sequence_role": "Role in the sequence",
                  "planner_alignment": ["How it fulfills sonic_direction"],
                  "transition_logic": "Why it connects to neighbors"
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
    构造 Planner Agent 消息（v6.1 / Schema_Planner_v4；legacy 与 staged 共用字段契约）。
    """
    m = (mode or "").strip().lower()
    system = dedent(
        """
        You are the PLANNER agent for an AI podcast music show (Schema_Planner_v4).

        Your responsibility:
          - Design the overall episode structure
          - Define segment narrative, sonic world, and constraints
        based on meta.theme, meta.language, meta.target_duration_seconds.

        You must follow STRICT field control:

        WRITE SCOPE:
        - meta.theme_description / theme_type / theme_subject / theme_relationship
        - global_constraints.energy_strategy / sonic_world / avoid
        - plan.segment_count / episode_direction / segments_design
        - segments[*].segment_id/order/name/target_duration_seconds/
          narrative_function/scene/sonic_direction/lyrical_direction/
          anchor_tracks/reference_material/sequence_direction/transition_to_next

        GENERAL RULES:
        - Maintain consistency with theme and energy_strategy
        - No ASCII " inside any JSON string. Use 「」 instead.
        - Do not generate playlist
        - Do not generate script
        - Do not write bpm_range / mood / segment_design / emotion_curve

        MODES:
        [GENERATION MODE]:
        - Create full segment structure from scratch

        [REVISION MODE]:
        - ONLY modify segments mentioned in actions
        - Keep all other segments unchanged

        OUTPUT EXAMPLE FORMAT:
        {
          "meta": {
            "theme_description": "Detailed episode blueprint",
            "theme_type": "",
            "theme_subject": "",
            "theme_relationship": ""
          },
          "global_constraints": {
            "energy_strategy": "How energy is maintained or varied",
            "sonic_world": ["cinematic electronic"],
            "avoid": ["generic festival EDM"]
          },
          "plan": {
            "segment_count": 3,
            "episode_direction": "Overall narrative arc",
            "segments_design": "High-level roles of each segment"
          },
          "segments": [
            {
              "segment_id": "seg_01",
              "order": 1,
              "name": "Opening",
              "target_duration_seconds": 600,
              "narrative_function": "Establish the world",
              "scene": "Night city dashboard glow",
              "sonic_direction": ["deep low-end", "controlled percussion"],
              "lyrical_direction": ["movement", "confidence"],
              "anchor_tracks": [{"track": "Lose My Mind", "artist": "Don Toliver", "required": true}],
              "reference_material": [{"track": "Sweet Dreams", "artist": "Eurythmics", "purpose": "hypnotic repetition"}],
              "sequence_direction": [
                {"phase": "arrival", "function": "establish", "musical_direction": "spacious and cinematic"}
              ],
              "transition_to_next": "Increase propulsion without resetting the sonic world"
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
            "narrative_function": s.get("narrative_function"),
            "scene": s.get("scene"),
            "sonic_direction": s.get("sonic_direction"),
            "lyrical_direction": s.get("lyrical_direction"),
            "anchor_tracks": s.get("anchor_tracks"),
            "reference_material": s.get("reference_material"),
            "sequence_direction": s.get("sequence_direction"),
            "transition_to_next": s.get("transition_to_next"),
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
    构造 Music Curator Agent 消息（v6.1 / Schema_Music-Curator_v4）。

    Curator 只允许输出 segments[*].playlist（含解释字段），严禁越权写入 script/critic/control 等。
    """
    m = (mode or "").strip().lower()
    system = dedent(
        """
        You are the MUSIC CURATOR agent (Schema_Music-Curator_v4).

        Your responsibility:
        - Select and arrange tracks for each segment based on meta.theme_description and
          segments[*].narrative_function / scene / sonic_direction / sequence_direction /
          anchor_tracks / lyrical_direction / target_duration_seconds.
        - Tracks must be real, identifiable recordings (not invented titles).

        WRITE SCOPE (each playlist item):
        - track, artist
        - selection_reason, sequence_role, planner_alignment[], transition_logic
        - Do NOT write bpm

        GENERAL RULES:
        - Do NOT modify segment structure
        - No ASCII " inside any JSON string. Use 「」 instead.
        - Fill planner_alignment with concrete links to Planner sonic/sequence/anchor guidance
        - Maintain continuity across the episode

        MODES:
        [GENERATION MODE]
        - Create playlists for all segments

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
                {
                  "track": "曲目 A",
                  "artist": "艺术家 X",
                  "selection_reason": "Why this recording fits musically and thematically",
                  "sequence_role": "What this track does at this point",
                  "planner_alignment": ["Matches sonic_direction deep low-end"],
                  "transition_logic": "Why it connects to neighbors"
                }
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
        - Do NOT include bpm on playlist items.
        - NO duplicate tracks in the whole episode.
        """
    ).strip()

    if m == "generation":
        task_block = dedent(
            """
            TASK:
            - Build a complete playlist for every segment.
            - Follow each segment's Planner guidance and target_duration_seconds.
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
              current state **exactly** (same tracks, order, artists, explanation fields).

            TASK:
            - Apply critic.actions; keep every untouched segment's playlist identical to state.
            """
        ).strip()
        user_body = f"{revision_header}\n\n{general_rules}"

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
            "narrative_function": s.get("narrative_function"),
            "scene": s.get("scene"),
            "sonic_direction": s.get("sonic_direction"),
            "lyrical_direction": s.get("lyrical_direction"),
            "anchor_tracks": s.get("anchor_tracks"),
            "sequence_direction": s.get("sequence_direction"),
            "transition_to_next": s.get("transition_to_next"),
            "playlist": s.get("playlist"),
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
        Your responsibility is to write host scripts based on the meta.theme_description and the segments.narrative_function / scene. 

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
            - You should see the playlist as a whole, and write the script at where it fits naturally. You don't need to write the script between every two tracks.
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
        - Mention host name Nova and show name Luma Hits before the last track's narration where it fits.
        - Write in the language given by state.meta.language.
        - You can add extra information related to album, artist, or story for either previous or next track, making it more informative and immersive.
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
    构造 v6.4 Critic Agent 消息。

    模型仅输出：overall_score / scores(七维) / issues / actions。
    pass、threshold、control.next_agent 由系统规则派生，禁止模型填写。
    """

    RUBRIC = dedent(
        """
        ====================
        RUBRIC (FIXED) — each dimension 0–100
        ====================

        1. theme_definition
        Clarity of theme type / subject / relationship in meta and plan.

        2. theme_relationship
        How well music and script serve the stated theme relationship.

        3. musical_concept
        Coherence of sonic world, energy strategy, and playlist concept.

        4. segment_differentiation
        Segments are distinct in function/scene without redundant sameness.

        5. sequence_narrative
        Ordering and transitions form a listen-through narrative arc.

        6. curator_actionability
        Planner directions are concrete enough for Music Curator to execute.

        7. creative_freedom
        Directions leave room for tasteful curation (not over-constrained).

        overall_score: integer 0–100 summarizing overall episode quality.
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
            - Score ALL seven dimensions (0–100 integers).
            - Set overall_score (0–100).
            - List ISSUES (as many as warranted).
            - Generate ACTIONS from issues (executable, single-decision).
            - Do NOT decide pass / threshold / next_agent (system derives them).

            ====================
            ISSUE & ACTION RULES
            ====================
            Each ISSUE MUST include:
            - type
            - severity: "minor" | "critical"
            - location
            - problem
            - listener_impact
            - suggestion

            Each ACTION MUST include:
            - target_agent: Planner | Music Curator | Script Writer
            - location
            - instruction (specific, executable, single-decision)

            If an issue needs multiple agents, emit multiple actions.
            System will pick next repair agent from actions by priority:
            Planner > Music Curator > Script Writer
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
            - Re-check ISSUES from last iteration; drop resolved ones; keep unresolved.
            - Do NOT invent brand-new issue types beyond unresolved carry-over + clear regressions.
            - Generate ACTIONS from remaining ISSUES.
            - Re-score seven dimensions (0–100) and overall_score.
            - Do NOT decide pass / threshold / next_agent.
            ====================
            ACTION RULES
            ====================
            Each ACTION MUST include target_agent, location, instruction
            and be specific / executable / single-decision.
            ====================
            """
        ).strip()

    EXAMPLE_OUTPUT = dedent(
        """
        {
          "critic": {
            "overall_score": 62,
            "scores": {
              "theme_definition": 75,
              "theme_relationship": 70,
              "musical_concept": 72,
              "segment_differentiation": 68,
              "sequence_narrative": 55,
              "curator_actionability": 74,
              "creative_freedom": 80
            },
            "issues": [{
              "type": "sequence_narrative",
              "severity": "critical",
              "location": "segments[1].playlist[2]",
              "problem": "情绪跳跃过大",
              "listener_impact": "听众感到断档",
              "suggestion": "替换为过渡更平缓的歌曲"
            }],
            "actions": [{
              "target_agent": "Music Curator",
              "location": "segments[1].playlist[2]",
              "instruction": "替换segments[1].playlist[2] 为更平缓的歌曲"
            }]
          }
        }
        """
    ).strip()

    system = dedent(
        f"""
        GENERAL RULES:
        You are a stable and professional CRITIC agent for the Music Podcast.
        Your role is to improve quality via structured scores, issues, and actions.
        You must NOT introduce new evaluation dimensions under any circumstances.
        Your goal is to help the system CONVERGE, not to endlessly criticize.
        No ASCII " inside any JSON string. Use 「」 instead.

        MODE: {mode}

        YOUR RESPONSIBILITY:
        {RESPONSIBILITY}

        EVALUATION DIMENSIONS (0–100 each):
        - theme_definition
        - theme_relationship
        - musical_concept
        - segment_differentiation
        - sequence_narrative
        - curator_actionability
        - creative_freedom

        RUBRIC:
        {RUBRIC}

        SYSTEM-DERIVED (DO NOT OUTPUT):
        - critic.pass
        - critic.threshold
        - control / control.next_agent

        WRITE SCOPE (model output ONLY):
        - critic.overall_score
        - critic.scores
        - critic.issues
        - critic.actions

        DO NOT WRITE:
        - meta / global_constraints / plan / segments
        - critic.pass / critic.threshold
        - control.*

        OUTPUT EXAMPLE FORMAT:
        {EXAMPLE_OUTPUT}
        """
    ).strip()

    state["critic"]["actions"] = []
    issues_from_last_iteration = state.get("critic", {}).get("issues", [])
    state["critic"]["issues"] = []

    user = dedent(
        f"""
        Evaluate the current PlanState for Planner / Music Curator / Script Writer and produce structured scores,
        issues, and executable actions.

        Requirements:
        - Do not demand BPM fields on playlist items (Curator no longer outputs bpm).
        - Check whether tracks appear to be real recordings; flag likely invented or unidentifiable titles.
        - Check whether song meanings fit the theme; flag clear mismatches.
        - Check script tone, style, and pacing; flag immersion breaks.
        - Check overall script length; it should be reasonable.
        - Mentioning host name Nova and show name Luma Hits in the script where it fits.
        - script.between_tracks should be null where appropriate.

        ISSUES FROM LAST ITERATION:
        {issues_from_last_iteration}

        CURRENT STATE (JSON):
        {json.dumps(state, ensure_ascii=False)}
        """
    ).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

