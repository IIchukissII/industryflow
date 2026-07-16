# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Does the image actually contain the code? (ADR-0026 — `main` means the stack starts.)

This check exists because the omission it catches already happened. The upload plane added two
modules; the Dockerfile copies an explicit list of files; the list was not updated. Every unit test
stayed green — they import from the source tree, which has the modules — and the *image* crashlooped
on `import admission`. Nothing between writing the code and deploying it had an opinion.

That is the shape this repo keeps meeting: a list somewhere that must be remembered, whose omission
is silent. The field allowlist that drops an unmapped column, the dev requirements that omit an
import, this. None of them fail loudly at the place the mistake is made.

The list stays explicit — that is the house style across the notebook services, and a deliberate
inventory is worth having. What changes is that forgetting it is now a red test rather than a
crashloop discovered on a box.
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
    # The other direction: a stale name in the list makes the build fail outright, which is loud —
    # but it is cheap to say so here rather than in a build log.
    phantom = _copied_modules() - _source_modules() - {"requirements.txt"}
    assert not phantom, f"the Dockerfile copies {sorted(phantom)}, which do not exist"
