import asyncio
import re
import base64
import ssl
import aiohttp
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# ====== CONFIG ======
api_id = 123456
api_hash = "YOUR_API_HASH"

channels = [
    "channel1",
    "channel2",
    "channel3",
    "channel4",
    "channel5",
    "channel6",
    "channel7",
    "channel8",
    "channel9",
    "channel10",
]

LIMIT = 100
MAX_CONCURRENT_CHANNEL = 3
MAX_CONCURRENT_CONFIG = 50
TCP_TIMEOUT = 5
HTTP_TIMEOUT = 5

patterns = r"(vmess://\S+|vless://\S+|trojan://\S+|ss://\S+)"
# ===================

client = TelegramClient("session", api_id, api_hash, flood_sleep_threshold=60)


# ----- Extract configs from a channel -----
async def read_channel(channel, semaphore):
    async with semaphore:
        try:
            messages = await client.get_messages(channel, limit=LIMIT)
            configs = []
            for msg in messages:
                if msg.text:
                    found = re.findall(patterns, msg.text)
                    configs.extend(found)
            print(f"{channel}: {len(configs)} found")
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
async def check_http(url):
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status == 200
    except Exception:
        return False


# ----- Check if a config is alive -----
async def is_alive(config, semaphore):
    async with semaphore:
        # Extract host:port from config (simple parsing, works for most vmess/vless/trojan)
        host_port = re.search(r"@([\w\.\-]+):(\d+)", config)
        if not host_port:
            return None
        host, port = host_port.group(1), int(host_port.group(2))

        tcp_ok = await check_tcp(host, port)
        if not tcp_ok:
            return None

        if config.startswith(("vless://", "vmess://", "trojan://")):
            tls_ok = await check_tls(host, port)
            if not tls_ok:
                return None

        # Optionally HTTP check for health_path
        # Example: /health or /status
        http_path_match = re.search(r"health_path=([\w\/\-]+)", config)
        if http_path_match:
            path = http_path_match.group(1)
            protocol = "https" if config.startswith(("vless://", "vmess://", "trojan://")) else "http"
            url = f"{protocol}://{host}:{port}{path}"
            http_ok = await check_http(url)
            if not http_ok:
                return None

        return config


async def main():
    await client.start()
    channel_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHANNEL)
    config_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONFIG)

    # --- Step 1: Extract configs from all channels ---
    all_configs = []
    tasks = [read_channel(ch, channel_semaphore) for ch in channels]
    results = await asyncio.gather(*tasks)
    for res in results:
        all_configs.extend(res)

    unique_configs = list(set(all_configs))
    print(f"Total unique configs before test: {len(unique_configs)}")

    # --- Step 2: Test all configs concurrently ---
    tasks = [is_alive(cfg, config_semaphore) for cfg in unique_configs]
    alive_results = await asyncio.gather(*tasks)
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
