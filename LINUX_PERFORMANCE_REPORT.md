# Linux Performance Test Report

## Environment
- **Platform**: Linux (Docker Container based on python:3.12-slim)
- **Python Version**: 3.12
- **Event Loop**: uvloop (Verified installed and active)

## Benchmark Results
- **Test Script**: `benchmarks/ingest_bench.py`
- **Duration**: 10 seconds
- **Target Rate**: 10,000 packets/sec
- **Total Packets Sent**: 99,988
- **Actual Rate**: 9,998.11 packets/sec

## Server Status
- **Startup**: Successful
- **Workers**: 1
- **Backend**: LoggerBackend (stdout)
- **Stability**: Stable throughout the test. No crashes observed after fixing the `LoggerBackend` stdout redirection issue.

## Fixes Applied
1. **LoggerBackend Crash**: Fixed an issue where `uvloop` would crash when trying to use `connect_write_pipe` on a redirected stdout (regular file). Added a check to fallback to thread executor for regular files.
2. **Docker Test Environment**: Created `Dockerfile.test` and `run_bench.sh` to reliably run tests in a Linux environment, bypassing local WSL configuration issues.

## How to Run Tests
To reproduce these results:

1. **Build the Test Image**:
   ```bash
   docker build -t pystatsd-test -f Dockerfile.test .
   ```

2. **Run the Benchmark**:
   ```bash
   docker run --rm pystatsd-test bash run_bench.sh
   ```

3. **Run Smoke Test**:
   ```bash
   docker run --rm pystatsd-test python smoke_test.py
   ```
