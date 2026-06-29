"""
米游社 GeeTest 极验验证码处理

使用 ddddocr HTTP API 识别滑块缺口位置，生成模拟人类轨迹完成验证。
供 mihoyobbs.py 和 gamecheckin.py 调用。

ddddocr API 服务需在宿主机运行：
    source ~/ddddocr_env/bin/activate && ddddocr api --host 127.0.0.1 --port 8000 --ocr false

API 接口：
    POST /slide/match
    Content-Type: multipart/form-data
    参数: target (滑块图片), background (背景图片)
    返回: {"target": [x1,y1,x2,y2], "target_x": int, "target_y": int, "confidence": float}
"""

import base64
import io
import json
import random
import time
from typing import Optional

import requests

# ddddocr API 地址
DDDDOCR_API = "http://127.0.0.1:8000"


def _generate_track(distance: int) -> list:
    """
    生成模拟人类的滑动轨迹

    轨迹特点：加速 -> 匀速 -> 减速，y轴小幅抖动

    Args:
        distance: 滑动距离（像素）

    Returns:
        [[x, y, timestamp], ...]
    """
    if distance <= 0:
        return []

    track = []
    x = 0
    y = 0
    t = 0

    # 初始停顿（手指按下前）
    track.append([0, 0, 0])
    t += random.randint(50, 150)

    # 三段式轨迹
    accel_dist = int(distance * 0.3)
    uniform_dist = int(distance * 0.5)
    decel_dist = distance - accel_dist - uniform_dist

    # 加速段
    v = 0
    a = random.uniform(1.5, 2.5)
    for _ in range(accel_dist):
        v += a
        a *= random.uniform(0.95, 0.99)
        x += 1
        y += random.choice([-1, 0, 0, 0, 1])
        t += max(1, int(100 / max(v, 1)))
        track.append([x, y, t])

    # 匀速段
    base_v = v
    for _ in range(uniform_dist):
        cur_v = base_v * random.uniform(0.9, 1.1)
        x += 1
        y += random.choice([-1, 0, 0, 0, 1])
        t += max(1, int(100 / max(cur_v, 1)))
        track.append([x, y, t])

    # 减速段
    for _ in range(decel_dist):
        v *= random.uniform(0.92, 0.98)
        if v < 0.5:
            v = 0.5
        x += 1
        y += random.choice([-1, 0, 0, 0, 1])
        t += max(1, int(100 / max(v, 1)))
        track.append([x, y, t])

    # 结束停顿
    t += random.randint(100, 300)
    track.append([x, y, t])

    # 限制y坐标范围
    for point in track:
        point[1] = max(-3, min(3, point[1]))

    return track


def _slide_match(slice_bytes: bytes, bg_bytes: bytes) -> Optional[int]:
    """
    调用 ddddocr API 识别滑块缺口位置

    Returns:
        缺口 x 坐标，失败返回 None
    """
    try:
        resp = requests.post(
            f"{DDDDOCR_API}/slide_match",
            json={
                "target_image": base64.b64encode(slice_bytes).decode(),
                "background_image": base64.b64encode(bg_bytes).decode(),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"ddddocr API 错误: {resp.status_code} {resp.text}")
            return None

        data = resp.json()
        if "target_x" in data:
            return data["target_x"]
        if "target" in data and len(data["target"]) >= 2:
            return data["target"][0]
        return None
    except Exception as e:
        print(f"调用 ddddocr API 失败: {e}")
        return None


def _solve_captcha(gt: str, challenge: str) -> Optional[dict]:
    """
    解决 GeeTest 验证码

    Args:
        gt: 极验公钥
        challenge: 挑战值

    Returns:
        {"challenge": ..., "validate": ...} 或 None
    """
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        # 1. 获取验证码图片信息
        url = f"https://api.geetest.com/get.php?gt={gt}&challenge={challenge}&lang=zh-cn&pt=0&client_type=web"
        resp = session.get(url)
        data = resp.json()

        bg_url = data.get("bg", "")
        slice_url = data.get("slice", "")
        new_challenge = data.get("challenge", challenge)

        # 补全URL
        if bg_url and not bg_url.startswith("http"):
            bg_url = f"https://static.geetest.com/{bg_url}"
        if slice_url and not slice_url.startswith("http"):
            slice_url = f"https://static.geetest.com/{slice_url}"

        # 2. 下载图片
        bg_bytes = session.get(bg_url).content
        slice_bytes = session.get(slice_url).content

        # 3. 调用 ddddocr API 识别缺口位置
        gap_x = _slide_match(slice_bytes, bg_bytes)
        if gap_x is None or gap_x <= 0:
            return None

        # 4. 生成轨迹
        track = _generate_track(gap_x)
        passtime = track[-1][2] if track else 0

        # 5. 构造 w 参数
        w_data = {
            "userresponse": [{"x": p[0], "y": p[1], "t": p[2]} for p in track],
            "passtime": passtime,
            "rp": f"{gt}|{challenge}"
        }
        w = json.dumps(w_data)

        # 6. 提交验证
        url = "https://api.geetest.com/ajax.php"
        data = {
            "gt": gt,
            "challenge": new_challenge,
            "lang": "zh-cn",
            "pt": 0,
            "client_type": "web",
            "w": w
        }
        resp = session.post(url, data=data)
        result = resp.json()

        if result.get("validate"):
            return {
                "challenge": result.get("challenge", new_challenge),
                "validate": result["validate"]
            }

        return None

    except Exception as e:
        print(f"验证码处理失败: {e}")
        return None


def game_captcha(gt: str, challenge: str) -> dict:
    """
    游戏签到验证码处理

    Args:
        gt: 极验公钥
        challenge: 挑战值

    Returns:
        成功: {"challenge": challenge, "validate": validate}
        失败: None
    """
    return _solve_captcha(gt, challenge)


def bbs_captcha(gt: str, challenge: str) -> dict:
    """
    论坛签到验证码处理

    Args:
        gt: 极验公钥
        challenge: 挑战值

    Returns:
        成功: {"challenge": challenge, "validate": validate}
        失败: None
    """
    return _solve_captcha(gt, challenge)
