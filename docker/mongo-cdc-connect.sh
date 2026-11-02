#!/usr/bin/env bash
set -euo pipefail

: "${SPIDER_KAFKA_BROKERS:?SPIDER_KAFKA_BROKERS is required}"
: "${SPIDER_KAFKA_TOPIC:?SPIDER_KAFKA_TOPIC is required}"

config=/tmp/spider-mongo-connect.properties
umask 077
: > "$config"

# Escape Java properties separately from the quoted JAAS username/password.
property() {
    local value="$2"
    value=${value//\\/\\\\}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    printf '%s=%s\n' "$1" "$value" >> "$config"
}

property bootstrap.servers "$SPIDER_KAFKA_BROKERS"
property group.id "${SPIDER_KAFKA_TOPIC}-mongo-connect"
property config.storage.topic "${SPIDER_KAFKA_TOPIC}-mongo-connect-configs"
property offset.storage.topic "${SPIDER_KAFKA_TOPIC}-mongo-connect-offsets"
property status.storage.topic "${SPIDER_KAFKA_TOPIC}-mongo-connect-status"
for store in config offset status; do
    property "$store.storage.replication.factor" "${SPIDER_MONGO_CDC_REPLICATION_FACTOR:-3}"
done
property offset.flush.interval.ms 1000
property key.converter org.apache.kafka.connect.json.JsonConverter
property value.converter org.apache.kafka.connect.json.JsonConverter
property key.converter.schemas.enable false
property value.converter.schemas.enable false
property plugin.path "${KAFKA_CONNECT_PLUGINS_DIR:-/kafka/connect}"
property listeners http://0.0.0.0:8083
property rest.advertised.host.name mongo-cdc-connect
property rest.advertised.port 8083
property producer.max.request.size 16777216

protocol=${SPIDER_KAFKA_SECURITY_PROTOCOL:-PLAINTEXT}
mechanism=${SPIDER_KAFKA_SASL_MECHANISM:-}
if [[ -n "$mechanism" ]]; then
    case "$mechanism" in
        PLAIN) login=org.apache.kafka.common.security.plain.PlainLoginModule ;;
        SCRAM-SHA-256|SCRAM-SHA-512) login=org.apache.kafka.common.security.scram.ScramLoginModule ;;
        *) echo 'Unsupported SPIDER_KAFKA_SASL_MECHANISM' >&2; exit 1 ;;
    esac
    username=${SPIDER_KAFKA_SASL_USERNAME:?SASL username is required}
    password=${SPIDER_KAFKA_SASL_PASSWORD:?SASL password is required}
    username=${username//\\/\\\\}; username=${username//\"/\\\"}
    password=${password//\\/\\\\}; password=${password//\"/\\\"}
    jaas="$login required username=\"$username\" password=\"$password\";"
fi
for prefix in '' producer. consumer. admin.; do
    property "${prefix}security.protocol" "$protocol"
    if [[ -n "${SPIDER_MONGO_CDC_TLS_CA:-}" ]]; then
        property "${prefix}ssl.truststore.type" PEM
        property "${prefix}ssl.truststore.location" "$SPIDER_MONGO_CDC_TLS_CA"
    fi
    if [[ -n "$mechanism" ]]; then
        property "${prefix}sasl.mechanism" "$mechanism"
        property "${prefix}sasl.jaas.config" "$jaas"
    fi
done

exec "${KAFKA_HOME:-/kafka}/bin/connect-distributed.sh" "$config"
