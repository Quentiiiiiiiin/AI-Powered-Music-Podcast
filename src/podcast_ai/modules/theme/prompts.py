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

