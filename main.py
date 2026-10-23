import http.client
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def listening_pids(port: int) -> set[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"无法检查端口 {port}：{result.stderr.strip()}")
    return {int(pid) for pid in result.stdout.split()}


def belongs_to_project(pid: int, directory: Path, marker: str) -> bool:
    cwd = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    if f"n{directory}" not in cwd:
        return False
    process = subprocess.run(
        ["ps", "-p", str(pid), "-o", "ppid=,command="],
        capture_output=True, text=True,
    ).stdout.strip().split(maxsplit=1)
    if len(process) != 2:
        return False
    parent, command = process
    if marker in command:
        return True
    parent_command = subprocess.run(
        ["ps", "-p", parent, "-o", "command="], capture_output=True, text=True,
    ).stdout
    return marker in parent_command


def wait_ready(process: subprocess.Popen, port: int, path: str):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"端口 {port} 对应的服务启动失败，请查看上方日志")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", path)
            if connection.getresponse().status == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.2)
    raise RuntimeError(f"等待端口 {port} 启动超时，请查看上方日志")


def main():
    for command in ("npm", "lsof", "ps"):
        if not shutil.which(command):
            raise RuntimeError(f"缺少 {command}，请先安装后重试")

    services = [
        (8000, ROOT, "backend.main:app", "/openapi.json", [
            sys.executable, "-m", "uvicorn", "backend.main:app",
            "--reload", "--reload-dir", str(ROOT / "backend"),
            "--timeout-graceful-shutdown", "5",  # SSE 长连接不能无限阻塞开发热重载。
            "--host", "127.0.0.1", "--port", "8000",
        ]),
        (5173, ROOT / "frontend", "vite", "/", [
            "npm", "run", "dev", "--", "--port", "5173", "--strictPort",
        ]),
    ]

    running = set()
    for port, directory, marker, _, _ in services:
        for pid in listening_pids(port):
            if not belongs_to_project(pid, directory, marker):
                raise RuntimeError(f"端口 {port} 被其他程序占用（PID {pid}），未停止任何服务")
            running.add(pid)

    print("正在停止本项目已运行的前后端…", flush=True)
    for pid in sorted(running):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while any(listening_pids(service[0]) for service in services):
        if time.monotonic() >= deadline:
            raise RuntimeError("原服务未释放端口，请手动检查后重试")
        time.sleep(0.2)

    processes = []
    try:
        for port, directory, _, path, command in services:
            process = subprocess.Popen(command, cwd=directory, start_new_session=True)
            processes.append(process)
            wait_ready(process, port, path)
        print("\n前端：http://127.0.0.1:5173/\n后端：http://127.0.0.1:8000/docs\n按 Ctrl+C 同时停止前后端。\n", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        print("服务进程已退出，正在停止本次启动的前后端。", flush=True)
    except KeyboardInterrupt:
        print("\n正在停止前后端…", flush=True)
    finally:
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        sys.exit(1)
