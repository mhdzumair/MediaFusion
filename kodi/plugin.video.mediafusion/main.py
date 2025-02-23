import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))

# Uncomment on Debugging
# try:
# import pydevd_pycharm
#     import socket
#     import xbmc
#
#     # validate if pydevd_pycharm server is running
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.settimeout(3)
#     result = sock.connect_ex(("localhost", 8877))
#     sock.close()
#     if result == 0:
#         xbmc.log("pydevd_pycharm server is running. start debugging", xbmc.LOGINFO)
#         pydevd_pycharm.settrace(
#             "localhost", port=8877, stdoutToServer=True, stderrToServer=True
#         )
#     else:
#         xbmc.log(
#             "pydevd_pycharm server is not running. not starting debug session",
#             xbmc.LOGINFO,
#         )
#
# except ImportError:
#     xbmc.log("pydevd_pycharm not imported. not starting debug session", xbmc.LOGWARNING)
# except ConnectionRefusedError:
#     xbmc.log("Failed to connect to pycharm", xbmc.LOGERROR)
# except Exception as e:
#     xbmc.log(f"Failed to start debug session: {e}", xbmc.LOGERROR)


if __name__ == "__main__":
    from lib.router import addon_router

    addon_router()
