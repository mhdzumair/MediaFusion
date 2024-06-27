import os
import sys

import xbmc

sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))

try:
    import pydevd_pycharm

    pydevd_pycharm.settrace(
        "localhost", port=8877, stdoutToServer=True, stderrToServer=True
    )
except ImportError:
    xbmc.log("pydevd_pycharm not imported. not starting debug session", xbmc.LOGWARNING)
except ConnectionRefusedError:
    xbmc.log("Failed to connect to pycharm", xbmc.LOGERROR)
except Exception as e:
    xbmc.log(f"Failed to start debug session: {e}", xbmc.LOGERROR)


if __name__ == "__main__":
    from lib.router import addon_router

    addon_router()
