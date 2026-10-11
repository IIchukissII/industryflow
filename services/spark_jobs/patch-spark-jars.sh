#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# The one place the Spark images' jars/ dir is changed. Shared by the streaming, aggregations and worker
# images so their classpaths cannot drift apart.
#
# 1. S3A for an object-storage checkpoint (ADR-0006, docs/architecture/stream-processing.md): baked in,
#    not --packages, so it is pinned to the image's Hadoop (3.4.2) and resolves offline. Spark 4's
#    Hadoop 3.4.x S3A uses the AWS SDK v2 bundle.
# 2. Netty: the distribution's 4.2.x set is replaced as a WHOLE set with a fixed patch release. Netty
#    modules of one release are built against each other; swapping only netty-handler would mix
#    versions on the classpath.
# 3. Removed because nothing here uses them: Derby (only the embedded Hive metastore loads it; no job
#    enables Hive support) and connect-repl/ (the Spark Connect client shell; not on the job classpath).
#
# Every download is checked against Maven Central's published SHA-1. The script then asserts the
# result, so a version that moves upstream fails the build instead of shipping a mixed classpath.
set -eu

HADOOP_AWS_VERSION=3.4.2
AWS_SDK_VERSION=2.55.14
NETTY_FROM=4.2.7.Final
NETTY_TO=4.2.17.Final

JARS=${SPARK_JARS_DIR:-/opt/spark/jars}
MAVEN=https://repo1.maven.org/maven2

# fetch <group path> <artifact> <version> <classifier or empty> <destination>
fetch() {
    name="$2-$3${4:+-$4}.jar"
    url="$MAVEN/$1/$2/$3/$name"
    curl -fsSL -o "$5" "$url"
    sha1=$(curl -fsSL "$url.sha1" | cut -c1-40)
    echo "$sha1  $5" | sha1sum -c -
}

fetch org/apache/hadoop hadoop-aws "$HADOOP_AWS_VERSION" "" "$JARS/hadoop-aws-$HADOOP_AWS_VERSION.jar"
fetch software/amazon/awssdk bundle "$AWS_SDK_VERSION" "" "$JARS/bundle-$AWS_SDK_VERSION.jar"

found=0
for jar in "$JARS"/netty-*-"$NETTY_FROM"*.jar; do
    [ -e "$jar" ] || continue
    found=$((found + 1))
    base=$(basename "$jar" .jar)
    artifact=${base%%-"$NETTY_FROM"*}
    classifier=${base#*-"$NETTY_FROM"}
    classifier=${classifier#-}
    fetch io/netty "$artifact" "$NETTY_TO" "$classifier" "$JARS/$artifact-$NETTY_TO${classifier:+-$classifier}.jar"
    rm -f "$jar"
done
[ "$found" -gt 0 ] || { echo "no netty $NETTY_FROM jars in $JARS: the base image moved, update NETTY_FROM" >&2; exit 1; }

rm -f "$JARS"/derby-*.jar "$JARS"/derbyshared-*.jar "$JARS"/derbytools-*.jar
rm -rf "$JARS/connect-repl"

if ls "$JARS" | grep -E "^netty-.*-$NETTY_FROM|^derby" >/dev/null; then
    echo "jars left behind:" >&2
    ls "$JARS" | grep -E "^netty-.*-$NETTY_FROM|^derby" >&2
    exit 1
fi
echo "spark jars patched: $found netty jars -> $NETTY_TO, aws sdk $AWS_SDK_VERSION, derby + connect-repl removed"
