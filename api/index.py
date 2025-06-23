from flask import Flask, request, jsonify
import os
import logging
import re
import time
import requests
from urllib.parse import urlparse, parse_qs

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2
PORT = 3000
DEVELOPER = "@Farooq_is_king"

SUPPORTED_DOMAINS = [
    "terabox.com", "1024terabox.com", "teraboxapp.com", "teraboxlink.com",
    "terasharelink.com", "terafileshare.com", "www.1024tera.com",
    "1024tera.com", "1024tera.cn", "teraboxdrive.com", "dubox.com"
]

TERABOX_URL_REGEX = r'^https:\/\/(www\.)?(terabox\.com|1024terabox\.com|teraboxapp\.com|teraboxlink\.com|terasharelink\.com|terafileshare\.com|1024tera\.com|1024tera\.cn|teraboxdrive\.com|dubox\.com)\/(s|sharing\/link)\/[A-Za-z0-9_\-]+'

COOKIES = {
    # Updated cookies here (as in your code)
    'ndut_fmt': '082E0D57C65BDC31F6FF293F5D23164958B85D6952CCB6ED5D8A3870CB302BE7',
    'ndus': 'Y-wWXKyteHuigAhC03Fr4bbee-QguZ4JC6UAdqap',
    'csrfToken': 'wlv_WNcWCjBtbNQDrHSnut2h',
    'lang': 'en',
    'PANWEB': '1',
    # ... more as needed
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/135.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
}

def validate_terabox_url(url):
    return re.match(TERABOX_URL_REGEX, url) is not None

def make_request(url, method='GET', headers=HEADERS, params=None, allow_redirects=True, cookies=None):
    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                cookies=cookies,
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
            logger.warning(f"Attempt {retries+1}: {e}")
            time.sleep(RETRY_DELAY)
            retries += 1
    raise Exception("Failed after max retries")

def extract_tokens(html):
    token_match = re.search(r'fn\(["\'](.*?)["\']\)', html)
    if not token_match:
        token_match = re.search(r'fn%28%22(.*?)%22%29', html)
    if not token_match:
        raise Exception("Token not found")
    js_token = token_match.group(1)

    log_id_match = re.search(r'dp-logid=([^&\'"]+)', html)
    if not log_id_match:
        raise Exception("Log ID not found")
    log_id = log_id_match.group(1)

    return js_token, log_id

def get_surl(response_url):
    parsed = urlparse(response_url)
    if '/s/' in parsed.path:
        return parsed.path.split('/s/')[1].split('/')[0]
    match = re.search(r'/(s|sharing/link)/([A-Za-z0-9_\-]+)', response_url)
    return match.group(2) if match else None

def get_direct_link(url, cookies):
    try:
        response = make_request(url, method='HEAD', allow_redirects=False, cookies=cookies)
        return response.headers.get('Location', url) if 300 <= response.status_code < 400 else url
    except:
        return url

def format_size(size):
    try:
        size = int(size)
        if size >= 1024 ** 3:
            return f"{size / 1024**3:.2f} GB"
        elif size >= 1024 ** 2:
            return f"{size / 1024**2:.2f} MB"
        elif size >= 1024:
            return f"{size / 1024:.2f} KB"
        return f"{size} bytes"
    except:
        return "Unknown"

def process_terabox_url(url):
    response = make_request(url, cookies=COOKIES)
    html = response.text
    js_token, log_id = extract_tokens(html)
    surl = get_surl(response.url)

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
        'site_referer': response.url,
        'shorturl': surl,
        'root': '1'
    }

    api_resp = make_request('https://www.1024tera.com/share/list', params=params, cookies=COOKIES)
    file_data = api_resp.json()

    file_list = file_data.get("list", [])
    if not file_list:
        raise Exception("No files found")

    if int(file_list[0].get('isdir', 0)) == 1:
        params.update({
            'dir': file_list[0]['path'],
            'order': 'asc',
            'by': 'name',
        })
        params.pop('desc', None)
        params.pop('root', None)

        folder_resp = make_request('https://www.1024tera.com/share/list', params=params, cookies=COOKIES).json()
        file_list = [f for f in folder_resp.get('list', []) if int(f.get("isdir", 0)) == 0]

    results = []
    for file in file_list:
        dlink = file.get("dlink")
        direct = get_direct_link(dlink, COOKIES) if dlink else None
        results.append({
            "file_name": file.get("server_filename", "Unknown"),
            "size": format_size(file.get("size")),
            "size_bytes": int(file.get("size", 0)),
            "download_url": dlink,
            "direct_download_url": direct,
            "is_directory": int(file.get("isdir", 0)) == 1,
            "modify_time": file.get("server_mtime", 0),
            "thumbnails": file.get("thumbs", {})
        })
    return results

@app.route('/api', methods=['GET'])
def api_handler():
    url = request.args.get('url')
    if not url:
        return jsonify({
            "status": "error",
            "message": "Missing 'url' parameter",
            "usage": "/api?url=TERABOX_SHARE_URL"
        }), 400

    if not validate_terabox_url(url):
        return jsonify({
            "status": "error",
            "message": "Invalid Terabox URL",
            "supported_domains": SUPPORTED_DOMAINS
        }), 400

    try:
        start = time.time()
        files = process_terabox_url(url)
        return jsonify({
            "status": "success",
            "url": url,
            "files": files,
            "processing_time": f"{time.time() - start:.2f}s",
            "file_count": len(files),
            "cookies": "valid"
        })
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Service error: {str(e)}",
            "developer": DEVELOPER
        }), 500

@app.route('/')
def home():
    return jsonify({
        "status": "API Running",
        "developer": DEVELOPER,
        "usage": "/api?url=TERABOX_SHARE_URL",
        "supported_domains": SUPPORTED_DOMAINS,
        "cookie_status": "valid"
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", PORT))
    logger.info(f"Running on port {port}")
    app.run(host="0.0.0.0", port=port)
