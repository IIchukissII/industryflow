# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""In-memory stand-in for the tenant alerts table behind AlertRepository.save_alert_once.

Mirrors the SQL's semantics, so engine tests exercise the durable dedup rather than a recorder:
a row is suppressed when one in the same scope sits strictly within the window of its event time
(reported back as prior_at), and an exact (alert_id, triggered_at) replay is a primary-key no-op.
Every behaviour is a scenario in dedup_scenarios.py, which runs against this fake in
test_fake_alert_store.py and against the real SQL in
infrastructure/timescaledb/tests/test_alert_dedup_hypertable.py.
"""


class FakeAlertStore:
    def __init__(self):
        self.saved = []

    @staticmethod
    def _in_scope(row, alert, retrain):
        if retrain:
            # model_id = $n, not IS NOT DISTINCT FROM: a recommendation with no model matches none.
            return (alert.get('model_id') is not None and row.get('model_id') == alert.get('model_id')
                    and row.get('condition') == 'retrain_recommended')
        return (
            (row['rule_id'], row.get('sensor_id'), row.get('equipment_id'))
            == (alert['rule_id'], alert.get('sensor_id'), alert.get('equipment_id'))
            and row.get('condition') != 'retrain_recommended'
        )

    async def save_alert_once(self, alert, *, window, retrain=False):
        # The real insert binds both by key (ADR-0044 dec 3); so does the fake, or a builder that
        # forgot them would pass here and fail in production.
        alert['degraded'], alert['neutralized_features']
        prior = [
            row['triggered_at'] for row in self.saved
            if self._in_scope(row, alert, retrain)
            and abs(row['triggered_at'] - alert['triggered_at']) < window
        ]
        if prior:
            return False, max(prior)
        if any((row['alert_id'], row['triggered_at']) == (alert['alert_id'], alert['triggered_at'])
               for row in self.saved):
            return False, None
        self.saved.append(dict(alert))
        return True, None
