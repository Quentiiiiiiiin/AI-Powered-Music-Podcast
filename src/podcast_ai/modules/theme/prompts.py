from __future__ import annotations

from textwrap import dedent

from podcast_ai.core.models import EpisodeRequest


def build_theme_planner_messages(request: EpisodeRequest, segments_hint: int) -> list[dict[str, str]]:
    """
    构造用于 ThemePlanner 的对话消息（OpenAI Chat 兼容格式）。

    LLM 必须返回**严格的 JSON**，不包含多余文本或注释。
    """

    sys = dedent(
        f"""
        你是一名专业的音乐电台节目策划人，擅长根据主题和时长规划音乐 Podcast。

        目标：
        - 根据用户提供的主题、目标时长和语言，规划一个音乐节目 EpisodePlan。
        - 输出节目段落（segments）、整体风格描述、整体 BPM 区间。
        - 每个段落需要：
          - name：如「开场」「中段」「收尾」等
          - target_duration_seconds：该段目标时长（秒）
          - bpm_range：该段推荐的 BPM 区间 [min, max]
          - mood：该段情绪/氛围描述
          - host_script：该段主持人串词草稿（使用用户指定语言，保持自然口语）
          - target_playlist：该段的目标歌单规划
            - 每一项包含：
              - recommended_tracks：若干推荐曲目名称或描述
              - search_hints：用于在各平台搜索的提示，如艺术家/曲风/BPM 区间/关键词等

        强约束：
        - 严格按照 JSON 结构返回，**不要** 包含任何解释性文字或 Markdown。
        - 字段名必须使用小写下划线风格（snake_case），如 target_duration_seconds、overall_bpm_range。
        - overall_bpm_range 和各段 bpm_range 可以为 null（当不方便给出时）。
        - target_playlist 至少包含 3 条推荐项。
        - 所有 host_script 文本必须使用用户指定的语言（language 字段）。

        输出 JSON 顶层结构示例（示意，不要照抄示例的内容）：
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
                  "recommended_tracks": ["曲目 A - 艺术家 X", "曲目 B - 艺术家 Y"],
                  "search_hints": {{
                    "genre": "chill electronic",
                    "bpm_range": [90, 100],
                    "keywords": ["late night", "lofi"]
                  }}
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
        - 期望段落数量（参考值）：{segments_hint}

        要求：
        - 总目标时长（所有 segments 的 target_duration_seconds 之和）要大致接近 {request.duration_minutes} 分钟（允许 ±10% 浮动）。
        - 每个段落的时长可以不完全平均，但需要有合理的节奏起伏（如开场略短，中段最长，收尾适中）。
        - 整体风格/情绪需要围绕主题展开，避免风格完全跑偏。
        - target_playlist 中的曲目名称必须是真实歌曲。

        请直接输出 JSON，不要添加任何额外文字。
        """
    ).strip()

    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]

