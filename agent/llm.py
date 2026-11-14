"""OpenAI compatible Chat Completions adapter for Yandex AI Studio."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def load_local_env():
    path = Path(__file__).resolve().parent.parent / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def configuration():
    load_local_env()
    key = os.environ.get('YC_API_KEY') or os.environ.get('LLM_API_KEY')
    folder = os.environ.get('YC_FOLDER_ID')
    model = os.environ.get('LLM_MODEL', 'yandexgpt-lite')
    if folder and not model.startswith('gpt://'):
        model = f'gpt://{folder}/{model}/latest'
    return {
        'available': bool(key and model and (folder or model.startswith('gpt://'))),
        'provider': 'Yandex AI Studio' if folder else 'OpenAI-compatible',
        'model': model if key else None,
        'base_url': os.environ.get('LLM_BASE_URL', 'https://ai.api.cloud.yandex.net/v1'),
        'key': key,
    }


class LLMError(Exception):
    pass


def complete_json(system, user):
    cfg = configuration()
    if not cfg['available']:
        raise LLMError('LLM не настроена: задайте YC_FOLDER_ID и YC_API_KEY в локальном .env.')
    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    body = json.dumps({'model': cfg['model'], 'temperature': 0, 'messages': [
        {'role': 'system', 'content': system + '\nОтветь только JSON-объектом без Markdown.'},
        {'role': 'user', 'content': user},
    ]}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, body, {'Authorization': 'Bearer ' + cfg['key'],
                                              'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise LLMError(f'LLM вернула HTTP {exc.code}. Проверьте модель и права ключа.') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError('LLM недоступна или превысила время ожидания.') from exc
    try:
        content = payload['choices'][0]['message']['content'].strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0]
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError('not an object')
        return result, payload.get('usage', {})
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise LLMError('LLM вернула ответ в неожиданном формате.') from exc

