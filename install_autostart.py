#!/usr/bin/env python3
"""One-time setup (Mac): makes the dashboard start by itself whenever you log in.
Run once:   python3 install_autostart.py        Undo:   python3 install_autostart.py --remove
After that just open  http://localhost:8769/fed_cheatsheet.html  (bookmark it)."""
import os, sys, subprocess, plistlib
HERE = os.path.dirname(os.path.abspath(__file__))
LABEL = "com.ryan.fed-dashboard"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
if "--remove" in sys.argv:
    subprocess.run(["launchctl", "unload", PLIST]); 
    if os.path.exists(PLIST): os.remove(PLIST)
    print("Removed. The dashboard will no longer start by itself."); sys.exit()
os.makedirs(os.path.dirname(PLIST), exist_ok=True)
plistlib.dump({
    "Label": LABEL,
    "ProgramArguments": [sys.executable, "-I", os.path.join(HERE, "server.py")],
    "WorkingDirectory": HERE,
    "EnvironmentVariables": {"FED_PROXY": ""},
    "RunAtLoad": True, "KeepAlive": True,
    "StandardOutPath": os.path.join(HERE, "server.log"), "StandardErrorPath": os.path.join(HERE, "server.log"),
}, open(PLIST, "wb"))
subprocess.run(["launchctl", "unload", PLIST], stderr=subprocess.DEVNULL)
subprocess.run(["launchctl", "load", PLIST])
print("Done. The dashboard is running now and will start at every login.")
print("Open:  http://localhost:8769/fed_cheatsheet.html   (bookmark it)")
