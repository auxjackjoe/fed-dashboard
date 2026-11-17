FOMC Watch - setup on your PC
1. Unzip anywhere. Open a terminal there.
2. Run with Anaconda Python as script files, not one-liners:   python -I server.py
3. Open  http://<this-PC-name>:8769/fed_cheatsheet.html   (never file:///)
4. Press "Refresh data" (runs build_data.py live; behind an office proxy put its URL in proxy.txt
   (one line, e.g. http://proxy-host:8080) or set env var FED_PROXY). First build is slow (~1,500 news searches, then cached in cache/).
5. data.json shipped here is DEMO (synthetic, banner shown). A live build replaces it.
   If a live build fetches nothing it keeps the old data.json and reports why.
Optional: itc_2nov.json  {"waller":0.8,...}  (ITC's -1..+1 scale) enables the "ITC (2 Nov)" view.
If commands get blocked, add curl and the Python path to the Claude allow-list in settings.json.
build_data.py: live fetchers are UNTESTED (could not reach those sites when written) - expect small parser fixes.

--- Mac (this machine) ---
Runs automatically at login from ~/Claude/fed_dash (LaunchAgent com.ryan.fed-dashboard, set up by install_autostart.py).
Open http://localhost:8769/fed_cheatsheet.html . Data rebuilds itself when older than 20 h; "Refresh data" forces it.
Don't move the folder back into ~/Documents: macOS blocks background apps from reading there.
If the office proxy isn't reachable, build_data.py now connects directly.
Restart server after editing server.py:  launchctl kickstart -k gui/$(id -u)/com.ryan.fed-dashboard
