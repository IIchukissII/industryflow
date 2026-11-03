# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/box-deploy.sh deploys with the procedure of the commit it deploys. Bash reads the script it
started from, which the checkout replaces, so without handing over to the new version every change to
the procedure (the backup's place, the revision a build reports) reaches the box one deploy late."""
from _repo import ROOT

DEPLOY = (ROOT / "scripts/box-deploy.sh").read_text()


def test_the_checked_out_version_takes_over_before_anything_is_built():
    checkout = DEPLOY.index('git checkout -q --detach "$ref"')
    handover = DEPLOY.index('BOX_DEPLOY_SHA="$sha" exec scripts/box-deploy.sh "$sha"')
    first_step = min(DEPLOY.index(step) for step in ('echo "== build"', 'echo "== backup"', 'echo "== schema"'))
    assert checkout < handover < first_step
    # Re-run once only: the handed-over run finds its own commit checked out and goes on.
    assert 'if [ "${BOX_DEPLOY_SHA:-}" != "$sha" ]; then' in DEPLOY[checkout:handover]
