"""Opportunistically drive the ``Android SDK - License Agreement`` install flow.

The Visual Studio MAUI workload does two things during the initial build if
the Android SDK is not fully installed:

1. Emits an Error-List warning like
   ``The project MauiAppXX is missing Android SDKs required for building.
   Double-click on this message and follow the prompts to install them.``
   The warning does **not** auto-launch any installer -- the user must
   double-click that Error-List row to kick off the install flow.
2. Once the row is double-clicked, VS pops:
     a. ``Android SDK - License Agreement`` -> click ``Accept``
     b. ``User Account Control`` -> click ``Yes``
   after which the SDK downloads/installs and the build can be retried.

This script polls for ``--timeout-ms`` milliseconds. Each cycle it:
  * scans the VS window (if ``--vs-hwnd`` is passed) for the missing-SDK
    warning row and double-clicks it (once);
  * scans the desktop for the License Agreement / UAC dialogs and dismisses
    whichever appear.

It always exits 0 (opportunistic safety net). If ``--vs-hwnd`` is not
provided the warning-detection step is skipped and this behaves like the
original license/UAC dismisser.

If SDK-install activity is detected (any of: warning double-clicked,
license Accept clicked, UAC Yes clicked), the poll deadline is extended by
``--post-work-ms`` to give the SDK download time to finish before we
return. Otherwise the initial ``--timeout-ms`` is used unchanged.
"""
import argparse, os, sys, time

try:
    from pywinauto import Desktop, Application
except Exception as e:
    print(f"ERROR: pywinauto import failed: {e}", file=sys.stderr); sys.exit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WARNING_PHRASE = "missing android sdks required for building"

# Gate UAC "Yes" clicks on a License Accept having just happened: VS always
# shows "Android SDK - License Agreement" -> Accept immediately before the
# matching UAC elevation prompt for that same SDK-install flow. Without this
# gate, a bare "User Account Control" / "Do you want to allow" window match is
# far too broad to leave running continuously for an entire test case (see
# sdk_dialog_watcher.py) -- it would happily approve an unrelated elevation
# prompt (Windows Update, another app, etc.) that happens to appear while the
# watcher is polling. `UAC_GRACE_SECONDS` is generous (the License->UAC
# transition is normally near-instant) without being so long it could still
# match a later, unrelated prompt.
#
# The "License was just accepted" fact has to survive across SEPARATE process
# invocations -- e.g. the License dialog can be accepted by the inline check
# at project-creation time (one `handle_android_sdk_dialogs.py` process) while
# the matching UAC prompt only appears a little later, polled for by either
# the next inline check or the continuous background watcher (each its own
# process). A plain in-memory flag would not be visible across that process
# boundary, so the "last license accept" timestamp is persisted to a small
# state file instead. CALLERS SHOULD PASS `--license-state-file` POINTING AT
# A PATH UNDER THAT RUN'S OWN `{artifacts.screenshot_dir}` (every test case
# gets its own, uniquely-timestamped one) rather than relying on the default
# below -- a run-scoped path means a crashed/killed previous run can never
# leave behind a stale "license accepted" timestamp that a later, unrelated
# run picks up and treats as its own. The default (a single well-known path
# under %LOCALAPPDATA%, mirroring run_test.py's own single-active-run marker
# convention) only exists for ad hoc/manual invocations that don't have a
# screenshot_dir to put it under.
#
# Even with run-scoping, a grace-period match alone can't rule out a UAC
# prompt that was ALREADY open (for something unrelated, e.g. Windows Update)
# at the moment a License was accepted -- it would still fall inside the
# window. `--ignore-preexisting-uac` (used by sdk_dialog_watcher.py, see
# `list_uac_hwnds()`) closes that gap: anything already on-screen before this
# invocation started polling can't possibly be a prompt OUR SDK flow is about
# to trigger, so it's excluded regardless of timing.
UAC_GRACE_SECONDS = 60


def _default_license_state_file():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ui-automation")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "android_sdk_license_grace.flag")


def _mark_license_accepted(state_file):
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(repr(time.time()))
    except Exception:
        pass


def _license_recently_accepted(state_file, grace_seconds):
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            ts = float(f.read().strip())
    except Exception:
        return False
    return (time.time() - ts) < grace_seconds


def try_click(win, names):
    for name in names:
        try:
            btn = win.child_window(title=name, control_type="Button")
            if btn.exists(timeout=0.5):
                btn.click_input()
                return name
        except Exception:
            continue
    return None


def _elem_text(elem):
    """Best-effort accessible text for a UIA element."""
    parts = []
    try:
        t = elem.window_text() or ""
        if t: parts.append(t)
    except Exception:
        pass
    try:
        lp = elem.legacy_properties() or {}
        for k in ("Name", "Value", "Description", "Help"):
            v = lp.get(k) or ""
            if v: parts.append(v)
    except Exception:
        pass
    return " | ".join(parts).lower()


def try_double_click_warning(vs_hwnd):
    """Find the missing-SDK warning row inside VS and double-click it.

    Returns True if a matching element was found and double-clicked, else
    False. Best-effort: never raises.
    """
    try:
        app = Application(backend="uia").connect(handle=int(vs_hwnd), timeout=2)
        win = app.window(handle=int(vs_hwnd))
    except Exception as e:
        print(f"    warning-scan: could not connect to VS hwnd {vs_hwnd}: {e}")
        return False

    try:
        elements = win.descendants()
    except Exception as e:
        print(f"    warning-scan: descendants() failed: {e}")
        return False

    for elem in elements:
        try:
            ct = ""
            try: ct = elem.element_info.control_type or ""
            except Exception: pass
            # Error List rows are typically DataItem / Custom / ListItem
            if ct and ct not in ("DataItem", "Custom", "ListItem", "Text", "Group", "TreeItem"):
                continue
            text = _elem_text(elem)
            if WARNING_PHRASE in text:
                try:
                    elem.double_click_input()
                    print(f"    warning-scan: double-clicked Error-List row ({ct}) matching '{WARNING_PHRASE}'")
                    return True
                except Exception as e:
                    print(f"    warning-scan: found match but double_click failed: {e}")
                    return False
        except Exception:
            continue
    return False


