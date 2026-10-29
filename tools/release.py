"""Build the public source release: a zip of the app's own files from one git commit, plus its SHA-256.

    python tools\\release.py            # from HEAD
    python tools\\release.py <commit>

Only the files in PUBLIC go in (no notes, plans or tool settings), and the build stops if any of them
mentions a personal path or name. The zip comes from `git archive`, so the same commit gives the same bytes
and anyone with the source can rebuild it and compare the hash. Output: dist\\gkeystrokes-<version>-src.zip
and dist\\SHA256SUMS.txt.
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = ["gk", "web", "gkeystrokes.pyw", "requirements.txt", "requirements.lock", "README.md", "PRIVACY.md",
          "tools/selftest.py", "tools/preview.py", "tools/release.py"]


def personal_pattern():
    """Paths into a user folder, plus this PC's user name, git user name and email (read here, never written
    into the source, so the check itself publishes nothing personal)."""
    words = {os.environ.get("USERNAME", ""), os.path.basename(os.path.expanduser("~"))}
    for key in ("user.name", "user.email"):
        v = subprocess.run(["git", "-C", ROOT, "config", key], capture_output=True, text=True).stdout.strip()
        words |= {v, v.split("@")[0]}
    words = sorted(w for w in words if len(w) >= 4)
    alts = [r"[A-Za-z]:\\\\?Users\\\\?(?!<|%|\{|\.\.\.)[A-Za-z0-9_.-]+"] + [re.escape(w) for w in words]
    return re.compile("|".join(alts), re.I)


def git(*args, text=True):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, check=True, text=text).stdout


def main():
    commit = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    sha = git("rev-parse", commit).strip()
    files = git("ls-tree", "-r", "--name-only", sha, "--", *PUBLIC).split()
    found = []
    personal = personal_pattern()
    for f in files:
        body = git("show", "%s:%s" % (sha, f), text=False).decode("utf-8", "replace")
        for n, line in enumerate(body.splitlines(), 1):
            if personal.search(line):
                found.append("%s:%d: %s" % (f, n, line.strip()[:100]))
    if found:
        print("Personal paths or names found; nothing was built:\n  " + "\n  ".join(found))
        return 1
    version = re.search(r'__version__ = "([^"]+)"', git("show", sha + ":gk/__init__.py")).group(1)
    name = "gkeystrokes-%s-src.zip" % version
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", name)
    git("archive", "--format=zip", "--prefix=gkeystrokes-%s/" % version, "-o", out, sha, "--", *PUBLIC)
    digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
    with open(os.path.join(ROOT, "dist", "SHA256SUMS.txt"), "a", encoding="utf-8") as f:
        f.write("%s  %s  (commit %s)\n" % (digest, name, sha))
    print("%s\n%d files, commit %s\nSHA-256 %s" % (out, len(files), sha, digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
