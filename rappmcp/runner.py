"""`python -m rappmcp.runner` keeps working: it is brainstem-mcp's worker, brainstem_mcp.runner."""
import sys

from brainstem_mcp import runner as _runner

if __name__ == "__main__":
    sys.exit(_runner.main(sys.argv[1:]))

sys.modules[__name__] = _runner
