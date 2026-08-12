# MiniMax同步语音合成HTTP

## 创建
### 非流式请求
```python 
import requests

url = "https://api.minimaxi.com/v1/t2a_v2"

payload = {
    "model": "speech-2.8-hd",
    "text": "今天是不是很开心呀(laughs)，当然了！",
    "stream": False,
    "voice_setting": {
        "voice_id": "male-qn-qingse",
        "speed": 1,
        "vol": 1,
        "pitch": 0,
        "emotion": "happy"
    },
    "audio_setting": {
        "sample_rate": 32000,
        "bitrate": 128000,
        "format": "mp3",
        "channel": 1
    },
    "pronunciation_dict": { "tone": ["处理/(chu3)(li3)", "危险/dangerous"] },
    "subtitle_enable": False
}
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer <token>"
}

response = requests.post(url, json=payload, headers=headers)

print(response.text)

```

### 非流式响应
```python
{
  "data": {
    "audio": "<hex编码的audio>",
    "status": 2
  },
  "extra_info": {
    "audio_length": 9900,
    "audio_sample_rate": 32000,
    "audio_size": 160323,
    "bitrate": 128000,
    "word_count": 52,
    "invisible_character_ratio": 0,
    "usage_characters": 26,
    "audio_format": "mp3",
    "audio_channel": 1
  },
  "trace_id": "01b8bf9bb7433cc75c18eee6cfa8fe21",
  "base_resp": {
    "status_code": 0,
    "status_msg": "success"
  }
}
```

