"""Windows controller and one-owner lifecycle for the shared local ledger."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser

from .browser import find_chrome
from .server import Application, make_server
from .store import Problem


class DataDirectoryLock:
    """Hold an OS file lock; keep the file so its identity survives restarts."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.file = None

    def acquire(self):
        if self.file is not None:
            return
        # A sibling lock keeps a fresh data directory absent so trial setup can
        # publish its fully initialized directory by rename on Windows.
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(os.path.normcase(str(self.directory)).encode('utf-8')).hexdigest()
        stream = (self.directory.parent / ('.ledger-lock-' + identity + '.lock')).open('a+b')
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise Problem('这份账本已经打开，或数据目录无法锁定。请回到已有的账本控制窗口；'
                          '如需重开，先在原窗口安全退出。不会连接到其他账本。') from exc
        self.file = stream

    def release(self):
        stream, self.file = self.file, None
        if stream is not None:
            # Closing the handle also releases the lock if explicit unlock fails.
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *unused):
        self.release()


def local_addresses():
    """Find private LAN IPv4 candidates without sending a network packet."""
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    result = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
            if address.version == 4 and address.is_private and not (
                address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or address.is_reserved
            ):
                result.append(str(address))
        except ValueError:
            pass
    return sorted(result)


def find_app_browser():
    """Use an installed browser for an app window, with no added web engine."""
    found = shutil.which('msedge.exe')
    if found:
        return found
    for variable in ['PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA', 'ProgramW6432']:
        base = os.environ.get(variable)
        if base:
            candidate = Path(base) / 'Microsoft/Edge/Application/msedge.exe'
            if candidate.is_file():
                return str(candidate)
    return find_chrome()


def open_app_window(url):
    executable = find_app_browser()
    if not executable:
        raise Problem('未找到 Edge 或 Chrome。请使用“打开普通网页”，'
                      '或将下方电脑地址复制到已安装的浏览器。')
    # Only a controller-generated loopback URL is accepted.
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
        raise ValueError('桌面窗口需要本机账本地址')
    return subprocess.Popen([executable, '--app=' + url], close_fds=True)


class LedgerService:
    """Serve one explicitly selected directory and never attach to other servers."""

    def __init__(self, directory, host='0.0.0.0', port=8765, test_mode=False):
        self.directory = Path(directory).resolve()
        self.host = host
        self.port = port
        self.test_mode = test_mode
        self.application = None
        self.server = None
        self.thread = None
        self.directory_lock = DataDirectoryLock(self.directory)
        self._lifecycle = threading.RLock()

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    @property
    def url(self):
        if self.server is None:
            return ''
        return f'http://127.0.0.1:{self.server.server_address[1]}'

    @property
    def phone_urls(self):
        if self.server is None or self.host == '127.0.0.1':
            return []
        port = self.server.server_address[1]
        return [f'http://{address}:{port}' for address in local_addresses()]

    def start(self):
        with self._lifecycle:
            if self.running:
                return self
            if not 0 <= self.port <= 65535:
                raise Problem('端口需要在 0 到 65535 之间')
            self.directory_lock.acquire()
            try:
                # Fail on known conflicts before initializing or migrating a ledger.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    if os.name == 'nt':
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind((self.host, self.port))
                self.application = Application(self.directory, test_mode=self.test_mode)
                self.server = make_server(self.application, self.host, self.port)
                self.thread = threading.Thread(target=self.server.serve_forever,
                                               kwargs={'poll_interval': 0.1},
                                               name='ledger-http', daemon=True)
                self.thread.start()
                return self
            except BaseException as exc:
                self._dispose()
                if isinstance(exc, OSError) and (getattr(exc, 'errno', None) in (48, 98, 10048)
                                                or getattr(exc, 'winerror', None) == 10048):
                    raise Problem(f'端口 {self.port} 正在使用。请先关闭已有的账本控制窗口，'
                                  '或使用 --port 8766 启动。没有连接到占用此端口的程序。') from exc
                raise

    def _dispose(self):
        try:
            if self.server is not None:
                self.server.server_close()
        finally:
            try:
                if self.application is not None:
                    self.application.close()
            finally:
                self.server = self.application = self.thread = None
                self.directory_lock.release()

    def stop(self):
        with self._lifecycle:
            if self.server is not None and self.running:
                self.server.shutdown()
                self.thread.join()
            self._dispose()


def program_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _cmd_argument(value):
    value = str(value)
    if any(character in value for character in '\r\n\0"'):
        raise ValueError('启动文件路径不能包含换行、空字符或引号')
    return '"' + value.replace('%', '%%') + '"'


