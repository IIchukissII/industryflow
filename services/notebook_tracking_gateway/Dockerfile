# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Experiment-tracking gateway image (ADR-0019). Authenticates a notebook's tracking capability and
# scopes every MLflow call to the caller's tenant; the live MLflow proxy + artifact pre-signing are
# cluster-validated (see README / issue #19).
FROM python:3.14-slim

WORKDIR /app
# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). pip
# and its vendored certifi come from the base image, and the pins below are direct only, so pip and
# each transitive dependency are taken fresh — but only when the layer re-runs, and the persistent
# build cache replays it verbatim. This RUN moves the cache key of every layer after it, so a security
# release of pip or a transitive dependency lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

# Every module the app imports. An omission here does not fail a test — it ships an image that
# crashloops on `import`, which is why `tests/test_image_contents.py` checks this list against the
# directory rather than trusting that it was remembered.
COPY policy.py routes.py scoping.py gateway.py admission.py staging.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

EXPOSE 5050
CMD ["python", "-m", "gateway"]
