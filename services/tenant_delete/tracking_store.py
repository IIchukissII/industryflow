# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removes a deleted tenant from the tracking store (ADR-0033 dec 4).

Through the tracking gateway's operator plane, with a secret only the gateway and this job hold: the
gateway is the artifact store's one writer and removes experiments and models through MLflow's API.
What MLflow keeps of a deleted experiment is hard-deleted by the tracking server's own collection,
on its own schedule, so the store is recorded only once the gateway reports nothing of the tenant
remaining. Until then the step returns None and the deletion waits; repeating the call is harmless.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from industryflow_contracts import tenancy

from .job import Removal

_PATH = "/api/2.0/industryflow-operator/tenants/"


class TrackingStore:
    def __init__(self, gateway_url: str, token: str, timeout: float = 300.0, opener=urllib.request.urlopen):
        self._url = gateway_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._open = opener

    def purge(self, company_id: str, user_ids: list, requested_at=None) -> Optional[Removal]:
        cid = tenancy.canonical_company_id(company_id)
        request = urllib.request.Request(self._url + _PATH + cid, method="DELETE",
                                         headers={"Authorization": f"Bearer {self._token}"})
        # A refusal or an MLflow failure raises (urllib raises on any non-2xx): the run fails and the
        # next one repeats the call.
        with self._open(request, timeout=self._timeout) as response:
            answer = json.load(response)
        if answer.get("company_id") != cid:
            raise RuntimeError(f"tracking gateway answered for {answer.get('company_id')!r}, not {cid}")
        remaining = answer["remaining"]
        if any(remaining.values()):
            return None
        return Removal(detail={"remaining": remaining})