def create_restore_launcher(directory):
    """Create one shortcut script inside the restored folder, never overwriting."""
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('恢复后的账本文件夹不存在')
    executable = Path(sys.executable).resolve()
    arguments = [str(executable)]
    if not getattr(sys, 'frozen', False):
        # Prefer the equivalent windowed interpreter when the full Python has it.
        windowed = executable.with_name('pythonw.exe')
        if os.name == 'nt' and windowed.is_file():
            arguments[0] = str(windowed)
        arguments.append(str(program_directory() / 'desktop.py'))
    arguments.extend(['--data-dir', str(directory)])
    command = 'start "" ' + ' '.join(_cmd_argument(argument) for argument in arguments)
    contents = '@echo off\r\nsetlocal DisableDelayedExpansion\r\nchcp 65001 >nul\r\n' + command + '\r\n'
    target = directory / '启动恢复账本.cmd'
    with target.open('x', encoding='utf-8', newline='') as stream:
        stream.write(contents)
    return target


def restore_for_desktop(source, directory):
    """Complete recovery independently from the optional convenience launcher."""
    from .restore import restore_backup
    restore_backup(source, directory)
    result = {'directory': Path(directory).resolve(), 'launcher': None, 'launcher_warning': ''}
    try:
        result['launcher'] = create_restore_launcher(directory)
    except Exception as exc:
        # The ledger has already recovered successfully; never claim otherwise.
        result['launcher_warning'] = str(exc)
    return result


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description='档口账本：Windows 与手机共用一本账')
    parser.add_argument('--data-dir', help='账本目录；默认为软件旁的 data')
    parser.add_argument('--test-mode', action='store_true', help='使用独立模拟账本')
    parser.add_argument('--port', type=int)
    parser.add_argument('--host', choices=['0.0.0.0', '127.0.0.1'], default='0.0.0.0')
    parser.add_argument('--no-auto-open', action='store_true', help='只打开控制窗口')
    args = parser.parse_args(argv)
    args.data_dir = args.data_dir or str(program_directory() / ('test-data' if args.test_mode else 'data'))
    args.port = args.port if args.port is not None else (8767 if args.test_mode else 8765)
    return args


