"""Read-only providers for the local Codex and DeepSeek account.

Codex: official `codex app-server` JSON-RPC account endpoints.
DeepSeek: official GET https://api.deepseek.com/user/balance.

No secrets are printed, persisted in this module, or sent to third parties.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVICE = "ai-usage-pet"


class ProviderError(RuntimeError):
    pass


def locate_codex() -> str | None:
    """GUI launchers often have a PATH without an interactive shell's npm paths."""
    configured = os.environ.get("CODEX_BIN", "").strip()
    if configured:
        p = Path(configured).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        raise ProviderError("CODEX_BIN 指向的 Codex 程序不存在或不可执行")
    path = shutil.which("codex")
    if path:
        return path
    home = Path.home()
    candidates = [
        home / ".local/bin/codex",
        home / ".npm-global/bin/codex",
        home / ".local/share/pnpm/codex",
        home / ".bun/bin/codex",
        Path("/usr/local/bin/codex"),
        Path("/usr/bin/codex"),
    ]
    nvm_bin = home / ".nvm/versions/node"
    if nvm_bin.exists():
        candidates.extend(sorted(nvm_bin.glob("*/bin/codex"), reverse=True))
    for item in candidates:
        if item.is_file() and os.access(item, os.X_OK):
            return str(item)
    return None


def codex_environment() -> dict:
    """Honor explicit proxy variables; otherwise use GNOME's existing manual proxy."""
    import ast
    environment = os.environ.copy()
    if any(environment.get(k) for k in ("ALL_PROXY", "all_proxy")):
        return environment
    settings = shutil.which("gsettings")
    if not settings:
        return environment

    def setting(schema, key):
        try:
            result = subprocess.run([settings, "get", schema, key], capture_output=True,
                                    text=True, timeout=2, check=True)
            return ast.literal_eval(result.stdout.strip())
        except (OSError, subprocess.SubprocessError, ValueError, SyntaxError):
            return None

    if setting("org.gnome.system.proxy", "mode") != "manual":
        return environment
    for scheme in ("http", "https"):
        if environment.get(scheme + "_proxy") or environment.get(scheme.upper() + "_PROXY"):
            continue
        host = setting("org.gnome.system.proxy." + scheme, "host")
        port = setting("org.gnome.system.proxy." + scheme, "port")
        if isinstance(host, str) and host and not any(c in host for c in "/@ ?#") and isinstance(port, int) and 0 < port < 65536:
            if ":" in host and not host.startswith("["):
                host = "[" + host + "]"
            environment[scheme.upper() + "_PROXY"] = f"http://{host}:{port}"
    return environment


