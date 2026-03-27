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
      你是一名专业的音乐电台节目策划人，你正编排下一期的音乐歌单，擅长根据主题和时长规划音乐 Podcast。

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
              - recommended_tracks：若干推荐曲目名称或描述

      强约束：
        - 严格按照 JSON 结构返回，**不要** 包含任何解释性文字或 Markdown。
        - 字段名必须使用小写下划线风格（snake_case），如 target_duration_seconds、overall_bpm_range。
        - overall_bpm_range 和各段 bpm_range 可以为 null（当不方便给出时）。
        - 每首歌曲用4分钟来估算段落时长和整期episode时长。
        - segments[*].host_script 为必填字段，且必须使用用户指定的语言（language 字段）。
        - target_playlist 必须是数组；target_playlist[*].recommended_tracks 必须是 string[]（推荐曲目名称数组，可为空数组但类型必须正确）。
        - target_playlist[*].search_hints 为对象，允许使用空对象 {{}}。
        - 若提供 target_playlist[*].host_script_between_songs，则该字段为可选字段，值可以是字符串或 null。

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
                  "recommended_tracks": [
                    "曲目 A - 艺术家 X",
                    "曲目 B - 艺术家 Y"
                  ],
                  "search_hints": {{
                    "keywords": ["late night", "chill electronic"],
                    "artist": "Artist X"
                  }},
                  "host_script_between_songs": null
                }},
                {{
                  "recommended_tracks": ["曲目 C - 艺术家 Z"],
                  "search_hints": {{}},
                  "host_script_between_songs": "接下来这首歌会把情绪再推高一点。"
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
        - 总目标时长（所有 segments 的 target_duration_seconds 之和）要大致接近 {request.duration_minutes} 分钟（允许 ±10% 浮动）。
        - 每个段落的时长可以不完全平均，但需要有合理的节奏起伏（如开场略短，中段最长，收尾适中）。
        - 整体风格/情绪需要围绕主题展开，避免风格完全跑偏。
        - target_playlist 中的曲目名称必须是真实歌曲。

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
          - 串词位置
            - 必须要的串词位置
              - 电台开场白（打招呼引出电台名称Luma Hits + 本期主题介绍 + 第一个segment要播放的歌曲）
              - 每个segment中间过渡串词
              - 最后一首歌之前结束语（重新回到本期主题，平滑引出最后一首歌，结束语）
            - 可选择的串词位置
              - 同一个segment里相邻的两首歌中间也可以插入串词，目的是抒发自己的感受，增加活人感。

        请直接输出 JSON，不要添加任何额外文字。
        """
    ).strip()

    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]

