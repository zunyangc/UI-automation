"""Ensure File Explorer shows file extensions for known file types.

Several e2e scenarios locate files in Explorer by their full name including
extension (e.g. "ConsoleApp1-80.zip") so they can later distinguish a .zip
from its same-named extracted folder. A fresh Windows profile has "Hide
extensions for known file types" ON by default (HideFileExt=1), which makes
every such ListItem lookup fail because Explorer renders the name without
its extension. This script flips that registry value to 0 and asks Explorer
to refresh its already-open windows, matching what a user clicking OK in
Folder Options > View would do -- run this once, early, before any Explorer
window used by the test is opened.
"""
import ctypes
import sys
import winreg

KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


def main():
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY_PATH, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "HideFileExt", 0, winreg.REG_DWORD, 0)
    except OSError as e:
        print(f"ERROR: failed to set HideFileExt: {e}", file=sys.stderr)
        sys.exit(2)

    # SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, NULL, NULL) -- the same
    # notification Explorer sends itself after Folder Options > View > OK, so
    # already-open Explorer windows re-render without needing a restart.
    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNF_IDLIST = 0x0000
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    print("HideFileExt set to 0 (file extensions now shown); Explorer notified.")


if __name__ == "__main__":
    main()
