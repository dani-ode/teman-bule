"""Local Docker gateway bridge to loopback-only Langflow. Development only.

Run: python scripts/langflow_dev_bridge.py
Binds Docker's host gateway only, not public interfaces. No HTTP logging.
"""

import asyncio


async def handle(reader, writer):
    upstream = None
    tasks = []
    try:
        remote, upstream = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 7860), timeout=5,
        )

        async def copy(source, target):
            while chunk := await source.read(65536):
                target.write(chunk)
                await target.drain()

        tasks = [asyncio.create_task(copy(reader, upstream)),
                 asyncio.create_task(copy(remote, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        if upstream:
            upstream.close()


async def main():
    server = await asyncio.start_server(handle, "172.17.0.1", 7861, limit=65536)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
