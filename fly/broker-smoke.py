"""One minimal Abliterated completion over the worker's private broker route."""
import http.client
import json
import os
from urllib.parse import urlsplit

REQUEST_BODY = {
    'model': 'morph-dsv41flash-abliterated',
    'messages': [{'role': 'user', 'content': 'Reply with exactly: ok'}],
    'max_tokens': 64,
    'stream': False,
}


def require_ack(status, payload):
    try:
        choices = payload['choices']
        content = choices[0]['message']['content']
        acknowledged = bool(content and content.strip())
    except (KeyError, IndexError, TypeError, AttributeError):
        acknowledged = False
    if status != 200 or not acknowledged:
        raise RuntimeError(broker_error(status))


def broker_error(status):
    return f'broker smoke failed: HTTP {status}, expected a model completion'


def main():
    for credential_name in ('MORPH_API_KEY', 'FLY_API_TOKEN'):
        if os.environ.get(credential_name):
            raise RuntimeError(f'{credential_name} must never be supplied to this worker')
    parsed = urlsplit(os.environ['HACK_ME_MODEL_BROKER_URL'])
    controller = os.environ['HACK_ME_CONTROL_PRIVATE_IP']
    if (
        parsed.scheme != 'http'
        or parsed.port != 8081
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not (
            parsed.hostname == controller
            or (parsed.hostname or '').endswith('.internal')
        )
    ):
        raise SystemExit('private broker endpoint is not a permitted control-plane route')
    path = parsed.path.rstrip('/') + '/chat/completions'
    print('preflight: broker-tcp-start', flush=True)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=60)
    try:
        connection.request(
            'POST', path, body=json.dumps(REQUEST_BODY).encode(),
            headers={
                'Authorization': f"Bearer {os.environ['HACK_ME_BROKER_TOKEN']}",
                'Content-Type': 'application/json',
            },
        )
        print('preflight: broker-request-sent', flush=True)
        response = connection.getresponse()
        status = response.status
        print(f'preflight: broker-response-status={status}', flush=True)
        payload = json.loads(response.read(1_048_576))
        print(f'preflight: broker-response-body-bytes={len(json.dumps(payload))}', flush=True)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise SystemExit(f'broker connection failed: {str(exc)[:160]}')
    finally:
        connection.close()
    require_ack(status, payload)
    print('preflight: broker-model-completion=ok', flush=True)


if __name__ == '__main__':
    main()
