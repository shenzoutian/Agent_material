"""
common/net.py —— HTTP 请求（带退避重试）。

"""

import time


def _get(url: str, params: dict = None, headers: dict = None, timeout: int = 30,
         tries: int = 3, backoff: float = 1.5):
    """带退避重试的 GET 请求，提高对短暂网络 / 服务波动的鲁棒性。"""
    import requests
    last = None
    for attempt in range(tries):
        try:
            return requests.get(url, params=params, headers=headers, timeout=timeout)
        except Exception as e:
            last = e
            if attempt < tries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last
