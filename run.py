#!/usr/bin/env python3
"""Start the shared restaurant ledger, locally or behind a HTTPS reverse proxy."""
import argparse
import getpass
import os
from pathlib import Path
import socket
import sys
import threading
import webbrowser

from localledger.server import Application, make_server
from localledger.access import set_password
from localledger.browser import find_chrome, open_chrome
from localledger.store import Problem
from localledger.desktop import DataDirectoryLock


def main():
    parser = argparse.ArgumentParser(description='本地账本：电脑存储，手机和电脑共用')
    program_directory = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    parser.add_argument('--data-dir')
    parser.add_argument('--test-mode', action='store_true', help='使用隔离的模拟测试账本，不打开正式data目录')
    parser.add_argument('--host', default=None)
    parser.add_argument('--public-url', help='HTTPS 反向代理域名；此模式只监听本机')
    parser.add_argument('--set-password', action='store_true', help='设置独立访问密码后退出')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--browser', choices=['system', 'chrome'], default='system', help='选择默认浏览器或Chrome')
    parser.add_argument('--chrome-path', help='Chrome安装位置（可选，自动检测失败时使用）')
    args = parser.parse_args()
    args.data_dir = args.data_dir or (str(program_directory / 'test-data') if args.test_mode else os.environ.get('LOCAL_LEDGER_DATA', str(program_directory / 'data')))
    args.port = args.port if args.port is not None else (8767 if args.test_mode else 8765)
    if args.set_password:
        first = getpass.getpass('设置访问密码（至少12位，不会显示）：')
        if first != getpass.getpass('再次输入密码：'):
            parser.error('两次密码不一致')
        try:
            with DataDirectoryLock(args.data_dir):
                set_password(args.data_dir, first)
        except Problem as exc:
            parser.error(str(exc))
        print('密码已保存。重新启动服务后生效，旧会话会失效。')
        return 0
    chrome = None
    if args.browser == 'chrome' and not args.no_browser:
        chrome = find_chrome(args.chrome_path)
        if not chrome:
            parser.error('未找到Chrome，请先安装，或用 --chrome-path 指定chrome.exe位置')
    args.host = args.host or ('127.0.0.1' if args.public_url else '0.0.0.0')
    directory_lock = DataDirectoryLock(args.data_dir)
    try:
        directory_lock.acquire()
        app = Application(args.data_dir, public_url=args.public_url, test_mode=args.test_mode)
    except (Problem, ValueError) as exc:
        directory_lock.release()
        parser.error(str(exc))
    except BaseException:
        directory_lock.release()
        raise
    try:
        server = make_server(app, args.host, args.port)
    except (OSError, Problem) as exc:
        try:
            app.close()
        finally:
            directory_lock.release()
        print(f'启动失败：{exc}。可使用 --port 8766 更换端口。', file=sys.stderr)
        return 1
    port = server.server_address[1]
    print('\n本地账本已启动。所有数据保存到：' + str(app.store.root), flush=True)
    if args.test_mode:
        print('【本地测试版】模拟数据持久保存，可练习完整流程；不会发起真实支付。', flush=True)
    if not app.password:
        print('本次连接码：' + app.code, flush=True)
    else:
        print('使用已设置的独立密码登录。', flush=True)
    if app.public_url:
        print('外网入口：' + app.public_url + '（需要 HTTPS 反向代理已经运行）', flush=True)
    if not app.public_url:
        print(f'电脑浏览器地址：http://127.0.0.1:{port}', flush=True)
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # No packet is sent: this determines the local routing interface.
            probe.connect(('192.0.2.1', 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    if args.host != '127.0.0.1':
        for address in sorted(addresses):
            if not address.startswith('127.'):
                print(f'同一Wi-Fi的手机可打开：http://{address}:{port}', flush=True)
    credential = '独立密码' if app.password else '启动窗口连接码'
    print(f'首次连接请输入{credential}。请保持此窗口运行；关闭后手机也会断开。按 Ctrl+C 退出。\n', flush=True)
    if not args.no_browser:
        url = app.public_url or f'http://127.0.0.1:{port}'
        def launch_browser():
            try:
                if args.browser == 'chrome':
                    open_chrome(chrome, url)
                else:
                    webbrowser.open(url)
            except OSError as exc:
                print(f'浏览器未能自动打开：{exc}。服务仍在运行，请在Chrome输入启动窗口显示的地址。', flush=True)
        threading.Timer(0.6, launch_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n正在保存并退出……', flush=True)
    finally:
        try:
            server.server_close()
            app.close()
        finally:
            directory_lock.release()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
