import os
import json
import aiohttp
import asyncio
import logging
import time
import requests
from urllib.parse import urlparse
from flask import Flask, request, jsonify
from fake_useragent import UserAgent

app = Flask(__name__)

COOKIES_FILE = 'cookies.txt'
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ua = UserAgent()

def get_random_headers():
    return {
        'User-Agent': ua.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Referer': 'https://www.1024terabox.com/'
    }

def load_cookies():
    cookies = {}
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
    return cookies

def find_between(text, start, end):
    try:
        s = text.find(start)
        if s == -1:
            return None
        s += len(start)
        e = text.find(end, s)
        if e == -1:
            return None
        return text[s:e]
    except:
        return None

def expand_short_url(url):
    try:
        resp = requests.get(url, allow_redirects=True, timeout=5)
        return resp.url
    except:
        return url

async def make_request(session, url, method='GET', **kwargs):
    retries, last_exception = 0, None
    while retries < MAX_RETRIES:
        try:
            async with session.request(method, url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT), **kwargs) as resp:
                logger.info(f"{method} {url} -> Status {resp.status}")
                if resp.status == 403:
                    logger.warning("Access Denied (403). Retrying...")
                    retries += 1
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Error {resp.status}: {body}")
                resp.raise_for_status()
                return resp
        except Exception as e:
            last_exception = e
            logger.error(f"Exception on attempt {retries + 1}: {str(e)}")
            retries += 1
            await asyncio.sleep(RETRY_DELAY)
    raise Exception(f"Max retries exceeded. Last error: {last_exception}")

async def fetch_download_link_async(url):
    cookies = load_cookies()
    if not cookies:
        raise Exception("No cookies found. Add your cookies.txt.")

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector, cookies=cookies, headers=get_random_headers()) as session:
        resp = await make_request(session, url)
        text = await resp.text()

        js_token = find_between(text, 'fn%28%22', '%22%29')
        log_id = find_between(text, 'dp-logid=', '&')

        if not js_token or not log_id:
            raise Exception("Unable to extract jsToken or logId.")

        surl = url.split('surl=')[-1] if 'surl=' in url else url.split('/')[-1]
        if not surl:
            raise Exception("Invalid share link: surl not found")

        params = {
            'app_id': '250528',
            'web': '1',
            'channel': 'dubox',
            'clienttype': '0',
            'jsToken': js_token,
            'dplogid': log_id,
            'page': '1',
            'num': '20',
            'order': 'time',
            'desc': '1',
            'site_referer': url,
            'shorturl': surl,
            'root': '1'
        }

        api_url = 'https://www.1024terabox.com/share/list'
        list_resp = await make_request(session, api_url, params=params)
        list_data = await list_resp.json()

        if 'list' not in list_data or not list_data['list']:
            raise Exception("No files found.")

        return list_data['list']

async def get_direct_link(session, dlink):
    try:
        resp = await make_request(session, dlink, method='HEAD', allow_redirects=False)
        if 300 <= resp.status < 400:
            return resp.headers.get('Location', dlink)
    except:
        pass
    try:
        resp = await make_request(session, dlink, method='GET', allow_redirects=False)
        if 300 <= resp.status < 400:
            return resp.headers.get('Location', dlink)
    except:
        pass
    return dlink

async def get_formatted_size(size_bytes):
    size_bytes = int(size_bytes)
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024 ** 3:.2f} GB"
    elif size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024 ** 2:.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} bytes"

async def process_file(session, file_data):
    direct_link = await get_direct_link(session, file_data['dlink'])
    return {
        "file_name": file_data.get("server_filename"),
        "size": await get_formatted_size(file_data.get("size", 0)),
        "size_bytes": file_data.get("size", 0),
        "download_url": file_data['dlink'],
        "direct_download_url": direct_link,
        "is_directory": file_data.get("isdir", "0") == "1",
        "modify_time": file_data.get("server_mtime"),
        "thumbnails": file_data.get("thumbs", {})
    }

@app.route('/api', methods=['GET'])
def api_handler():
    start_time = time.time()
    raw_url = request.args.get('url')
    if not raw_url:
        return jsonify({
            "status": "error",
            "message": "URL parameter is required.",
            "usage": "/api?url=YOUR_TERABOX_SHARE_URL"
        }), 400

    url = expand_short_url(raw_url)
    logger.info(f"Processing URL: {url}")

    try:
        files = asyncio.run(fetch_download_link_async(url))
        if not files:
            return jsonify({
                "status": "error",
                "message": "No files found in the shared link",
                "url": url
            }), 404

        results = []

        async def process_all_files():
            async with aiohttp.ClientSession(cookies=load_cookies()) as session:
                for file in files:
                    processed = await process_file(session, file)
                    if processed:
                        results.append(processed)

        asyncio.run(process_all_files())

        if not results:
            return jsonify({
                "status": "error",
                "message": "Could not process any files",
                "url": url
            }), 500

        return jsonify({
            "status": "success",
            "url": url,
            "files": results,
            "processing_time": f"{time.time() - start_time:.2f} seconds",
            "file_count": len(results)
        })

    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "url": url or "Not provided"
        }), 500

@app.route('/')
def home():
    return jsonify({
        "status": "Running ✅",
    })

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port, threaded=True)
