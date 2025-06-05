import os
import re
import json
import time
import aiohttp
import asyncio
import logging
from flask import Flask, request, jsonify
from urllib.parse import urlparse
from datetime import datetime

app = Flask(__name__)

# Configuration
COOKIES_FILE = 'cookies.txt'
REQUEST_TIMEOUT = 45
MAX_RETRIES = 3
RETRY_DELAY = 2

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def load_cookies():
    """Load cookies from file in Netscape format"""
    cookies = {}
    try:
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        cookies[parts[5]] = parts[6]
        return cookies
    except Exception as e:
        logger.error(f"Cookie loading error: {str(e)}")
        return {}

def validate_terabox_url(url):
    """Validate Terabox share URL"""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        valid_domains = [
            'terabox.com',
            'teraboxapp.com',
            '1024terabox.com',
            'www.terabox.com'
        ]
        return any(parsed.netloc.endswith(d) for d in valid_domains)
    except Exception:
        return False

async def make_request(session, url, method='GET', **kwargs):
    """Make HTTP request with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            async with session.request(
                method,
                url,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                **kwargs
            ) as response:
                response.raise_for_status()
                return response
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))

async def extract_tokens(text, url):
    """Extract required tokens from page content"""
    try:
        # Try multiple patterns to find tokens
        patterns = [
            (r'fn%28%22([^%]+)%22%29', r'dp-logid=([^&]+)'),  # Pattern 1
            (r'jsToken":"([^"]+)"', r'dplogid=([^&]+)'),       # Pattern 2
            (r'jstoken=([^&]+)', r'logid=([^&]+)')             # Pattern 3
        ]
        
        for js_pattern, logid_pattern in patterns:
            js_match = re.search(js_pattern, text)
            logid_match = re.search(logid_pattern, str(url) + text)
            
            if js_match and logid_match:
                return js_match.group(1), logid_match.group(1)
        
        raise Exception("Tokens not found in page")
    except Exception as e:
        logger.error(f"Token extraction failed: {str(e)}")
        raise

async def fetch_file_list(session, api_url, params):
    """Fetch file list from Terabox API"""
    try:
        response = await make_request(session, api_url, params=params)
        data = await response.json()
        
        if data.get('errno') != 0:
            raise Exception(data.get('errmsg', 'API error'))
        
        return data.get('list', [])
    except Exception as e:
        logger.error(f"API request failed: {str(e)}")
        raise

def format_file_data(file_list):
    """Format file data for response"""
    formatted = []
    for file in file_list:
        formatted.append({
            'name': file.get('server_filename'),
            'size': file.get('size', 0),
            'size_formatted': convert_size(file.get('size', 0)),
            'is_directory': file.get('isdir') == '1',
            'download_url': file.get('dlink', ''),
            'modified': file.get('server_mtime'),
            'md5': file.get('md5')
        })
    return formatted

def convert_size(size_bytes):
    """Convert bytes to human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return "0 B"

async def process_terabox_url(url):
    """Main processing function for Terabox URLs"""
    cookies = load_cookies()
    if not cookies:
        raise Exception("No valid cookies found")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.terabox.com/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    async with aiohttp.ClientSession(
        cookies=cookies,
        headers=headers,
        trust_env=True
    ) as session:
        # Initial request to get tokens
        response = await make_request(session, url)
        response_text = await response.text()
        
        # Extract required tokens
        js_token, log_id = await extract_tokens(response_text, response.url)
        
        # Get share URL parameter
        surl_match = re.search(r'surl=([^&]+)', str(response.url))
        if not surl_match:
            raise Exception("Share URL parameter not found")
        surl = surl_match.group(1)
        
        # Prepare API request
        api_url = 'https://www.1024terabox.com/api/share/list'
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
        
        # Get file list
        file_list = await fetch_file_list(session, api_url, params)
        return format_file_data(file_list)

@app.route('/api', methods=['GET'])
def api_handler():
    """Main API endpoint"""
    start_time = time.time()
    url = request.args.get('url')
    
    if not url or not validate_terabox_url(url):
        return jsonify({
            'status': False,
            'error': 'Invalid or missing Terabox URL',
            'usage': '/api?url=TERABOX_SHARE_URL',
            'time': f"{time.time() - start_time:.2f}s"
        }), 400
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        files = loop.run_until_complete(process_terabox_url(url)))
        loop.close()
        
        return jsonify({
            'status': True,
            'url': url,
            'files': files,
            'count': len(files),
            'time': f"{time.time() - start_time:.2f}s"
        })
        
    except Exception as e:
        logger.error(f"API Error: {str(e)}")
        return jsonify({
            'status': False,
            'error': str(e),
            'url': url,
            'time': f"{time.time() - start_time:.2f}s"
        }), 500

@app.route('/')
def home():
    return jsonify({
        'status': True,
        'service': 'Terabox API',
        'endpoints': {
            '/api': {
                'method': 'GET',
                'params': {'url': 'Terabox share URL'},
                'description': 'Get file information from Terabox share link'
            }
        },
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, threaded=True)
