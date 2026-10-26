# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for `curl --unix-socket <docker.sock>`, serving the Docker Engine API calls the notebook
backup scripts make, over a directory tree.

Volumes are directories under ``$FAKE_DOCKER_ROOT/volumes/<name>/``; created containers are JSON
files under ``containers/``. Every request is appended to ``$FAKE_DOCKER_ROOT/calls.log`` (method,
path, body) so a test can check how a container was configured. ``FAKE_DOCKER_DROP_ON_PUT`` names a
file the archive upload silently leaves out, standing in for a restore that wrote less than it was
given.
"""
import io
import json
import os
import shutil
import sys
import tarfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.environ["FAKE_DOCKER_ROOT"])
VOLUMES, CONTAINERS = ROOT / "volumes", ROOT / "containers"


def main(argv):
    method, body, url, stdin_body = "GET", None, None, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-X":
            method = argv[i + 1]; i += 2; continue
        if a in ("-H", "--unix-socket"):
            i += 2; continue
        if a in ("-d", "--data-binary"):
            if argv[i + 1] == "@-":
                stdin_body = True
            else:
                body = argv[i + 1]
            i += 2; continue
        if a.startswith("http://"):
            url = a
        i += 1
    u = urlparse(url)
    path, query = u.path, parse_qs(u.query)
    data = sys.stdin.buffer.read() if stdin_body else None
    with open(ROOT / "calls.log", "a") as log:
        log.write(json.dumps({"method": method, "path": path, "query": query, "body": body}) + "\n")
    VOLUMES.mkdir(parents=True, exist_ok=True)
    CONTAINERS.mkdir(parents=True, exist_ok=True)

    def fail(code):
        print(json.dumps({"message": f"fake docker {code}"}))
        return 22

    parts = path.strip("/").split("/")
    if method == "GET" and parts == ["volumes"]:
        print(json.dumps({"Volumes": [{"Name": p.name} for p in sorted(VOLUMES.iterdir())]}))
        return 0
    if method == "GET" and parts[0] == "volumes" and len(parts) == 2:
        return 0 if (VOLUMES / parts[1]).is_dir() else fail(404)
    if method == "POST" and parts == ["volumes", "create"]:
        (VOLUMES / json.loads(body)["Name"]).mkdir()
        print(body)
        return 0
    if method == "POST" and parts == ["containers", "create"]:
        spec = json.loads(body)
        name, _, mode = spec["HostConfig"]["Binds"][0].split(":")
        if not (VOLUMES / name).is_dir():
            (VOLUMES / name).mkdir()  # the Engine creates a named volume a bind names
        cid = uuid.uuid4().hex
        (CONTAINERS / cid).write_text(json.dumps({"volume": name, "mode": mode, "spec": spec}))
        print(json.dumps({"Id": cid}))
        return 0
    if parts[0] == "containers" and len(parts) >= 2:
        c = CONTAINERS / parts[1]
        if not c.exists():
            return fail(404)
        info = json.loads(c.read_text())
        vol = VOLUMES / info["volume"]
        if method == "DELETE":
            c.unlink()
            return 0
        if parts[2:] == ["archive"] and method == "GET":
            assert query["path"] == ["/w"], query
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as t:
                t.add(vol, arcname="w")
            sys.stdout.buffer.write(buf.getvalue())
            return 0
        if parts[2:] == ["archive"] and method == "PUT":
            if info["mode"] != "rw" or info["spec"]["HostConfig"]["ReadonlyRootfs"]:
                return fail(403)
            drop = os.environ.get("FAKE_DOCKER_DROP_ON_PUT")
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                for m in t.getmembers():
                    rel = m.name.removeprefix("w").lstrip("/")
                    if not rel or rel == drop:
                        continue
                    target = vol / rel
                    if m.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with t.extractfile(m) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    os.chmod(target, m.mode)
            return 0
    return fail(400)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
