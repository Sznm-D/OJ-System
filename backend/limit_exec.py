"""Linux-only child launcher. Never runs inside the FastAPI process."""
import math
import os
import resource
import sys

seconds, megabytes = float(sys.argv[1]), int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_CPU, (max(1, math.ceil(seconds)), max(2, math.ceil(seconds) + 1)))
resource.setrlimit(resource.RLIMIT_AS, (megabytes * 1024**2, megabytes * 1024**2))
resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024**2, 4 * 1024**2))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
