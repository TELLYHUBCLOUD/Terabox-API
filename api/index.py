import os
from flask import Flask, request, jsonify
import aiohttp
import asyncio
import logging
from urllib.parse import urlparse
from fake_useragent import UserAgent
import time
import re

app = Flask(__name__)

# Configuration
COOKIES_FILE = 'cookies.txt'
REQUEST_TIMEOUT = 60  # Increased timeout
MAX_RETRIES = 5       # Increased retries
RETRY_DELAY = 3       # Increased delay

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize user agent rotator
ua = UserAgent()

def get_random_headers():
    return {
        'User-Agent': ua.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://www.terabox.com/',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1'
    }

def load_cookies():
    cookies_dict = {}
    try:
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain = parts[0].strip()
                        if domain.startswith('.'):
                            domain = domain[1:]
                        cookie_name = parts[5].strip()
                        cookie_value = parts[6].strip()
                        cookies_dict[cookie_name] = cookie_value
    except Exception as e:
        logger.error(f"Error loading cookies: {str(e)}")
    return cookies_dict

def validate_terabox_url(url):
    """Validate if the URL is a Terabox share URL"""
    if not url:
        return False
    parsed = urlparse(url)
    allowed_domains = ['terabox.com', 'teraboxapp.com', '1024terabox.com']
    return any(parsed.netloc.endswith(domain) for domain in allowed_domains)

async def make_request(session, url, method='GET', headers=None, params=None):
    retry_count = 0
    last_exception = None
    
    while retry_count < MAX_RETRIES:
        try:
            async with session.request(
                method,
                url,
                headers=headers or get_random_headers(),
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=True
            ) as response:
                response.raise_for_status()
                return response
        except aiohttp.ClientError as e:
            last_exception = e
            logger.warning(f"Attempt {retry_count + 1} failed: {str(e)}")
            retry_count += 1
            await asyncio.sleep(RETRY_DELAY * retry_count)
        except Exception as e:
            last_exception = e
            logger.error(f"Unexpected error: {str(e)}")
            retry_count += 1
            await asyncio.sleep(RETRY_DELAY * retry_count)
    
    raise Exception(f"Request failed after {MAX_RETRIES} attempts. Last error: {str(last_exception)}")

async def fetch_file_info(url):
    cookies = load_cookies()
    if not cookies:
        raise Exception("Valid cookies are required to access Terabox links")
    
    # Convert cookies to the format aiohttp expects
    jar = aiohttp.CookieJar()
    for name, value in cookies.items():
        jar.update_cookies({name: value}, domain='.1024terabox.com')
    
    connector = aiohttp.TCPConnector(force_close=True, limit=0)
    async with aiohttp.ClientSession(
        cookie_jar=jar,
        connector=connector,
        trust_env=True
    ) as session:
        # Initial request to get tokens
        try:
            response = await make_request(session, url)
            response_text = await response.text()
            
            # Extract required tokens from page
            js_token_match = re.search(r'fn%28%22([^%]+)%22%29', response_text)
            js_token = js_token_match.group(1) if js_token_match else None
            
            log_id_match = re.search(r'dp-logid=([^&]+)', str(response.url))
            log_id = log_id_match.group(1) if log_id_match else None
            
            if not js_token or not log_id:
                # Alternative token extraction method
                js_token_match = re.search(r'jsToken":"([^"]+)"', response_text)
                js_token = js_token_match.group(1) if js_token_match else None
                
                log_id_match = re.search(r'dplogid=([^&]+)', response_text)
                log_id = log_id_match.group(1) if log_id_match else None
                
                if not js_token or not log_id:
                    raise Exception("Could not extract required authentication tokens")
            
            # Get surl from final URL
            surl_match = re.search(r'surl=([^&]+)', str(response.url))
            surl = surl_match.group(1) if surl_match else None
            if not surl:
                raise Exception("Could not extract share URL parameter")
            
            # Fetch file list
            params = {
                'app_id': '250528',
                'web': '1',
                'channel': 'dubox',
                'clienttype': '0',
                'jsToken': js_token,
                'dplogid': log_id,
                'shorturl': surl,
                'root': '1'
            }
            
            api_url = 'https://www.1024terabox.com/api/share/list'
            list_response = await make_request(
                session,
                api_url,
                params=params
            )
            
            list_data = await list_response.json()
            if list_data.get('errno') != 0:
                raise Exception(f"API error: {list_data.get('errmsg', 'Unknown error')}")
            
            if not list_data.get('list'):
                raise Exception("No files found in the shared link")
            
            return list_data['list']
        except Exception as e:
            logger.error(f"Error in fetch_file_info: {str(e)}")
            raise

def format_file_info(file_data):
    return {
        "name": file_data.get("server_filename"),
        "size": file_data.get("size", 0),
        "size_formatted": format_size(file_data.get("size", 0)),
        "is_directory": file_data.get("isdir", "0") == "1",
        "download_url": file_data.get("dlink", ""),
        "modified_time": file_data.get("server_mtime"),
        "md5": file_data.get("md5")
    }

def format_size(size_bytes):
    try:
        size_bytes = int(size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"
    except (ValueError, TypeError):
        return "0 B"

@app.route('/api', methods=['GET'])
def api_handler():
    url = request.args.get('url')
    if not url:
        return jsonify({
            "status": False,
            "error": "URL parameter is required",
            "usage": "/api?url=TERABOX_SHARE_URL"
        }), 400
    
    if not validate_terabox_url(url):
        return jsonify({
            "status": False,
            "error": "Invalid Terabox URL",
            "message": "Please provide a valid Terabox share URL"
        }), 400
    
    try:
        # Run async code in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        file_list = loop.run_until_complete(fetch_file_info(url))
        loop.close()
        
        formatted_files = [format_file_info(f) for f in file_list]
        
        return jsonify({
            "status": True,
            "url": url,
            "files": formatted_files,
            "count": len(formatted_files)
        })
        
    except Exception as e:
        logger.error(f"API Error: {str(e)}")
        return jsonify({
            "status": False,
            "error": str(e),
            "url": url
        }), 500

@app.route('/')
def home():
    return jsonify({
        "status": True,
        "service": "Terabox API",
        "developer": "@Farooq_is_king",
        "endpoint": {
            "/api": {
                "method": "GET",
                "parameter": "url",
                "description": "Terabox share URL"
            }
        }
    })

@app.route('/health')
def health_check():
    return jsonify({
        "status": True,
        "health": "OK",
        "timestamp": int(time.time())
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port, threaded=True)
