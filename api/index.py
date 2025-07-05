from flask import Flask, request, jsonify
import os
import json
import logging
import re
import random
import time
import requests
from urllib.parse import urlparse, parse_qs

app = Flask(__name__)

# Configuration
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2
PORT = 3000

# Supported domains
SUPPORTED_DOMAINS = [
    "terabox.com",
    "teraboxapp.com",
    "www.terabox.com",
    "www.teraboxapp.com"
]

# Regex pattern for Terabox URLs
TERABOX_URL_REGEX = r'^https:\/\/(www\.)?(terabox\.com|teraboxapp\.com)\/(s|sharing)\/[A-Za-z0-9_\-]+'

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Updated Cookies (Replace with fresh cookies)
COOKIES = {
    'ndus': 'Y-wWXKyteHuigAhC03Fr4bbee-QguZ4JC6UAdqap',
    'ndut_fmt': '082E0D57C65BDC31F6FF293F5D23164958B85D6952CCB6ED5D8A3870CB302BE7',
    'PANWEB': '1',
    'csrfToken': 'wlv_WNcWCjBtbNQDrHSnut2h',
    'lang': 'en'
}
# COOKIES = {
#     'ndut_fmt': '082E0D57C65BDC31F6FF293F5D23164958B85D6952CCB6ED5D8A3870CB302BE7',
#     'ndus': 'Y-wWXKyteHuigAhC03Fr4bbee-QguZ4JC6UAdqap',
#     '__bid_n': '196ce76f980a5dfe624207',
#     '__stripe_mid': '148f0bd1-59b1-4d4d-8034-6275095fc06f99e0e6',
#     '__stripe_sid': '7b425795-b445-47da-b9db-5f12ec8c67bf085e26',
#     'browserid': 'veWFJBJ9hgVgY0eI9S7yzv66aE28f3als3qUXadSjEuICKF1WWBh4inG3KAWJsAYMkAFpH2FuNUum87q',
#     'csrfToken': 'wlv_WNcWCjBtbNQDrHSnut2h',
#     'lang': 'en',
#     'PANWEB': '1',
#     'ab_sr': '1.0.1_NjA1ZWE3ODRiYjJiYjZkYjQzYjU4NmZkZGVmOWYxNDg4MjU3ZDZmMTg0Nzg4MWFlNzQzZDMxZWExNmNjYzliMGFlYjIyNWUzYzZiODQ1Nzg3NWM0MzIzNWNiYTlkYTRjZTc0ZTc5ODRkNzg4NDhiMTljOGRiY2I4MzY4ZmYyNTU5ZDE5NDczZmY4NjJhMDgyNjRkZDI2MGY5M2Q5YzIyMg=='
# }
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
}

def validate_terabox_url(url):
    """Validate Terabox URL format"""
    try:
        return re.match(TERABOX_URL_REGEX, url) is not None
    except Exception:
        return False

def make_request(url, method='GET', headers=None, params=None, allow_redirects=True, cookies=None):
    """Make HTTP request with retry logic"""
    session = requests.Session()
    retries = 0
    
    while retries < MAX_RETRIES:
        try:
            response = session.request(
                method,
                url,
                headers=headers or HEADERS,
                params=params,
                cookies=cookies or COOKIES,
                allow_redirects=allow_redirects,
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code in [403, 429, 503]:
                logger.warning(f"Rate limited ({response.status_code}), retrying...")
                time.sleep(RETRY_DELAY * (2 ** retries))
                retries += 1
                continue
                
            response.raise_for_status()
            return response
        except Exception as e:
            logger.warning(f"Request failed (attempt {retries + 1}): {str(e)}")
            if retries == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY * (2 ** retries))
            retries += 1

