import asyncio
import uvloop
import socket

async def main():
    loop = asyncio.get_running_loop()
    print("Loop type:", type(loop))
    
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            local_addr=('127.0.0.1', 8888),
            reuse_port=True
        )
        print("Success")
        transport.close()
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
