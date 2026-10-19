"""PyInstaller entry point.

A one-folder build only works from the folder it was extracted to: the Qt DLLs live in `_internal` beside the
executable. Running the exe straight out of the zip (Windows extracts just that one file to a temp folder) or
copying the exe on its own gives "DLL load failed while importing QtCore", which tells a user nothing about
what to do. Catch that here and say it plainly, in a message box that does not need Qt.
"""
import os
import sys


def _complain(text):
    """Say it where the user will see it: a console build prints, a windowed one opens a message box."""
    if sys.stdout is not None:
        try:
            sys.stderr.write(text + "\n")
        except Exception:
            pass
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "TTSkyAnnot", 0x10)
    except Exception:
        pass


def _runtime_hint():
    """What is wrong with this copy of the program, phrased for the person who just unzipped it."""
    if not getattr(sys, "frozen", False):
        return None
    here = os.path.dirname(sys.executable)
    if not os.path.isdir(os.path.join(here, "_internal")):
        return ("程序没有完整解压。\n\n"
                "请把下载的 zip 整个解压到一个普通文件夹，再打开里面的 TTSkyAnnot.exe；\n"
                "不要在压缩包里直接双击运行，也不要只把 exe 复制出来。")
    if not os.path.isfile(os.path.join(here, "_internal", "PyQt5", "Qt5", "bin", "Qt5Core.dll")):
        return ("缺少 Qt 运行库（_internal\\PyQt5\\Qt5\\bin\\Qt5Core.dll）。\n\n"
                "请重新完整解压整个 zip；如果杀毒软件把文件隔离了，请恢复后再运行。")
    return None


try:
    from skyannot.cli import main
except Exception as e:                       # a broken install: the reason is almost always the folder
    _complain((_runtime_hint() or "启动失败 / failed to start") + "\n\n" + str(e))
    sys.exit(1)

try:
    code = main()
except Exception as e:                       # the same failure one level down (Qt is imported by the GUI)
    _complain((_runtime_hint() or "启动失败 / failed to start") + "\n\n" + str(e))
    code = 1
sys.exit(code)
