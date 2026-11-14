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
# Every jar is checked against the SHA-256 committed in spark-jars.sha256, beside this script, and is
# fetched from the first source that serves it (ADR-0026 dec 12): a source that refuses moves on to
# the next, a digest mismatch fails the build. A jar without a pin fails it too. The script then
# asserts the result, so a version that moves upstream fails the build instead of shipping a mixed
# classpath.
set -eu

HADOOP_AWS_VERSION=3.4.2
AWS_SDK_VERSION=2.55.14
NETTY_FROM=4.2.7.Final
NETTY_TO=4.2.17.Final

JARS=${SPARK_JARS_DIR:-/opt/spark/jars}
PINS=${SPARK_JAR_PINS:-$(dirname "$0")/spark-jars.sha256}
# Maven Central, then Google's mirror of it. The digest, not the host, is what is trusted.
SOURCES=${SPARK_JAR_SOURCES:-"https://repo1.maven.org/maven2 https://maven-central.storage-download.googleapis.com/maven2"}

[ -r "$PINS" ] || { echo "no jar pins at $PINS" >&2; exit 1; }

# fetch <group path> <artifact> <version> <classifier or empty> <destination>
fetch() {
    name="$2-$3${4:+-$4}.jar"
    pin=$(awk -v n="$name" '$2 == n { print $1 }' "$PINS")
    [ -n "$pin" ] || { echo "$name has no pin in $(basename "$PINS")" >&2; exit 1; }
    for source in $SOURCES; do
        # A source answers a transient error now and then; one that keeps refusing is skipped.
        if curl -fsSL --retry 3 --retry-all-errors --retry-delay 2 -o "$5" "$source/$1/$2/$3/$name"; then
            echo "$pin  $5" | sha256sum -c - || { rm -f "$5"; exit 1; }
            return 0
        fi
        echo "$source refused $name; trying the next source" >&2
    done
    echo "no source served $name" >&2
    exit 1
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
