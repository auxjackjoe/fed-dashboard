#!/bin/bash
# Double-click this file (in Finder) to put the dashboard online with GitHub.
# Safe to run again: it skips whatever is already done.
cd "$(dirname "$0")" || exit 1
GH="$HOME/.local/bin/gh"
REPO="fed-dashboard"
say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
fail() { printf "\n\033[31mStopped: %s\033[0m\n" "$1"; read -n 1 -s -r -p "Press any key to close."; exit 1; }

[ -x "$GH" ] || fail "GitHub tool not found at $GH"

say "Step 1 of 5 - Sign in to GitHub"
if ! "$GH" auth status -h github.com >/dev/null 2>&1; then
  echo "A browser window will open. Sign in, enter the code shown below, then click Authorize."
  echo "(If asked 'Authenticate Git with your GitHub credentials?', just press Enter.)"
  "$GH" auth login --hostname github.com --git-protocol https --web --scopes workflow || fail "sign-in did not finish"
fi
"$GH" auth setup-git -h github.com >/dev/null 2>&1
USER_LOGIN=$("$GH" api user --jq .login) || fail "could not read your GitHub username"
USER_ID=$("$GH" api user --jq .id)
echo "Signed in as $USER_LOGIN"

say "Step 2 of 5 - Use your private GitHub email on the commit (keeps your real email off the public site)"
NOREPLY="${USER_ID}+${USER_LOGIN}@users.noreply.github.com"
git config user.name "$USER_LOGIN"; git config user.email "$NOREPLY"
if ! git remote get-url origin >/dev/null 2>&1; then
  git commit --amend --no-edit --reset-author -q || fail "could not update the commit"
fi
git add -A && (git diff --cached --quiet || git commit -q -m "Local changes")
if [ -s exclude_words.txt ]; then   # never upload anything containing the private words
  PAT=$(grep -v '^\s*$' exclude_words.txt | paste -sd'|' -)
  HITS=$(git grep -l -i -E "$PAT" HEAD -- . 2>/dev/null)
  [ -z "$HITS" ] || fail "these files contain a word from exclude_words.txt - ask Claude to clean them: $HITS"
fi

say "Step 3 of 5 - Create the repository $USER_LOGIN/$REPO"
if ! "$GH" repo view "$USER_LOGIN/$REPO" >/dev/null 2>&1; then
  "$GH" repo create "$REPO" --public --description "Central bank dove/hawk dashboard" || fail "could not create the repository"
fi
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$USER_LOGIN/$REPO.git"

say "Step 4 of 5 - Turn on the website (GitHub Pages)"
"$GH" api "repos/$USER_LOGIN/$REPO/pages" >/dev/null 2>&1 || \
  "$GH" api -X POST "repos/$USER_LOGIN/$REPO/pages" -f build_type=workflow >/dev/null || fail "could not turn on Pages"
if [ -s exclude_words.txt ]; then   # hidden setting, so the cloud build filters the same words
  "$GH" secret set NEWS_EXCLUDE -R "$USER_LOGIN/$REPO" --body "$(grep -v '^\s*$' exclude_words.txt | paste -sd',' -)" >/dev/null || fail "could not save the word filter"
fi

say "Step 5 of 5 - Upload (about 30 MB, may take a minute)"
git push -u origin main || fail "upload failed"
sleep 5
"$GH" workflow run build.yml -R "$USER_LOGIN/$REPO" >/dev/null 2>&1 && echo "Started the first data rebuild in the cloud (~15-20 min)."

URL="https://$USER_LOGIN.github.io/$REPO/"
say "Done!  Your dashboard: $URL"
echo "It may take 2-3 minutes to appear the first time. It rebuilds itself twice a day - the Mac can be closed."
echo "Progress: https://github.com/$USER_LOGIN/$REPO/actions"
echo "$URL" > "Dashboard address.txt"
sleep 3; open "$URL"
read -n 1 -s -r -p "Press any key to close."
