"""Issue short-lived local Superset guest tokens for the embedded dashboard."""
import http.cookiejar
import json
import urllib.request

from .store import ROOT, RUNTIME

BASE = 'http://127.0.0.1:8088'


def guest_token():
    config = json.loads((RUNTIME / 'superset.json').read_text(encoding='utf-8'))
    values = dict(line.split('=', 1) for line in (ROOT / '.env.superset').read_text(encoding='utf-8').splitlines() if '=' in line)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None, token=None, csrf=None):
        headers = {'Content-Type': 'application/json', 'Referer': BASE + '/'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if csrf:
            headers['X-CSRFToken'] = csrf
        request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers, method='POST' if body is not None else 'GET')
        with opener.open(request, timeout=12) as response:
            return json.load(response)

    access = call('/api/v1/security/login', {
        'username': values['SUPERSET_ADMIN_USERNAME'], 'password': values['SUPERSET_ADMIN_PASSWORD'],
        'provider': 'db', 'refresh': True,
    })['access_token']
    csrf = call('/api/v1/security/csrf_token/', token=access)['result']
    result = call('/api/v1/security/guest_token/', {
        'user': {'username': 't1-viewer', 'first_name': 'T1', 'last_name': 'Viewer'},
        'resources': [{'type': 'dashboard', 'id': config['embedded_id']}], 'rls': [],
    }, token=access, csrf=csrf)
    return result['token']