def _is_uac_window(title):
    title = (title or "").strip()
    return title == "User Account Control" or "Do you want to allow" in title


def list_uac_hwnds():
    """Snapshot the handles of every currently-open UAC-titled window.

    Used by long-running callers (sdk_dialog_watcher.py) to record a
    baseline of UAC prompts that already existed *before* this run could
    possibly have triggered the Android SDK install flow. Those pre-
    existing prompts are never ours to approve -- see `ignore_hwnds` on
    `handle_dialogs_once` -- no matter how soon a License Accept happens
    to follow.
    """
    hwnds = set()
    for backend in ("uia", "win32"):
        try:
            for w in Desktop(backend=backend).windows():
                try:
                    if _is_uac_window(w.window_text()):
                        hwnds.add(w.handle)
                except Exception:
                    continue
        except Exception:
            continue
    return hwnds


def handle_dialogs_once(license_state_file=None, ignore_hwnds=None):
    state_file = license_state_file or _default_license_state_file()
    ignore_hwnds = ignore_hwnds or ()
    handled = []
    for backend in ("uia", "win32"):
        try:
            for w in Desktop(backend=backend).windows():
                try:
                    title = (w.window_text() or "").strip()
                except Exception:
                    continue
                if "Android SDK" in title and "License" in title:
                    clicked = try_click(w, ("Accept", "I Accept", "Yes"))
                    if clicked:
                        handled.append(f"license {title!r} -> {clicked}")
                        _mark_license_accepted(state_file)
                elif _is_uac_window(title):
                    try:
                        hwnd = w.handle
                    except Exception:
                        hwnd = None
                    if hwnd is not None and hwnd in ignore_hwnds:
                        # Present before this run could have triggered the SDK
                        # flow (or already handled this cycle) -- never ours.
                        continue
                    if not _license_recently_accepted(state_file, UAC_GRACE_SECONDS):
                        # No recent Android SDK License Accept -> not our flow;
                        # leave this UAC prompt alone (see UAC_GRACE_SECONDS).
                        continue
                    clicked = try_click(w, ("Yes",))
                    if clicked:
                        handled.append(f"UAC {title!r} -> {clicked}")
        except Exception:
            continue
    return handled


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vs-hwnd", dest="vs_hwnd", default=None,
                   help="VS window hwnd (int). If set, the Error List will be scanned "
                        "for the missing-SDK warning and double-clicked when found.")
    p.add_argument("--timeout-ms", dest="timeout_ms", type=int, default=3000)
    p.add_argument("--poll-ms", dest="poll_ms", type=int, default=500)
    p.add_argument("--post-work-ms", dest="post_work_ms", type=int, default=900000,
                   help="If SDK-install activity is detected, extend the poll deadline "
                        "by this many ms (default 15 min) so the SDK download can "
                        "finish before we return.")
    p.add_argument("--license-state-file", dest="license_state_file", default=None,
                   help="Path used to remember the last Android SDK License Accept "
                        "timestamp, so a UAC prompt is only auto-approved shortly "
                        "after that (see UAC_GRACE_SECONDS). Defaults to a shared "
                        "per-devbox file under %%LOCALAPPDATA%%; pass this explicitly "
                        "to share state with another cooperating process (e.g. "
                        "sdk_dialog_watcher.py) that isn't using the default -- CSV "
                        "callers should pass a path under {artifacts.screenshot_dir} "
                        "so stale state from a previous/crashed run can never leak "
                        "into a new one (a fresh run always gets a fresh directory).")
    p.add_argument("--ignore-preexisting-uac", dest="ignore_preexisting_uac",
                   action="store_true",
                   help="Snapshot every UAC-titled window already open before polling "
                        "starts, and never approve one of those -- a prompt that "
                        "predates this invocation cannot belong to an SDK flow it "
                        "triggers. Recommended for any long-running/continuous caller.")
    a = p.parse_args()

    start = time.time()
    deadline = start + a.timeout_ms / 1000.0
    warning_clicked_once = False
    events = []
    baseline_uac_hwnds = list_uac_hwnds() if a.ignore_preexisting_uac else ()

    while time.time() < deadline:
        cycle_hits = []

        # 1) VS Error-List warning (once per invocation)
        if a.vs_hwnd and not warning_clicked_once:
            if try_double_click_warning(a.vs_hwnd):
                warning_clicked_once = True
                cycle_hits.append("warning double-clicked")

        # 2) License Agreement + UAC dialogs
        cycle_hits.extend(handle_dialogs_once(a.license_state_file, baseline_uac_hwnds))

        if cycle_hits:
            events.extend(cycle_hits)
            for line in cycle_hits:
                print(f"    {line}")
            # Extend deadline once SDK-install activity is seen so we don't
            # return while the license/UAC/download flow is still in progress.
            new_deadline = time.time() + a.post_work_ms / 1000.0
            if new_deadline > deadline:
                deadline = new_deadline
                print(f"    (deadline extended to {a.post_work_ms} ms after activity)")

        time.sleep(a.poll_ms / 1000.0)

    if events:
        print(f"handled {len(events)} sdk-install event(s)")
    else:
        print("no android/UAC dialogs or SDK warning seen (opportunistic no-op)")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(0)
