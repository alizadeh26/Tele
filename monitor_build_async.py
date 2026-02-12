import asyncio
import re
import base64
import ssl
import aiohttp
import json
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# ===== CONFIG =====
api_id = 123456
api_hash = "YOUR_API_HASH"

MAX_CONCURRENT_CHANNEL = 3
MAX_CONCURRENT_CONFIG = 100
TCP_TIMEOUT = 5
HTTP_TIMEOUT = 5
patterns = r"(vmess://\S+|vless://\S+|trojan://\S+|ss://\S+)"
# ==================

client = TelegramClient("session", api_id, api_hash, flood_sleep_threshold=60)

# ----- Extract configs from a channel -----
async def read_channel(channel, limit, semaphore):
    async with semaphore:
        try:
            messages = await client.get_messages(channel, limit=limit)
            configs = []
            for msg in messages:
                if msg.text:
                    found = re.findall(patterns, msg.text)
                    configs.extend(found)
            print(f"{channel}: {len(configs)} configs found")
            return configs
        except FloodWaitError as e:
            print(f"Flood wait {e.seconds}s on {channel}")
            await asyncio.sleep(e.seconds)
            return []
        except Exception as e:
            print(f"Error on {channel}: {e}")
            return []

# ----- TCP check -----
async def check_tcp(host, port):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=TCP_TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

# ----- TLS check -----
async def check_tls(host, port):
    try:
        ssl_ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_ctx), timeout=TCP_TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

# ----- HTTP Health check -----
async def check_http(host, port, path, use_tls):
    try:
        protocol = "https" if use_tls else "http"
        url = f"{protocol}://{host}:{port}{path}"
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status == 200
    except Exception:
        return False

# ----- Check if a config is alive -----
async def is_alive(config, server, semaphore):
    async with semaphore:
        host = server["host"]
        port = server["port"]
        use_tls = server.get("tls", False)
        health_path = server.get("health_path", "/")

        tcp_ok = await check_tcp(host, port)
        if not tcp_ok:
            return None

        if use_tls:
            tls_ok = await check_tls(host, port)
            if not tls_ok:
                return None

        http_ok = await check_http(host, port, health_path, use_tls)
        if not http_ok:
            return None

        return config

async def main():
    await client.start()

    # Load servers & channels
    with open("servers.json") as f:
        data = json.load(f)

    # --- Step 1: Extract configs from channels ---
    channel_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHANNEL)
    all_configs = []
    channel_tasks = [
        read_channel(ch["name"], ch.get("limit", 100), channel_semaphore)
        for ch in data.get("channels", [])
    ]
    results = await asyncio.gather(*channel_tasks)
    for res in results:
        all_configs.extend(res)

    unique_configs = list(set(all_configs))
    print(f"Total unique configs before test: {len(unique_configs)}")

    # --- Step 2: Test all configs concurrently ---
    config_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONFIG)
    alive_tasks = [
        is_alive(cfg, srv, config_semaphore)
        for cfg in unique_configs
        for srv in data.get("servers", [])
    ]
    alive_results = await asyncio.gather(*alive_tasks)
    alive_configs = [cfg for cfg in alive_results if cfg]

    print(f"Alive configs: {len(alive_configs)}")

    # --- Step 3: Build Base64 Subscription ---
    sub_text = "\n".join(alive_configs)
    sub_base64 = base64.b64encode(sub_text.encode()).decode()
    with open("subscription.txt", "w") as f:
        f.write(sub_base64)

    print("Subscription updated!")

with client:
    client.loop.run_until_complete(main())
