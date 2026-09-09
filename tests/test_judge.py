import asyncio
import shutil

import pytest

from backend.judge import Runner, normalize
from backend.models import Language
from conftest import PROBLEM


@pytest.mark.parametrize("actual,expected", [("3 \t\n\n", "3"), ("a  \nb\n", "a\nb"), ("3\r\n", "3")])
def test_output_normalization(actual, expected):
    assert normalize(actual) == expected


def test_output_preserves_meaningful_whitespace():
    assert normalize(" 3\n") != normalize("3")
    assert normalize("a\n\nb") != normalize("a\nb")
    assert normalize("a  b") != normalize("a b")


@pytest.mark.parametrize("code,verdict", [("print(3)", "AC"), ("print(4)", "WA"), ("raise ZeroDivisionError('example')", "RE"),
                                        ("while True: pass", "TLE"), ("x=bytearray(300*1024*1024)", "MLE"), ("while True: print('x'*10000)", "RE")])
async def test_python_verdicts(code, verdict):
    language = Language(name="python", file_ext=".py", run_cmd="python3 {src}").model_dump()
    result = await Runner().judge(dict(PROBLEM, time_limit=3 if "10000" in code else 0.5, memory_limit=64), language, code)
    assert result["details"][0]["result"] == verdict, result
    assert result["counts"] == 10


@pytest.mark.skipif(not shutil.which("g++"), reason="g++ not installed")
@pytest.mark.parametrize("code,verdict", [('#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b;}', "AC"), ('int main( {', "CE")])
async def test_cpp_compilation(code, verdict):
    language = Language(name="cpp", file_ext=".cpp", compile_cmd="g++ {src} -std=c++14 -o {exe}", run_cmd="{exe}").model_dump()
    result = await Runner().judge(PROBLEM, language, code)
    assert result["run_info"]["result"] == verdict, result


async def test_language_limit_fallback():
    language = Language(name="python", file_ext=".py", run_cmd="python3 {src}", time_limit=0.1).model_dump()
    result = await Runner().judge(dict(PROBLEM, time_limit=None), language, "import time; time.sleep(1)")
    assert result["run_info"]["result"] == "TLE"


async def test_runner_cancellation_cleans_process_tree(tmp_path):
    import psutil
    import sys
    from pathlib import Path
    runner = Runner()
    source = tmp_path / "main.py"
    marker = tmp_path / "child.pid"
    source.write_text("import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\nPath('child.pid').write_text(str(p.pid))\ntime.sleep(30)", encoding="utf-8")
    handle = asyncio.create_task(runner.run([sys.executable, str(source)], Path(tmp_path), "", 30, 128))
    for _ in range(200):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    pid = int(marker.read_text())
    handle.cancel()
    await asyncio.gather(handle, return_exceptions=True)
    for _ in range(100):
        if not psutil.pid_exists(pid):
            break
        await asyncio.sleep(0.01)
    assert not psutil.pid_exists(pid)
