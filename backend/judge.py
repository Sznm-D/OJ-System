"""Asynchronous judge with bounded output, process-tree cleanup and optional Docker isolation."""
import asyncio
import os
import shlex
import shutil
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psutil


def normalize(output):
    # Preserve leading spaces, internal blank lines and token boundaries.
    return "\n".join(line.rstrip(" \t\r") for line in output.split("\n")).rstrip("\n")


def command_args(template, src, exe):
    args = [token.replace("{src}", str(src)).replace("{exe}", str(exe)) for token in shlex.split(template)]
    if args[0] in {"python", "python3"}:
        args[0] = sys.executable
        if "-I" not in args:
            args.insert(1, "-I")
    return args


def terminate_tree(pid):
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for process in reversed(children):
            try:
                process.kill()
            except psutil.Error:
                pass
        parent.kill()
    except psutil.Error:
        pass


class Runner:
    def __init__(self, mode="local", image="oj-lab-runner:latest"):
        if mode not in {"local", "docker"}:
            raise ValueError("OJ_RUNNER must be local or docker")
        self.mode, self.image = mode, image

    async def run(self, args, cwd, stdin, seconds, memory_mb):
        container_name = None
        if self.mode == "docker":
            container_name = "oj-" + uuid.uuid4().hex
            docker_args = []
            for arg in args:
                if arg == sys.executable:
                    arg = "python3"
                elif arg.startswith(str(cwd)):
                    arg = "/work/" + Path(arg).name
                docker_args.append(arg)
            args = ["docker", "run", "--name", container_name, "--rm", "-i", "--network=none",
                    "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=32",
                    "--cpus=1", f"--memory={memory_mb}m", f"--memory-swap={memory_mb}m",
                    "--user=65534:65534", "--tmpfs=/tmp:rw,nosuid,size=64m", "-w", "/work",
                    "--mount", f"type=bind,source={cwd},target=/work", self.image, *docker_args]
        elif sys.platform == "linux":
            args = [sys.executable, str(Path(__file__).with_name("limit_exec.py")), str(seconds), str(memory_mb), *args]
        # Never pass server-side model credentials or arbitrary inherited secrets to submissions.
        env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if k in os.environ}
        env.update({"HOME": str(cwd), "TMPDIR": str(cwd), "TEMP": str(cwd), "TMP": str(cwd), "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"})
        started = time.perf_counter()
        windows_local = sys.platform == "win32" and self.mode == "local"
        process = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=sys.platform != "win32",
            creationflags=0x00000004 if windows_local else 0,
        )
        job = None
        if windows_local:
            from .windows_job import WindowsJob
            try:
                job = WindowsJob(process.pid, memory_mb)
                psutil.Process(process.pid).resume()
            except BaseException:
                if job:
                    job.close()
                process.kill()
                await process.wait()
                raise
        # Process creation (notably Windows executable inspection) is not execution time.
        started = time.perf_counter()
        collected = {"stdout": bytearray(), "stderr": bytearray()}
        reason, peak, size = "", 0, 0

        async def read_stream(stream, label):
            nonlocal reason, size
            while chunk := await stream.read(8192):
                size += len(chunk)
                remaining = max(0, 256000 - len(collected[label]))
                collected[label].extend(chunk[:remaining])
                if size > 512000:
                    reason = "RE"
                    break

        async def feed():
            try:
                process.stdin.write(stdin.encode())
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        workers = [asyncio.create_task(read_stream(process.stdout, "stdout")),
                   asyncio.create_task(read_stream(process.stderr, "stderr")), asyncio.create_task(feed())]
        try:
            while process.returncode is None:
                elapsed = time.perf_counter() - started
                if elapsed > seconds:
                    reason = reason or "TLE"
                if self.mode == "local":
                    try:
                        parent = psutil.Process(process.pid)
                        rss = sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)] if p.is_running())
                        peak = max(peak, rss / 1024**2)
                        if peak > memory_mb:
                            reason = "MLE"
                    except psutil.Error:
                        pass
                if reason:
                    break
                await asyncio.sleep(0.005)
        finally:
            # Cleanup runs on success, failure, application shutdown and AI cancellation.
            if job:
                peak = max(peak, job.peak_memory())
                job.close()
            if container_name:
                cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await cleanup.wait()
            if sys.platform != "win32":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                await asyncio.to_thread(terminate_tree, process.pid)
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            try:
                await asyncio.wait_for(asyncio.gather(*workers), timeout=2)
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
        stdout = collected["stdout"].decode("utf-8", errors="replace")
        stderr = collected["stderr"].decode("utf-8", errors="replace").replace(str(cwd), "<workspace>")
        if not reason and process.returncode:
            if "MemoryError" in stderr or "bad_alloc" in stderr or (self.mode == "docker" and process.returncode == 137):
                reason = "MLE"
            elif process.returncode == -getattr(signal, "SIGXCPU", 24):
                reason = "TLE"
            else:
                reason = "RE"
        if size > 512000:
            stderr = "输出超过 512 KB 限制。"
        return {"result": reason or "AC", "time": round(time.perf_counter() - started, 4),
                "memory": round(peak, 2) if self.mode == "local" else None,
                "stdout": stdout, "stderr": stderr[:4000], "exit_code": process.returncode}

    async def judge(self, problem, language, code, on_case=None, capture_output=False):
        seconds = problem.get("time_limit") or language.get("time_limit") or 3
        memory = problem.get("memory_limit") or language.get("memory_limit") or 128
        result = {"status": "success", "score": 0, "counts": len(problem["testcases"]) * 10,
                  "compile_info": None, "run_info": None, "error_info": "", "details": []}
        with tempfile.TemporaryDirectory(prefix="oj-run-") as temporary:
            cwd = Path(temporary).resolve()
            cwd.chmod(0o777)
            src, exe = cwd / ("main" + language["file_ext"]), cwd / ("main.exe" if os.name == "nt" and self.mode == "local" else "main")
            await asyncio.to_thread(src.write_text, code, encoding="utf-8")
            if language["compile_cmd"]:
                compile_result = await self.run(command_args(language["compile_cmd"], src, exe), cwd, "", 30, 1024)
                result["compile_info"] = {"result": "success" if compile_result["result"] == "AC" else "error",
                                          "message": compile_result["stderr"]}
                if compile_result["result"] != "AC":
                    result["run_info"] = {"result": "CE", "message": "编译失败，未执行测试点"}
                    result["details"] = [{"id": i + 1, "result": "CE", "time": 0, "memory": 0} for i in range(len(problem["testcases"]))]
                    return result
            for i, case in enumerate(problem["testcases"]):
                outcome = await self.run(command_args(language["run_cmd"], src, exe), cwd, case["input"], seconds, memory)
                verdict = outcome["result"]
                if verdict == "AC" and normalize(outcome["stdout"]) != normalize(case["output"]):
                    verdict = "WA"
                result["score"] += 10 if verdict == "AC" else 0
                result["details"].append({"id": i + 1, "result": verdict, "time": outcome["time"],
                                          "memory": outcome["memory"], "message": outcome["stderr"]})
                if capture_output:
                    result["details"][-1]["stdout"] = outcome["stdout"]
                if on_case:
                    await on_case(i + 1, len(problem["testcases"]))
            failed = next((case["result"] for case in result["details"] if case["result"] != "AC"), None)
            messages = [c["message"] for c in result["details"] if c.get("message")]
            result["run_info"] = {"result": failed or "AC", "message": f"{len(result['details'])} test cases finished" + ("\n" + messages[0] if messages else "")}
            return result


class JudgeQueue:
    def __init__(self, store, runner):
        self.store, self.runner = store, runner
        self.gate = asyncio.Semaphore(1)
        self.tasks = {}

    def start(self, submission):
        sid = submission["submission_id"]
        task = asyncio.create_task(self.evaluate(submission), name=f"judge-{sid}")
        self.tasks[sid] = task
        task.add_done_callback(lambda t: self.tasks.pop(sid, None) if self.tasks.get(sid) is t else None)

    async def evaluate(self, submission):
        try:
            async with self.gate:
                result = await self.runner.judge(submission["problem_snapshot"], submission["language_snapshot"], submission["code"])
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {"status": "error", "score": 0, "counts": len(submission["problem_snapshot"]["testcases"]) * 10,
                      "compile_info": None, "run_info": {"result": "UNK", "message": "评测执行器不可用"},
                      "error_info": "无法完成评测，请检查执行器或编译器配置。", "details": []}
        async with self.store.lock:
            submission.update(result)
            await self.store.put("submissions", submission["submission_id"], submission)

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def runtime_available(mode):
    return {"mode": mode, "python": True, "cpp": bool(shutil.which("g++")), "docker": bool(shutil.which("docker"))}
