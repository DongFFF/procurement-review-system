#!/usr/bin/env python
"""
后端启动脚本（桌面端与命令行共用）。

用法：
    python backend/run_server.py                    # 默认 127.0.0.1:8756
    python backend/run_server.py --port 9000        # 指定端口
    python backend/run_server.py --no-browser       # 不自动打开浏览器

桌面端（Tauri）会以子进程方式调用本脚本，并传入 --port / --host。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 保证能 import app 包
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_PORT = 8756
DEFAULT_HOST = "127.0.0.1"


def find_free_port(host: str, start: int, tries: int = 30) -> int:
    """从 start 开始找一个可用端口。"""
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"从 {start} 起连续 {tries} 个端口都被占用，请用 --port 指定其他端口")


def wait_and_open(url: str, timeout: float = 60.0) -> None:
    """等后端起来后自动打开浏览器（仅桌面/独立运行时用）。"""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as r:
                if r.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)
    print(f"[警告] 等待后端就绪超时，请手动访问 {url}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="中直高校采购需求审查系统 - 后端服务")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="端口被占用时自动顺延（桌面端推荐）",
    )
    args = parser.parse_args()

    host = args.host
    port = args.port

    # 端口占用处理
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            if args.auto_port:
                port = find_free_port(host, port)
                print(f"[提示] 端口 {args.port} 被占用，改用 {port}")
            else:
                raise SystemExit(
                    f"端口 {port} 已被占用。"
                    f"若本程序已在运行，请直接访问 http://{host}:{port}；"
                    f"或使用 --port 指定其他端口。"
                )

    url = f"http://{host}:{port}"
    print("=" * 62)
    print("  中直高校采购需求审查系统")
    print(f"  服务地址：{url}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 62)

    if not args.no_browser and not args.reload:
        threading.Thread(target=wait_and_open, args=(url,), daemon=True).start()

    # 打包态下把工作目录固定到程序根，保证相对路径正确
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
        access_log=False,
        app_dir=str(BACKEND_DIR),
    )


if __name__ == "__main__":
    main()
