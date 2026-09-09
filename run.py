"""Start both services using the active Python interpreter. Ctrl+C stops both."""
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    children = []
    commands = [[sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8001"],
                [sys.executable, "-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--server.headless", "true"]]
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=root, env=os.environ.copy()))
        print("Frontend: http://127.0.0.1:8501\nAPI docs: http://127.0.0.1:8001/docs", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
