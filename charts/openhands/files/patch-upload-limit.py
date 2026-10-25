#!/usr/bin/env python3
"""Raise the browser's limit on files attached to a chat message.

Agent Canvas refuses any attachment above 3 MB, per file and in total, before it
uploads anything. The agent server streams uploads to disk and has no limit of its
own. Argument: the new limit in MB, 0 for no limit. Fails the build when the
frontend no longer has the expected shape.
"""
import glob
import re
import sys

MB = int(sys.argv[1])
ASSETS = sys.argv[2] if len(sys.argv) > 2 else "/opt/agent-canvas/frontend/assets"
LIMIT = "Number.MAX_SAFE_INTEGER" if MB == 0 else f"{MB}*1024*1024"
LABEL = "the size limit" if MB == 0 else f"{MB}MB"

CONST = re.compile(r"(\w+)=3\*1024\*1024,(\w+)=3\*1024\*1024")
files = [f for f in glob.glob(f"{ASSETS}/*.js") if CONST.search(open(f).read())]
if len(files) != 1:
    sys.exit(f"patch-upload-limit: {len(files)} files with the 3MB limits, expected 1")
src = open(files[0]).read()
src, n1 = CONST.subn(rf"\1={LIMIT},\2={LIMIT}", src)
src, n2 = re.subn(r"Files exceeding 3MB", f"Files exceeding {LABEL}", src)
src, n3 = re.subn(r"exceeding the 3MB limit", f"exceeding {LABEL}" if MB == 0 else f"exceeding the {LABEL} limit", src)
if (n1, n2, n3) != (1, 1, 1):
    sys.exit(f"patch-upload-limit: {files[0]}: changes {n1},{n2},{n3}, expected 1,1,1")
open(files[0], "w").write(src)
print(f"patch-upload-limit: {files[0]}: limit {LIMIT}")
