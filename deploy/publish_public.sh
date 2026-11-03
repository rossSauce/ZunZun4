#!/bin/sh
# Publish the current commit's files to the public GitHub repo WITHOUT the
# private history (older commits contain passwords and session data).
# Each run adds one commit to the public repo containing a snapshot of HEAD.
#
#   deploy/publish_public.sh ["commit message"]
set -e
cd "$(git rev-parse --show-toplevel)"

REMOTE=public
URL=https://github.com/rossSauce/ZunZun4.git
BRANCH=main
MSG=${1:-"Update from private repository ($(git rev-parse --short HEAD))"}

git remote get-url $REMOTE >/dev/null 2>&1 || git remote add $REMOTE $URL

# refuse if HEAD's files contain known secrets or database files
# Extra strings to look for (old passwords etc.) go one per line in
# /etc/zunzunsite3/publish_blocklist, never in this file: it is published.
BLOCK=/etc/zunzunsite3/publish_blocklist
if git grep -qIE "github_pat_[A-Za-z0-9_]{20}|gh[pousr]_[A-Za-z0-9]{20}|-----BEGIN [A-Z ]*PRIVATE KEY-----" HEAD -- . ':(exclude)deploy/git-hooks/*' ':(exclude)deploy/publish_public.sh' \
   || { [ -r $BLOCK ] && git grep -qIF -f $BLOCK HEAD; }; then
  echo "publish: secrets found in HEAD, not publishing" >&2; exit 1
fi
if git ls-tree -r --name-only HEAD | grep -qE '\.(sqlite3|db|pem|key)$|(^|/)(secret_key|email_password|\.env)$'; then
  echo "publish: secret or database files in HEAD, not publishing" >&2; exit 1
fi

TREE=$(git rev-parse HEAD^{tree})
if git fetch -q $REMOTE $BRANCH 2>/dev/null && git rev-parse -q --verify FETCH_HEAD >/dev/null \
   && git merge-base --is-ancestor FETCH_HEAD refs/heads/public-snapshot 2>/dev/null; then
  PARENT="-p FETCH_HEAD"
else
  PARENT=""   # first publish: start a fresh history
fi
COMMIT=$(git commit-tree $TREE $PARENT -m "$MSG")
git update-ref refs/heads/public-snapshot $COMMIT

if [ -z "$PARENT" ]; then
  git push --force $REMOTE public-snapshot:$BRANCH
else
  git push $REMOTE public-snapshot:$BRANCH
fi
echo "published $(git rev-parse --short $COMMIT) to $URL ($BRANCH)"
