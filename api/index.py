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
}

@app.route('/api', methods=['GET'])
def api_handler():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400
    
    try:
        # Extract short URL ID
        surl = re.search(r'/s/([^/]+)', url) or re.search(r'surl=([^&]+)', url)
        if not surl:
            return jsonify({'error': 'Invalid Terabox URL format'}), 400
        
        surl = surl.group(1)
        
        # API request
        api_url = "https://www.terabox.com/share/list"
        params = {
            'app_id': '250528',
            'shorturl': surl,
            'root': '1',
        }
        
        response = requests.get(
            api_url,
            params=params,
            cookies=COOKIES,
            headers=HEADERS,
            timeout=30
        )
        data = response.json()
        
        if not data.get('list'):
            return jsonify({'error': 'No files found'}), 404
            
        # Process files
        files = []
        for item in data['list']:
            if item.get('isdir') == '1':
                continue
                
            files.append({
                'filename': item.get('server_filename'),
                'size': item.get('size'),
                'download_url': item.get('dlink'),
            })
        
        return jsonify({
            'status': 'success',
            'files': files,
            'count': len(files)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'usage': '/api?url=YOUR_TERABOX_URL'
    })

# Vercel requires this
def handler(request):
    with app.app_context():
        response = app.full_dispatch_request()()
        return response
