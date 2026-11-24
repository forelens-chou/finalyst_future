"""
Network proxy injection, connectivity diagnostic, and retry decorator.
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Any, Callable, Dict, Optional, TypeVar
import requests

from src.config import AppConfig

logger = logging.getLogger("fetchfu.network")

F = TypeVar("F", bound=Callable[..., Any])


def setup_proxy_environment(config: AppConfig) -> None:
    """
    Inject proxy settings into environment variables and AkShare configuration.
    Supports http://, https://, socks5://, and socks5h:// (remote DNS).
    Enforces minimum network timeout on all underlying requests sessions.
    """
    timeout = max(config.network.timeout_seconds, 25)

    # 1. Patch requests.Session.request globally to prevent third-party libraries (e.g. AkShare)
    # from hardcoding a small timeout (e.g. 10s or 15s) that fails on cross-border links.
    if not getattr(requests.Session, "_fetchfu_timeout_patched", False):
        orig_request = requests.Session.request

        def _patched_request(self, method, url, *args, **kwargs):
            req_timeout = kwargs.get("timeout")
            if req_timeout is None or (isinstance(req_timeout, (int, float)) and req_timeout < timeout):
                kwargs["timeout"] = timeout
            return orig_request(self, method, url, *args, **kwargs)

        requests.Session.request = _patched_request
        requests.Session._fetchfu_timeout_patched = True  # type: ignore
        logger.debug(f"Patched requests.Session with minimum timeout of {timeout}s.")

    proxy_cfg = config.network.proxy
    if proxy_cfg.enabled and (proxy_cfg.http_url or proxy_cfg.https_url):
        http_p = proxy_cfg.http_url or proxy_cfg.https_url
        https_p = proxy_cfg.https_url or proxy_cfg.http_url

        os.environ["HTTP_PROXY"] = http_p
        os.environ["http_proxy"] = http_p
        os.environ["HTTPS_PROXY"] = https_p
        os.environ["https_proxy"] = https_p
        os.environ["ALL_PROXY"] = https_p
        os.environ["all_proxy"] = https_p

        # Attempt to set Akshare internal context config
        try:
            from akshare.utils.context import config as ak_cfg
            ak_cfg.set_proxies({"http": http_p, "https": https_p})
            ak_cfg.set_timeout(timeout)
            logger.debug("Configured AkShare internal proxy context.")
        except Exception as e:
            logger.debug(f"Could not configure AkShare internal context: {e}")

        logger.info(f"Proxy environment initialized: HTTP={http_p}, HTTPS={https_p}")
    else:
        try:
            from akshare.utils.context import config as ak_cfg
            ak_cfg.set_timeout(timeout)
        except Exception:
            pass
        logger.debug(f"Proxy disabled or not configured; using direct connection (timeout={timeout}s).")


def check_proxy(config: AppConfig) -> Dict[str, Any]:
    """
    Perform a health check on the network connection and proxy setup.
    Tests external IP resolution and Sina Finance endpoint accessibility.
    """
    setup_proxy_environment(config)

    proxy_cfg = config.network.proxy
    proxies = None
    if proxy_cfg.enabled and (proxy_cfg.http_url or proxy_cfg.https_url):
        proxies = {
            "http": proxy_cfg.http_url or proxy_cfg.https_url,
            "https": proxy_cfg.https_url or proxy_cfg.http_url,
        }

    timeout = config.network.timeout_seconds
    result: Dict[str, Any] = {
        "proxy_enabled": proxy_cfg.enabled,
        "proxy_urls": {
            "http": proxy_cfg.http_url,
            "https": proxy_cfg.https_url,
        },
        "success": False,
        "ip": None,
        "latency_ms": None,
        "sina_connectivity": False,
        "sina_latency_ms": None,
        "message": "",
    }

    # 1. IP check endpoints (fallback order)
    ip_check_urls = [
        "https://httpbin.org/ip",
        "https://api.ipify.org?format=json",
        "https://ipapi.co/json/",
    ]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })
    if proxies:
        session.proxies.update(proxies)

    ip_found = None
    elapsed_ms = None
    for url in ip_check_urls:
        try:
            t0 = time.perf_counter()
            resp = session.get(url, timeout=timeout)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            if resp.status_code == 200:
                data = resp.json()
                ip_found = data.get("origin") or data.get("ip")
                if ip_found:
                    break
        except Exception as e:
            logger.debug(f"Failed check against {url}: {e}")

    result["ip"] = ip_found
    result["latency_ms"] = elapsed_ms

    # 2. Sina Futures API connectivity check
    sina_test_url = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.getFewMinLine"
    try:
        t0 = time.perf_counter()
        resp = session.get(
            sina_test_url,
            params={"symbol": "RB0", "type": "60"},
            timeout=timeout,
        )
        sina_elapsed = round((time.perf_counter() - t0) * 1000, 2)
        if resp.status_code == 200 and ("\"d\":" in resp.text or "=(" in resp.text):
            result["sina_connectivity"] = True
            result["sina_latency_ms"] = sina_elapsed
        else:
            result["sina_connectivity"] = False
            result["sina_latency_ms"] = sina_elapsed
            result["message"] = f"Sina returned status {resp.status_code}"
    except Exception as e:
        result["sina_connectivity"] = False
        result["message"] = f"Sina endpoint connection failed: {e}"

    if result["sina_connectivity"]:
        result["success"] = True
        result["message"] = "Network and Sina futures endpoint reachable successfully."
    else:
        result["success"] = False

    return result


def retry_with_backoff(
    retries: int = 3,
    backoff_factor: float = 1.5,
    initial_delay: float = 1.0,
    exceptions: tuple = (requests.RequestException, ConnectionError, TimeoutError, OSError),
) -> Callable[[F], F]:
    """
    Decorator for retrying a function with exponential backoff.
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            delay = initial_delay
            while attempt < retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    attempt += 1
                    if attempt >= retries:
                        logger.error(
                            f"[{func.__name__}] Failed after {retries} attempts: {exc}"
                        )
                        raise
                    logger.warning(
                        f"[{func.__name__}] Attempt {attempt}/{retries} failed ({exc}). "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
                except Exception as exc:
                    # Non-retryable fatal error
                    logger.error(f"[{func.__name__}] Non-retryable error encountered: {exc}")
                    raise
            return None
        return wrapper  # type: ignore
    return decorator
