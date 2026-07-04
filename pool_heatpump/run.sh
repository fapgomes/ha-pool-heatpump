#!/usr/bin/env bashio
# Obtém as credenciais MQTT (das opções ou do serviço MQTT do Supervisor)
# e lança a ponte.

MQTT_HOST="$(bashio::config 'mqtt_host')"
MQTT_PORT="$(bashio::config 'mqtt_port')"
MQTT_USER="$(bashio::config 'mqtt_username')"
MQTT_PASS="$(bashio::config 'mqtt_password')"

if [ -z "${MQTT_HOST}" ] && bashio::services.available "mqtt"; then
    MQTT_HOST="$(bashio::services mqtt 'host')"
    MQTT_PORT="$(bashio::services mqtt 'port')"
    MQTT_USER="$(bashio::services mqtt 'username')"
    MQTT_PASS="$(bashio::services mqtt 'password')"
    bashio::log.info "A usar o serviço MQTT do Supervisor em ${MQTT_HOST}:${MQTT_PORT}"
fi

cat > /opt/scripts/heatpump_bridge.conf <<EOF
{
  "mqtt": {
    "host": "${MQTT_HOST}",
    "port": ${MQTT_PORT:-1883},
    "username": "${MQTT_USER}",
    "password": "${MQTT_PASS}"
  }
}
EOF

bashio::log.info "A iniciar a ponte da bomba de calor (porta 8502)..."
cd /opt/scripts
exec python3 heatpump_bridge.py
