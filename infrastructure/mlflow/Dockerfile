# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.11-slim

# Install MLflow 2.8.1 and dependencies
RUN pip install --no-cache-dir \
    mlflow==2.8.1 \
    psycopg2-binary==2.9.9 \
    boto3==1.28.0

EXPOSE 5000

CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
