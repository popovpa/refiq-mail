#!/bin/sh
# Idempotent local topic provisioning. Does not alter broker defaults or other topics.
# mail-events uses 3 partitions so the mail-service group can scale consumers.
# mail-events.dlq uses 1 partition. Replication factor is 1 for the single local broker.
set -eu

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:19092}"
KAFKA_TOPICS="${KAFKA_TOPICS_BIN:-/opt/kafka/bin/kafka-topics.sh}"

i=0
while [ "$i" -lt 30 ]; do
  if "$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --list >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

"$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic mail-events --partitions 3 --replication-factor 1
"$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic mail-events.dlq --partitions 1 --replication-factor 1
