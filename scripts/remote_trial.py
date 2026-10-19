#!/usr/bin/env python3
"""Optional temporary HTTPS tunnel for evaluation, with a required local password."""
import argparse
import collections
from getpass import getpass
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.access import load_password, set_password
from localledger.store import Problem


def tunnel_url(line):
    match = re.search(r'https://([a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com)(?![a-z0-9.-])', line)
    return match.group(0) if match else None


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='临时外网试用：需要官方 cloudflared；地址会变化，不适用于正式长期运营')
    parser.add_argument('--data-dir', default=os.environ.get('LOCAL_LEDGER_DATA', str(root / 'data')))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--cloudflared', default=None)
    args = parser.parse_args()
    binary = args.cloudflared or str(root / ('cloudflared.exe' if os.name == 'nt' else 'cloudflared'))
    if not Path(binary).is_file():
        binary = shutil.which('cloudflared')
    if not binary:
        parser.error('请从 https://github.com/cloudflare/cloudflared/releases 下载适合本机的官方程序，放到软件目录并命名为 cloudflared.exe（Windows）或 cloudflared（Linux/macOS）')
    # Fail before creating a tunnel if another ledger instance is listening.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            parser.error('端口正在使用，请先关闭其他账本启动窗口，或使用 --port 8766')
    try:
        if not load_password(args.data_dir):
            first = getpass('为外网访问设置独立密码（至少12位，不会显示）：')
            if first != getpass('再次输入密码：'):
                parser.error('两次密码不一致')
            set_password(args.data_dir, first)
    except (Problem, ValueError) as exc:
        parser.error(str(exc))
    print('这是临时外网试用入口，重启可能换地址，服务没有在线时长保证。', flush=True)
    print('隧道服务会转发你的网页与账本数据；请先用模拟数据验证网络和设备。', flush=True)
    print('正在建立 HTTPS 连接，可按 Ctrl+C 退出……', flush=True)
    tunnel = None
    app = None
    lines = queue.Queue(maxsize=200)
    tail = collections.deque(maxlen=8)
    try:
        tunnel = subprocess.Popen([str(binary), 'tunnel', '--no-autoupdate', '--protocol', 'http2',
            '--url', f'http://127.0.0.1:{args.port}'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace')
        def reader():
            for line in tunnel.stdout:
                try:
                    lines.put_nowait(line.rstrip())
                except queue.Full:
                    pass
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline, url = time.monotonic() + 60, None
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=0.5)
                tail.append(line)
                url = tunnel_url(line)
                if url:
                    break
            except queue.Empty:
                if tunnel.poll() is not None:
                    break
        if not url:
            print('未能创建外网入口。可检查网络能否连接 Cloudflare，或改用本地启动；不要反复重新录入账目。', file=sys.stderr)
            for line in tail:
                print(line, file=sys.stderr)
            return 1
        app = subprocess.Popen([sys.executable, str(root / 'run.py'), '--data-dir', args.data_dir,
            '--host', '127.0.0.1', '--port', str(args.port), '--public-url', url, '--no-browser'])
        print('\n手机（含移动网络）与电脑都使用这个 HTTPS 地址：' + url, flush=True)
        print('输入独立访问密码。请保存地址并保持本窗口、电脑与网络运行。', flush=True)
        print('关闭隧道后入口失效；账本仍留在原数据目录。', flush=True)
        while app.poll() is None and tunnel.poll() is None:
            time.sleep(0.5)
        return app.returncode if app.poll() is not None else 1
    except KeyboardInterrupt:
        print('\n正在关闭外网试用入口……', flush=True)
        return 0
    finally:
        for process in [app, tunnel]:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
