#!/bin/bash
set -e

# Start server in background
echo "Starting server..."
python -m pystatsd_helix.main --config bench_config.toml > server.log 2>&1 &
SERVER_PID=$!

# Wait for server to start
sleep 5

# Run benchmark
echo "Running benchmark..."
# Target localhost since we are in the same container
python benchmarks/ingest_bench.py --host 127.0.0.1 --port 8130 --duration 10 --rate 10000

# Stop server
echo "Stopping server..."
kill $SERVER_PID || echo "Server already stopped"
wait $SERVER_PID || true

echo "Server log:"
cat server.log
