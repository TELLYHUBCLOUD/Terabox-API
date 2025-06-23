from flask import Flask, request, jsonify, make_response
import requests
import re
import time
import logging
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache setup (in-memory for demo, use Redis in production)
cache = {}
CACHE_EXPIRY = 300  # 5 minutes

# Updated cookies (replace with fresh ones)
COOKIES = {
    'ndus': 'YvZNLrCteHuiHhOL5JkRGyt7mwk2eJ0crYm0-ZBu',
    'ndut_fmt': '143EC283EEA14790A54CD9359192BB84AD94DE8264161DB67EC48A8A55D29315',
    'csrfToken': 'zk5ofX38OBqhVW6CxXFKohl5'
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Referer': 'https://www.terabox.com/',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://www.terabox.com'
}

# Thread pool for parallel processing
executor = ThreadPoolExecutor(max_workers=4)

def validate_url(url):
    """Validate Terabox URL with multiple patterns"""
    patterns = [
        r'https?://(www\.)?terabox\.com/s/[A-Za-z0-9_-]+',
        r'https?://(www\.)?terabox\.com/sharing/link\?surl=[A-Za-z0-9_-]+',
        r'https?://(www\.)?1024terabox\.com/s/[A-Za-z0-9_-]+'
    ]
    return any(re.match(pattern, url) for pattern in patterns)

def extract_surl(url):
    """Advanced URL parsing with multiple fallbacks"""
    try:
        parsed = urlparse(url)
        # Case 1: /s/ format
        if '/s/' in parsed.path:
            return parsed.path.split('/s/')[1].split('/')[0]
        # Case 2: surl= parameter
        if 'surl=' in parsed.query:
            return parse_qs(parsed.query)['surl'][0]
        # Case 3: /sharing/link/ format
        if '/sharing/link/' in parsed.path:
            return parsed.path.split('/sharing/link/')[1]
        return None
    except Exception as e:
        logger.error(f"URL extraction error: {e}")
        return None

def get_terabox_data(surl):
    """Fetch data from Terabox API with retry logic"""
    api_url = "https://www.terabox.com/api/share/list"
    params = {
        'app_id': '250528',
        'shorturl': surl,
        'root': '1',
        'clienttype': '0',
        'web': '1'
    }

    for attempt in range(3):
        try:
            response = requests.get(
                api_url,
                params=params,
                cookies=COOKIES,
                headers=HEADERS,
                timeout=15
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)  # Exponential backoff
    return None

def format_file_size(size_bytes):
    """Convert bytes to human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

@app.route('/api', methods=['GET'])
def api_handler():
    """Main API endpoint with caching and parallel processing"""
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400

    # Check cache first
    cache_key = f"terabox:{url}"
    if cache_key in cache and time.time() - cache[cache_key]['timestamp'] < CACHE_EXPIRY:
        return jsonify(cache[cache_key]['data'])

    if not validate_url(url):
        return jsonify({'error': 'Invalid Terabox URL'}), 400

    surl = extract_surl(url)
    if not surl:
        return jsonify({'error': 'Could not extract file ID'}), 400

    try:
        # Process in thread pool to avoid blocking
        future = executor.submit(get_terabox_data, surl)
        data = future.result(timeout=20)

        if not data or not data.get('list'):
            return jsonify({'error': 'No files found'}), 404

        # Process files in parallel
        files = []
        def process_file(item):
            if item.get('isdir') == '1':
                return None
            return {
                'filename': item.get('server_filename'),
                'size': format_file_size(int(item.get('size', 0))),
                'size_bytes': item.get('size'),
                'download_url': item.get('dlink'),
                'md5': item.get('md5'),
                'modified': item.get('server_mtime'),
            }

        with ThreadPoolExecutor() as pool:
            results = pool.map(process_file, data['list'])
            files = [f for f in results if f]

        response_data = {
            'status': 'success',
            'url': url,
            'files': files,
            'count': len(files),
            'timestamp': int(time.time())
        }

        # Cache the response
        cache[cache_key] = {
            'data': response_data,
            'timestamp': time.time()
        }

        return jsonify(response_data)
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'version': '2.0',
        'endpoints': {
            '/api': {
                'method': 'GET',
                'params': {'url': 'Terabox share URL'}
            }
        }
    })

# Vercel serverless handler
def handler(request):
    with app.app_context():
        response = app.full_dispatch_request()()
        return response
