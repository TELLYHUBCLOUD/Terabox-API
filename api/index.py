from flask import Flask, request, jsonify
import requests
import re
import time
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Updated cookies (replace with fresh ones)
COOKIES = {
    'ndus': 'YvZNLrCteHuiHhOL5JkRGyt7mwk2eJ0crYm0-ZBu',  # Replace with fresh cookie
    'ndut_fmt': '143EC283EEA14790A54CD9359192BB84AD94DE8264161DB67EC48A8A55D29315',   # Replace with fresh cookie
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Referer': 'https://www.terabox.com/',
    'Accept-Language': 'en-US,en;q=0.9',
}

def validate_url(url):
    """More lenient URL validation"""
    terabox_domains = [
        'terabox.com',
        'teraboxapp.com',
        '1024terabox.com',
        'www.terabox.com',
        'teraboxlink.com'
    ]
    return any(domain in url for domain in terabox_domains)

def extract_surl(url):
    """Extract surl from various URL formats"""
    patterns = [
        r'/s/([^/]+)',
        r'/sharing/link\?surl=([^&]+)',
        r'/sharing/link/([^/]+)',
        r'surl=([^&]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_file_info(surl):
    """Get file information using API"""
    api_url = "https://www.terabox.com/share/list"
    
    params = {
        'app_id': '250528',
        'channel': 'dubox',
        'clienttype': '0',
        'shorturl': surl,
        'root': '1',
    }
    
    try:
        response = requests.get(
            api_url,
            params=params,
            cookies=COOKIES,
            headers=HEADERS,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"API request failed: {str(e)}")
        return None

def process_file_data(data):
    """Process the API response data"""
    if not data or 'list' not in data or not data['list']:
        return None
    
    files = []
    for item in data['list']:
        if item.get('isdir') == '1':
            continue  # Skip directories
            
        files.append({
            'filename': item.get('server_filename'),
            'size': item.get('size'),
            'size_formatted': format_size(int(item.get('size', 0))),
            'download_url': item.get('dlink'),
            'md5': item.get('md5'),
            'modified_time': item.get('server_mtime'),
        })
    
    return files

def format_size(size_bytes):
    """Convert bytes to human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

@app.route('/api', methods=['GET'])
def api_handler():
    """Main API endpoint"""
    url = request.args.get('url')
    if not url:
        return jsonify({
            'status': 'error',
            'message': 'URL parameter is required',
            'usage': '/api?url=TERABOX_SHARE_URL'
        }), 400
    
    if not validate_url(url):
        return jsonify({
            'status': 'error',
            'message': 'Invalid Terabox URL',
            'supported_domains': [
                'terabox.com',
                'teraboxapp.com',
                '1024terabox.com'
            ]
        }), 400
    
    try:
        surl = extract_surl(url)
        if not surl:
            return jsonify({
                'status': 'error',
                'message': 'Could not extract file ID from URL'
            }), 400
        
        data = get_file_info(surl)
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No file information found'
            }), 404
        
        files = process_file_data(data)
        if not files:
            return jsonify({
                'status': 'error',
                'message': 'No downloadable files found'
            }), 404
        
        return jsonify({
            'status': 'success',
            'url': url,
            'files': files,
            'count': len(files)
        })
    except Exception as e:
        logger.error(f"Processing error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'solution': 'Try again later or check your cookies'
        }), 500

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'service': 'Terabox API',
        'usage': '/api?url=YOUR_TERABOX_URL',
        'note': 'Cookies need to be updated regularly'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000)