class CodexRPC:
    """Short-lived JSON-RPC stdio session; never creates a model turn."""
    def __init__(self, executable: str, timeout: float = 14):
        self.executable = executable
        self.timeout = timeout
        self.proc: subprocess.Popen[str] | None = None
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.seq = 0
        self.unmatched: dict[int, dict[str, Any]] = {}
        self._reader: threading.Thread | None = None

    def __enter__(self):
        environment = codex_environment()
        executable_dir = str(Path(self.executable).parent)
        environment["PATH"] = executable_dir + os.pathsep + environment.get("PATH", "")
        try:
            self.proc = subprocess.Popen(
                [self.executable, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                encoding="utf-8",
                text=True,
                env=environment,
            )
        except OSError as exc:
            raise ProviderError(f"无法启动 Codex：{exc.strerror or type(exc).__name__}") from None

        def listen():
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        self.events.put(item)
                except json.JSONDecodeError:
                    continue

        self._reader = threading.Thread(target=listen, daemon=True)
        self._reader.start()
        try:
            self.request("initialize", {"clientInfo": {
                "name": "ai_usage_pet", "title": "AI Usage Pet", "version": "1.0.0"}})
            self.notify("initialized", {})
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=1)
            except OSError:
                pass
            if self.proc.stdin:
                self.proc.stdin.close()
            if self.proc.stdout:
                self.proc.stdout.close()
        return False

    def _send(self, message: dict):
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            raise ProviderError("Codex app-server 已退出；请在终端检查 codex app-server")
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ProviderError("与 Codex 的本地连接中断") from None

    def notify(self, method: str, params: dict | None = None):
        self._send({"method": method, "params": params or {}})

    def request(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        current_id = self.seq
        # Current app-server requires params even for parameterless methods.
        message = {"id": current_id, "method": method, "params": params if params is not None else {}}
        self._send(message)
        deadline = time.monotonic() + self.timeout
        while True:
            if current_id in self.unmatched:
                result = self.unmatched.pop(current_id)
            else:
                try:
                    result = self.events.get(timeout=min(0.5, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        raise ProviderError(f"Codex 查询超时：{method}") from None
                    if self.proc and self.proc.poll() is not None:
                        raise ProviderError("Codex app-server 提前退出；可能需要更新 Codex CLI") from None
                    continue
                if result.get("id") != current_id:
                    other_id = result.get("id")
                    if isinstance(other_id, int):
                        self.unmatched[other_id] = result
                    continue
            if "error" in result:
                err = result["error"]
                msg = err.get("message", "未提供错误信息") if isinstance(err, dict) else str(err)
                raise ProviderError(f"Codex 接口 {method}：{msg}")
            body = result.get("result")
            if not isinstance(body, dict):
                raise ProviderError(f"Codex 接口 {method} 返回格式无法识别")
            return body


def parse_codex_rate_limits(response: dict) -> dict:
    """Handle both multi-bucket and older single-bucket response shapes."""
    limits = response.get("rateLimitsByLimitId")
    if not isinstance(limits, dict) or not limits:
        single = response.get("rateLimits")
        limits = {single.get("limitId") or "codex": single} if isinstance(single, dict) else {}
    buckets = []
    for key, entry in limits.items():
        if not isinstance(entry, dict):
            continue
        windows = []
        for kind in ("primary", "secondary"):
            window = entry.get(kind)
            if not isinstance(window, dict):
                continue
            percent = window.get("usedPercent")
            if not isinstance(percent, (int, float)) or isinstance(percent, bool):
                continue
            windows.append({
                "kind": kind,
                "used": max(0.0, min(100.0, float(percent))),
                "remaining": max(0.0, min(100.0, 100.0 - float(percent))),
                "duration_mins": window.get("windowDurationMins"),
                "resets_at": window.get("resetsAt"),
            })
        buckets.append({
            "id": str(entry.get("limitId") or key),
            "name": entry.get("limitName") or str(key),
            "plan": entry.get("planType"),
            "windows": windows,
        })
    credits = response.get("rateLimitResetCredits")
    reset_info = None
    if isinstance(credits, dict):
        count = credits.get("availableCount")
        details = credits.get("credits")
        reset_info = {
            "available": count if isinstance(count, int) and not isinstance(count, bool) else None,
            "details": [{
                "title": (i.get("title") or "重置卡"),
                "expires_at": i.get("expiresAt"),
                "status": i.get("status"),
            } for i in details if isinstance(i, dict)] if isinstance(details, list) else None,
        }
    return {"buckets": buckets, "resets": reset_info}


def api_mode_info(config: dict) -> dict:
    """Public provider metadata only. Never read or export API credentials."""
    from urllib.parse import urlsplit
    provider_id = config.get("model_provider") or "openai"
    providers = config.get("model_providers") or {}
    provider = providers.get(provider_id) or {}
    base_url = provider.get("base_url") or (os.environ.get("OPENAI_BASE_URL") if provider_id == "openai" else None)
    base_url = base_url or ("https://api.openai.com/v1" if provider_id == "openai" else "")
    try:
        host = urlsplit(base_url).hostname or ""
    except ValueError:
        host = ""
    official = host == "api.openai.com"
    return {
        "mode": "api",
        "plan": None,
        "buckets": [],
        "resets": None,
        "api": {
            "provider": "OpenAI" if official else "第三方 API",
            "host": host,
            "balance": None,
            "status": "unavailable",
            "message": "普通 API Key 无法通过公开接口读取剩余余额，请在 OpenAI 账单页查看。" if official else "此平台的余额接口尚未适配。",
            "billing_url": "https://platform.openai.com/settings/organization/billing/overview" if official else None,
        },
    }


def uses_external_api(config: dict) -> bool:
    provider_id = config.get("model_provider") or "openai"
    provider = (config.get("model_providers") or {}).get(provider_id) or {}
    return provider_id != "openai" and provider.get("requires_openai_auth", False) is False


def fetch_codex() -> dict:
    path = locate_codex()
    if not path:
        raise ProviderError("未找到 Codex CLI。可运行 npm install -g @openai/codex@latest")
    with CodexRPC(path) as conn:
        account = conn.request("account/read", {"refreshToken": False})
        account_data = account.get("account")
        # config/read resolves active provider/profile layering without changing it.
        config = {}
        try:
            value = conn.request("config/read", {"includeLayers": False}).get("config")
            if isinstance(value, dict):
                config = value
        except ProviderError:
            pass
        auth_type = account_data.get("type") if isinstance(account_data, dict) else None
        if auth_type in ("apiKey", "apikey") or uses_external_api(config):
            return api_mode_info(config)
        if auth_type != "chatgpt":
            raise ProviderError("Codex 未登录，或当前凭据类型不支持额度查询。请检查 codex login status。")
        rate_data = conn.request("account/rateLimits/read")
        parsed = parse_codex_rate_limits(rate_data)
        # Activity statistics are optional and should never block rate-limit display.
        summary = None
        daily = []
        try:
            usage = conn.request("account/usage/read")
            summary = usage.get("summary") if isinstance(usage.get("summary"), dict) else None
            if isinstance(usage.get("dailyUsageBuckets"), list):
                daily = usage["dailyUsageBuckets"][-14:]
        except ProviderError:
            pass
        return {
            "mode": "chatgpt",
            "email": account_data.get("email"),
            "plan": account_data.get("planType"),
            "buckets": parsed["buckets"],
            "resets": parsed["resets"],
            "token_summary": summary,
            "daily_usage": daily,
        }


def get_deepseek_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        import keyring
        return keyring.get_password(SERVICE, "deepseek-api-key")
    except Exception:
        return None


def save_deepseek_key(key: str):
    if not key.strip():
        raise ProviderError("不能保存空密钥")
    try:
        import keyring
        keyring.set_password(SERVICE, "deepseek-api-key", key.strip())
    except Exception as exc:
        raise ProviderError("系统密钥环不可用。请在系统中启用 GNOME Keyring，或使用 DEEPSEEK_API_KEY 环境变量") from exc


def delete_deepseek_key():
    try:
        import keyring
        keyring.delete_password(SERVICE, "deepseek-api-key")
    except Exception:
        pass


def fetch_deepseek() -> dict:
    key = get_deepseek_key()
    if not key:
        raise ProviderError("未设置 DeepSeek API Key；请点击“设置 DeepSeek Key”")
    request = Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=12) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderError("DeepSeek API Key 无效或没有查询权限") from None
        raise ProviderError(f"DeepSeek 返回 HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise ProviderError("连接 DeepSeek 失败，请检查网络和代理设置") from None
    except (ValueError, json.JSONDecodeError):
        raise ProviderError("DeepSeek 返回内容不是有效 JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("balance_infos"), list):
        raise ProviderError("DeepSeek 余额字段不存在")
    return {
        "is_available": data.get("is_available"),
        "balances": [{
            "currency": entry.get("currency"),
            "total": entry.get("total_balance"),
            "granted": entry.get("granted_balance"),
            "topped_up": entry.get("topped_up_balance"),
        } for entry in data["balance_infos"] if isinstance(entry, dict)],
    }


def refresh_all() -> dict:
    results = {"checked_at": int(time.time())}
    for name, fetch in (("codex", fetch_codex), ("deepseek", fetch_deepseek)):
        try:
            results[name] = {"ok": True, "data": fetch()}
        except ProviderError as exc:
            results[name] = {"ok": False, "error": str(exc)}
        except Exception as exc:
            # Avoid propagating exception text from HTTP libraries that may
            # expose environment, paths or credentials.
            results[name] = {"ok": False, "error": f"查询异常：{type(exc).__name__}"}
    return results
