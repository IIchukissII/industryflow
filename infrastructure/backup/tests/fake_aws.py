# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for the `aws` CLI over a directory tree, for the backup script tests.

Each endpoint is its own store (``$FAKE_S3_ROOT/<endpoint>/<bucket>/<key>``), so a mirror between two
stores is exercised as one. Only the subcommands the backup scripts use are implemented. Every call
is appended to ``$FAKE_S3_ROOT/calls.log`` with the access key it ran under, so a test can tell which
principal touched which store — and that no credential travelled on argv.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["FAKE_S3_ROOT"])


def main(argv):
    endpoint = "aws"
    if "--endpoint-url" in argv:
        i = argv.index("--endpoint-url")
        endpoint = argv[i + 1].split("://", 1)[-1].replace(":", "_").replace("/", "_")
        argv = argv[:i] + argv[i + 2:]
    store = ROOT / endpoint
    with open(ROOT / "calls.log", "a") as log:
        log.write(json.dumps({"endpoint": endpoint, "key_id": os.environ.get("AWS_ACCESS_KEY_ID"),
                              "argv": argv}) + "\n")

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    def obj(uri):
        bucket, _, key = uri[len("s3://"):].partition("/")
        return store / bucket / key

    if argv[:2] == ["s3api", "list-objects-v2"]:
        bucket, prefix, query = store / opt("--bucket"), opt("--prefix", ""), opt("--query")
        keys = sorted(str(p.relative_to(bucket)) for p in bucket.rglob("*") if p.is_file()) \
            if bucket.exists() else []
        keys = [k for k in keys if k.startswith(prefix)]
        if opt("--output") == "json":
            # The CLI prints null for a query over an empty listing.
            print(json.dumps([[k, (bucket / k).stat().st_size] for k in keys] or None))
        elif not keys:
            print("None")
        elif query == "Contents[].[Key,Size]":
            for k in keys:
                print(f"{k}\t{(bucket / k).stat().st_size}")
        else:
            print("\t".join(keys))
        return 0
    if argv[:2] == ["s3api", "head-object"]:
        path = store / opt("--bucket") / opt("--key")
        if not path.exists():
            return 255
        print(path.stat().st_size)
        return 0
    if argv[:2] == ["s3api", "head-bucket"]:
        return 0 if (store / opt("--bucket")).is_dir() else 255
    if argv[:2] == ["s3", "mb"]:
        obj(argv[2] + "/x").parent.mkdir(parents=True, exist_ok=True)
        return 0
    if argv[:2] == ["s3", "rm"]:
        obj(argv[2]).unlink()
        return 0
    if argv[:2] == ["s3", "cp"]:
        src, dst = argv[2], argv[3]
        data = sys.stdin.buffer.read() if src == "-" else (
            obj(src).read_bytes() if src.startswith("s3://") else Path(src).read_bytes())
        if dst.startswith("s3://") and os.environ.get("FAKE_S3_TRUNCATE_PUT"):
            data = data[:-1]
        if dst == "-":
            sys.stdout.buffer.write(data)
        else:
            target = obj(dst) if dst.startswith("s3://") else Path(dst)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return 0
    print(f"fake aws: unsupported {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
