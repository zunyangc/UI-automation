"""Print the current user's real Desktop folder path as the first stdout column.

Resolves the Desktop *known folder* via the Windows shell API rather than
assuming ``{home}\\Desktop``. On machines where Desktop is redirected (e.g. by
OneDrive Known Folder Move, common on managed DevBoxes), the real Desktop
lives elsewhere, such as ``C:\\Users\\name\\OneDrive - Company\\Desktop``. A
test captures this via ``$.cols[0]`` and uses ``{vars.desktop}`` to build
absolute, machine-portable Desktop paths (e.g. for post-run cleanup) instead
of the wrong, non-redirected ``{vars.home}\\Desktop`` guess.
"""
import ctypes, sys
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# FOLDERID_Desktop, see:
# https://learn.microsoft.com/windows/win32/shell/knownfolderid
FOLDERID_Desktop = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]

    def __init__(self, guid_str):
        import uuid
        u = uuid.UUID(guid_str)
        self.Data1, self.Data2, self.Data3 = u.time_low, u.time_mid, u.time_hi_version
        rest = u.bytes[8:]
        for i, b in enumerate(rest):
            self.Data4[i] = b


def get_desktop_path():
    guid = GUID(FOLDERID_Desktop)
    path_ptr = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
    )
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath failed: hr={hr:#x}")
    try:
        return path_ptr.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)


def main():
    print(get_desktop_path())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