def main(argv=None):
    # Keep tkinter out of server/test imports and require it only for the controller.
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    args = parse_arguments(argv)
    service = LedgerService(args.data_dir, args.host, args.port, args.test_mode)
    root = tk.Tk()
    root.title('档口账本 · 启动控制台' + ('【练习账本】' if args.test_mode else ''))
    root.geometry('700x620')
    root.minsize(620, 590)
    root.configure(bg='#f3f5f1')
    style = ttk.Style(root)
    if 'vista' in style.theme_names():
        style.theme_use('vista')
    style.configure('TLabel', font=('Microsoft YaHei UI', 10))
    style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(10, 7))
    content = ttk.Frame(root, padding=24)
    content.pack(fill='both', expand=True)
    content.columnconfigure(0, weight=1)
    ttk.Label(content, text='档口账本', font=('Microsoft YaHei UI', 22, 'bold')).grid(sticky='w')
    status = tk.StringVar(value='正在打开账本，请稍候……')
    ttk.Label(content, textvariable=status).grid(sticky='w', pady=(8, 16))
    if args.test_mode:
        ttk.Label(content, text='练习账本：模拟数据保存在独立目录，请勿录入正式账目。').grid(sticky='w', pady=(0, 12))
    fields = {}
    for label in ['数据保存位置', '本次连接码', '电脑网页地址']:
        ttk.Label(content, text=label).grid(sticky='w', pady=(7, 3))
        variable = tk.StringVar(value=str(service.directory) if label == '数据保存位置' else '正在准备')
        ttk.Entry(content, textvariable=variable, state='readonly').grid(sticky='ew')
        fields[label] = variable
    ttk.Label(content, text='手机地址（与电脑连接同一个 Wi-Fi）').grid(sticky='w', pady=(12, 3))
    phone = tk.Text(content, height=3, wrap='word', relief='solid', borderwidth=1,
                    font=('Microsoft YaHei UI', 10))
    phone.grid(sticky='ew')
    phone.configure(state='disabled')
    ttk.Label(content, text='手机输入上面的地址和连接码。多个地址可逐个尝试；连接失败请检查专用网络防火墙。',
              wraplength=620).grid(sticky='w', pady=(6, 12))
    actions = ttk.Frame(content)
    actions.grid(sticky='ew', pady=6)
    events = queue.Queue()
    phase = {'value': 'starting', 'close_requested': False, 'restoring': False}

    def show_phone_addresses():
        phone.configure(state='normal')
        phone.delete('1.0', 'end')
        phone.insert('1.0', '\n'.join(service.phone_urls) or '暂未发现局域网地址。请确认电脑已连接店内 Wi-Fi。')
        phone.configure(state='disabled')

    def open_window():
        try:
            open_app_window(service.url)
        except (OSError, Problem, ValueError) as exc:
            messagebox.showerror('桌面窗口未打开', str(exc), parent=root)

    def open_web():
        try:
            if not webbrowser.open(service.url):
                raise Problem('浏览器未自动打开，请复制电脑网页地址手动打开。')
        except (OSError, Problem) as exc:
            messagebox.showerror('网页未打开', str(exc), parent=root)

    def restore():
        source = filedialog.askopenfilename(parent=root, title='选择完整备份 ZIP',
                                            filetypes=[('完整账本备份', '*.zip')])
        if not source:
            return
        parent = filedialog.askdirectory(parent=root, title='选择新账本文件夹的保存位置')
        if not parent:
            return
        name = simpledialog.askstring('新账本文件夹', '填写一个尚不存在的文件夹名称：',
                                      initialvalue='恢复后的账本', parent=root)
        if name is None:
            return
        name = name.strip()
        if not name or name in ('.', '..') or any(character in name for character in '<>:"/\\|?*'):
            messagebox.showerror('名称无效', '请填写一个普通文件夹名称，不要包含路径或特殊符号。', parent=root)
            return
        destination = Path(parent) / name
        if destination.exists():
            messagebox.showerror('文件夹已经存在', '请选择一个新的文件夹名称，恢复不会覆盖已有账本。', parent=root)
            return
        phase['restoring'] = True
        restore_button.state(['disabled'])

        def restore_work():
            try:
                result = restore_for_desktop(source, destination)
                events.put(('restore', (result, None)))
            except Exception as exc:
                events.put(('restore', (None, exc)))
        threading.Thread(target=restore_work, name='ledger-restore', daemon=True).start()

    buttons = [ttk.Button(actions, text='打开软件窗口', command=open_window),
               ttk.Button(actions, text='打开普通网页', command=open_web),
               ttk.Button(actions, text='刷新手机地址', command=show_phone_addresses)]
    for button in buttons:
        button.pack(side='left', padx=(0, 8))
        button.state(['disabled'])
    ttk.Label(content, text='请保持此控制窗口和电脑运行。关闭记账页面不会停止服务；安全退出后手机也会断开。',
              wraplength=620).grid(sticky='w', pady=(12, 10))
    restore_button = ttk.Button(content, text='从备份恢复到新文件夹', command=restore)
    restore_button.grid(sticky='w', pady=(0, 8))
    restore_button.state(['disabled'])

    def work(action):
        try:
            service.start() if action == 'start' else service.stop()
            events.put((action, None))
        except Exception as exc:
            events.put((action, exc))

    def stop_service():
        phase['value'] = 'stopping'
        status.set('正在等待当前操作完成并安全退出……')
        for button in buttons + [exit_button, restore_button]:
            button.state(['disabled'])
        threading.Thread(target=work, args=('stop',), daemon=True).start()

    def close():
        if phase['restoring']:
            messagebox.showinfo('正在恢复备份', '请等待备份恢复完成后再退出。当前记账服务继续运行。', parent=root)
            return
        if phase['value'] == 'stopping':
            return
        if phase['value'] == 'starting':
            phase['close_requested'] = True
            status.set('正在完成启动并安全退出……')
            return
        if messagebox.askyesno('安全退出', '退出后电脑和手机的记账页面都会断开。\n已保存的账目会保留。确定退出？', parent=root):
            stop_service()

    exit_button = ttk.Button(content, text='安全退出账本服务', command=close)
    exit_button.grid(sticky='e')
    root.protocol('WM_DELETE_WINDOW', close)

    def poll():
        try:
            action, error = events.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        if action == 'restore':
            result, restore_error = error
            phase['restoring'] = False
            restore_button.state(['!disabled'])
            if restore_error:
                messagebox.showerror('备份未能恢复', str(restore_error), parent=root)
            else:
                destination = result['directory']
                if result['launcher']:
                    next_step = ('当前账本继续运行，尚未切换。请先安全退出，再双击新文件夹内的：\n'
                                 '启动恢复账本.cmd\n\n请保留当前软件文件夹，启动文件需要使用其中的程序。')
                else:
                    next_step = ('账本已恢复，但便捷启动文件未能生成。\n'
                                 f'原因：{result["launcher_warning"]}\n\n'
                                 '请先安全退出，再使用以下参数打开恢复后的账本：\n'
                                 f'--data-dir "{destination}"')
                messagebox.showinfo('已恢复到新文件夹', f'恢复位置：\n{destination}\n\n'
                                    + next_step, parent=root)
            root.after(100, poll)
            return
        if error:
            messagebox.showerror('账本未能启动' if action == 'start' else '退出时出现问题', str(error), parent=root)
            root.destroy()
            return
        if action == 'stop':
            root.destroy()
            return
        phase['value'] = 'running'
        if phase['close_requested']:
            stop_service()
        else:
            status.set('正在运行 · 电脑与手机共用这份账本')
            fields['本次连接码'].set('已设置访问密码，请使用原密码' if service.application.password else service.application.code)
            fields['电脑网页地址'].set(service.url)
            show_phone_addresses()
            for button in buttons + [restore_button]:
                button.state(['!disabled'])
            if not args.no_auto_open:
                open_window()
        root.after(100, poll)

    threading.Thread(target=work, args=('start',), daemon=True).start()
    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        service.stop()
    return 0
