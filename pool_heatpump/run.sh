#!/usr/bin/env sh
# Build the config from the add-on options (read directly from
# /data/options.json, no bashio/Supervisor API needed for user values) and
# launch the bridge.
set -e

python3 /opt/scripts/make_conf.py
echo "Starting the pool heat pump bridge (port 8502)..."
cd /opt/scripts
exec python3 heatpump_bridge.py