def extract_tokens(html):
    """Extract required tokens from HTML"""
    try:
        # Extract jsToken
        js_token = re.search(r'window\.jsToken\s*=\s*["\']([^"\']+)["\']', html)
        js_token = js_token.group(1) if js_token else None
        
        # Extract logid
        logid = re.search(r'logid=([^&"\']+)', html)
        logid = logid.group(1) if logid else None
        
        if not js_token or not logid:
            raise Exception("Could not extract required tokens")
            
        return js_token, logid
    except Exception as e:
        logger.error(f"Token extraction error: {str(e)}")
        raise

def get_surl(url):
    """Extract surl from URL"""
    try:
        # Try to get from query params
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'surl' in params:
            return params['surl'][0]
        
        # Try to get from path
        path_parts = parsed.path.split('/')
        if 's' in path_parts:
            idx = path_parts.index('s')
            if len(path_parts) > idx + 1:
                return path_parts[idx + 1]
        if 'sharing' in path_parts:
            idx = path_parts.index('sharing')
            if len(path_parts) > idx + 2:
                return path_parts[idx + 2]
        
        raise Exception("Could not extract surl")
    except Exception as e:
        logger.error(f"surl extraction error: {str(e)}")
        raise

def process_terabox_url(url):
    """Main processing function for Terabox URLs"""
    try:
        # Step 1: Initial request to get tokens
        response = make_request(url)
        html = response.text
        
        # Step 2: Extract required tokens
        js_token, logid = extract_tokens(html)
        surl = get_surl(response.url)
        
        # Step 3: Prepare API request
        api_url = "https://www.terabox.com/api/shorturlinfo"
        params = {
            'app_id': '250528',
            'web': '1',
            'channel': 'dubox',
            'clienttype': '0',
            'jsToken': js_token,
            'dplogid': logid,
            'shorturl': surl,
            'root': '1'
        }
        
        # Step 4: Make API request
        api_response = make_request(api_url, params=params)
        data = api_response.json()
        
        if 'list' not in data or not data['list']:
            raise Exception("No files found in response")
        
        # Step 5: Process files
        results = []
        for item in data['list']:
            if item.get('isdir') == '1':
                continue  # Skip directories
                
            dlink = item.get('dlink', '')
            if not dlink:
                continue
                
            # Format size
            size_bytes = int(item.get('size', 0))
            size_str = format_size(size_bytes)
            
            results.append({
                "filename": item.get('server_filename'),
                "size": size_str,
                "size_bytes": size_bytes,
                "download_url": dlink,
                "direct_url": get_direct_url(dlink),
                "modified": item.get('server_mtime'),
                "thumbnails": item.get('thumbs', {})
            })
        
        return results
    except Exception as e:
        logger.error(f"Processing error: {str(e)}")
        raise

def get_direct_url(url):
    """Get direct download URL by following redirects"""
    try:
        response = make_request(url, method='HEAD', allow_redirects=False)
        if response.status_code in [301, 302, 303, 307, 308]:
            return response.headers.get('Location', url)
        return url
    except Exception:
        return url

def format_size(size_bytes):
    """Convert bytes to human-readable format"""
    if size_bytes == 0:
        return "0B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while size_bytes >= 1024 and i < len(units)-1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f}{units[i]}"

@app.route('/api', methods=['GET'])
def api_handler():
    """Main API endpoint"""
    start_time = time.time()
    url = request.args.get('url')
    
    if not url:
        return jsonify({
            "status": "error",
            "message": "URL parameter is required",
            "usage": "/api?url=TERABOX_SHARE_URL"
        }), 400
    
    if not validate_terabox_url(url):
        return jsonify({
            "status": "error",
            "message": "Invalid Terabox URL",
            "supported_domains": SUPPORTED_DOMAINS
        }), 400
    
    try:
        files = process_terabox_url(url)
        return jsonify({
            "status": "success",
            "url": url,
            "files": files,
            "count": len(files),
            "time_taken": f"{time.time() - start_time:.2f}s"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "solution": "Try again later or check your cookies"
        }), 500

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Terabox API",
        "version": "2024.06",
        "endpoint": "/api?url=TERABOX_URL"
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, threaded=True)
