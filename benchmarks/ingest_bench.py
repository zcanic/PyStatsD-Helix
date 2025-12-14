import asyncio
import time
import socket
import argparse
import random
import logging
from collections import Counter

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ingest_bench")

async def send_packets(host, port, duration, rate, batch_size):
    """
    Send UDP packets at a target rate.
    """
    batch_size = max(1, batch_size)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Increase buffer size
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
    
    target_addr = (host, port)
    
    logger.info(f"Starting benchmark: target={host}:{port}, duration={duration}s, rate={rate} pkt/s")
    
    start_time = time.monotonic()
    end_time = start_time + duration
    
    packets_sent = 0
    metrics_sent = 0
    bytes_sent = 0
    
    # Pre-generate some payloads to avoid CPU overhead during loop
    payloads = []
    for i in range(1000):
        # Mix of counters and timers
        if i % 2 == 0:
            p = f"bench.counter.{i%100}:1|c".encode()
        else:
            p = f"bench.timer.{i%100}:{random.randint(1, 100)}|ms".encode()
        payloads.append(p)
        
    payload_count = len(payloads)
    
    # Token bucket for rate limiting
    tokens = 0.0
    last_check = time.monotonic()
    
    while True:
        now = time.monotonic()
        if now >= end_time:
            break
            
        # Add tokens
        elapsed = now - last_check
        last_check = now
        tokens += elapsed * rate
        
        if tokens > rate: # Cap tokens to 1 second worth
            tokens = rate
            
        while tokens >= 1.0:
            if batch_size == 1:
                payload = payloads[packets_sent % payload_count]
            else:
                metrics_batch = []
                base_idx = packets_sent % payload_count
                for offset in range(batch_size):
                    metrics_batch.append(payloads[(base_idx + offset) % payload_count])
                payload = b"\n".join(metrics_batch)

            try:
                sock.sendto(payload, target_addr)
                packets_sent += 1
                metrics_sent += batch_size
                bytes_sent += len(payload)
                tokens -= 1.0
            except BlockingIOError:
                # Socket buffer full, wait a bit
                await asyncio.sleep(0.001)
                break
            except Exception as e:
                logger.error(f"Send error: {e}")
                break
                
        # Yield to event loop to keep it responsive
        await asyncio.sleep(0.001)
        
    total_duration = time.monotonic() - start_time
    logger.info(f"Benchmark finished.")
    logger.info(f"Sent: {packets_sent} packets, {metrics_sent} metrics, {bytes_sent} bytes in {total_duration:.2f}s")
    logger.info(f"Actual Rate: {packets_sent / total_duration:.2f} pkt/s")
    logger.info(f"PACKETS_SENT={packets_sent}")
    logger.info(f"METRICS_SENT={metrics_sent}")
    
    return packets_sent

async def main():
    parser = argparse.ArgumentParser(description="PyStatsD-Helix Ingest Benchmark")
    parser.add_argument("--host", default="127.0.0.1", help="Target host")
    parser.add_argument("--port", type=int, default=8125, help="Target port")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds")
    parser.add_argument("--rate", type=int, default=40000, help="Target packet rate (pkt/s)")
    parser.add_argument("--batch-size", type=int, default=1, help="Metrics per packet")
    
    args = parser.parse_args()
    
    await send_packets(args.host, args.port, args.duration, args.rate, args.batch_size)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
