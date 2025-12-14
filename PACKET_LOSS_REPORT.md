# Packet Loss Test Report

## Methodology
- **Environment**: Linux (Docker, python:3.12-slim)
- **Tool**: `benchmarks/ingest_bench.py` (Sender) -> `pystatsd` (Receiver)
- **Verification**: 
  - **Sent**: Counted by sender script.
  - **Received**: Queried from Observability HTTP Endpoint (`/metrics`), specifically `pystatsd_aggregator_received_total`.
  - **Note**: This method avoids log parsing overhead and verifies the internal state of the aggregator.

## Configuration
- **Workers**: 1
- **Event Loop**: uvloop
- **Backend**: Logger (stdout, minimal overhead)
- **Flush Interval**: 2.0s

## Results

| Target Rate (pps) | Sent Packets | Received Metrics | Loss Rate (%) |
|-------------------|--------------|------------------|---------------|
| 10,000            | 49,981       | 7,025            | 85.94%        |
| 20,000            | 99,963       | 8,865            | 91.13%        |
| 40,000            | 199,940      | 16,052           | 91.97%        |
| 60,000            | 299,941      | 25,184           | 91.60%        |
| 80,000            | 399,858      | 18,240           | 95.44%        |
| 100,000           | 499,324      | 18,528           | 96.29%        |
| 120,000           | 599,821      | 17,408           | 97.10%        |
| 150,000           | 749,857      | 14,400           | 98.08%        |
| 200,000           | 999,329      | 18,592           | 98.14%        |

## Analysis
The current single-worker implementation shows significant packet loss even at 10k PPS. This suggests a bottleneck in the ingestion path, likely due to:
1. **UDP Buffer Overflow**: The OS drops packets because the application cannot dequeue them fast enough.
2. **Processing Overhead**: The Python-based parser or aggregator might be too slow for this volume.
3. **Docker Network Overhead**: Running in Docker with port mapping adds some overhead, though usually not this much.

## Recommendations
1. **Increase UDP Receive Buffer**: Set `socket_buffer_size` in config to a larger value (e.g., 4MB or 8MB).
2. **Scale Workers**: Use `num_workers > 1` with `SO_REUSEPORT` (supported on Linux) to distribute load.
3. **Optimize Parser**: Consider using a C-extension for parsing (e.g., `pystatsd-parser` if available) or optimizing the Python parser.
