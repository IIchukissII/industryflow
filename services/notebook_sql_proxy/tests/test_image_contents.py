# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Does the image actually contain the code?

The Dockerfile copies an explicit list of modules. The unit tests import from the source tree, so a
module missing from that list leaves every test green and the container crashlooping on import.
This reads both sides — the modules here and the modules the Dockerfile copies — so forgetting the
list is a red test rather than a crashloop found on a box. (The same guard as the tracking
gateway's.)
"""
import os
import re

DOCKERFILE = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
SRC = os.path.join(os.path.dirname(__file__), "..")


def _copied_modules():
    with open(DOCKERFILE) as fh:
        body = fh.read()
    copied = set()
    for line in body.splitlines():
        if line.startswith("COPY ") and ".py" in line:
            copied.update(re.findall(r"[\w.]+\.py", line))
    return copied


def _source_modules():
    return {f for f in os.listdir(SRC) if f.endswith(".py")}


def test_the_image_carries_every_module_in_this_service():
    missing = _source_modules() - _copied_modules()
    assert not missing, (
        f"{sorted(missing)} exist here but the Dockerfile never copies them into the image. "
        f"The tests would pass and the container would crashloop on import."
    )


def test_the_dockerfile_does_not_copy_modules_that_no_longer_exist():
    phantom = _copied_modules() - _source_modules()
    assert not phantom, f"the Dockerfile copies {sorted(phantom)}, which do not exist"
