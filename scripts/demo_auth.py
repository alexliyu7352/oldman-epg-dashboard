"""示例配置里没有的认证密钥：测试和浏览器门禁每次临时生成。"""

from __future__ import annotations

import secrets

#: 「服务调用方」示例页用的 API key 名字；浏览器门禁按这个名字从配置文件里取出密钥。
DEMO_API_KEY_NAME = "demo_script"


def demo_auth_settings() -> dict[str, object]:
    """访问令牌与 API key 两个示例页要的 ``web.auth``：随机的签名密钥和一个 API key。

    这些密钥不写进 data/web_settings.example.yaml：拿到 ``jwt.secret`` 就能签出任意用户（包括超级用户）的令牌，
    而示例配置是公开的。
    """
    return {
        "authenticators": ["session", "jwt", "api_key"],
        "jwt": {"secret": secrets.token_urlsafe(48)},
        "api_keys": {DEMO_API_KEY_NAME: {"secret": secrets.token_urlsafe(32)}},
    }
