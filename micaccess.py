"""Windows microphone privacy settings (read-only).

If any of these is "Deny", Windows hands desktop apps pure silence instead of mic
audio, even when the right microphone is selected.
"""
import winreg

_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
_CHECKS = [
    (winreg.HKEY_LOCAL_MACHINE, _KEY, "Microphone access is turned off for this PC"),
    (winreg.HKEY_CURRENT_USER, _KEY, "Microphone access is turned off"),
    (winreg.HKEY_CURRENT_USER, _KEY + r"\NonPackaged",
     '"Let desktop apps access your microphone" is turned off'),
]


def mic_access_problems():
    """Return a list of human-readable reasons Windows blocks the mic (empty = allowed)."""
    problems = []
    for hive, path, reason in _CHECKS:
        try:
            with winreg.OpenKey(hive, path) as k:
                value, _ = winreg.QueryValueEx(k, "Value")
        except OSError:
            continue                      # not set = Windows default (allowed)
        if str(value).lower() == "deny":
            problems.append(reason)
    return problems
