"""Bounded public GET transport. Per-exchange pacing, retries and cooldown."""
import json
import time
import socket
import random
import threading
from http.client import HTTPException
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from .schema import now_ms


class PublicHTTP:
    def __init__(self, timeout=8, retries=1, min_gap=0.3):
        self.timeout, self.retries, self.min_gap = timeout, retries, min_gap
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.cooldown_until = 0.0

    def get(self, url, params=None):
        # Discovery and depth collection share pacing and cooldown per venue.
        with self.lock:
            return self._get(url, params)

    def _get(self, url, params=None):
        full_url = url + ('?' + urlencode(params) if params else '')
        envelope = {'url': full_url, 'requested_at_ms': now_ms(), 'data': None,
                    'error': None, 'attempts': [], 'exchange_time_ms': None}
        start = time.monotonic()
        if start < self.cooldown_until:
            envelope.update(error='RATE_LIMIT_COOLDOWN', received_at_ms=now_ms(), latency_ms=0)
            return envelope
        for attempt in range(self.retries + 1):
            time.sleep(max(0, self.min_gap - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            status = None
            try:
                req = Request(full_url, headers={'User-Agent': 'FundingResearchCollector/0.1',
                                                'Accept': 'application/json'}, method='GET')
                with urlopen(req, timeout=self.timeout) as response:
                    status = response.status
                    data = json.load(response)
                envelope['data'] = data
                if isinstance(data, dict):
                    code = data.get('retCode', data.get('code', 0))
                    if str(code) in ('10006', '50011', '50040'):
                        self.cooldown_until = time.monotonic() + 60
                    if str(code) != '0':
                        raise ValueError(f'API_ERROR {code}: {data.get("retMsg", data.get("msg", ""))}')
                envelope['error'] = None
                envelope['attempts'].append({'status': status, 'error': None})
                break
            except HTTPError as exc:
                status = exc.code
                error = f'HTTP_{status}'
                try:
                    body = exc.read(65536).decode('utf-8', errors='replace')
                    envelope['error_body'] = body
                except (OSError, HTTPException, AttributeError):
                    pass
                if status in (418, 429):
                    try:
                        retry_after = float(exc.headers.get('Retry-After', '60'))
                    except ValueError:
                        retry_after = 60
                    self.cooldown_until = time.monotonic() + max(60, retry_after)
                retryable = status >= 500
            except (URLError, TimeoutError, socket.timeout, OSError, HTTPException) as exc:
                error = f'NETWORK: {exc}'
                retryable = True
            except (ValueError, TypeError) as exc:
                error = f'INVALID_RESPONSE: {exc}'
                retryable = False
            envelope['error'] = error
            envelope['attempts'].append({'status': status, 'error': error})
            if not retryable or attempt == self.retries:
                break
            time.sleep(min(2 ** attempt + random.random() * 0.2, 4))
        envelope['received_at_ms'] = now_ms()
        envelope['latency_ms'] = round((time.monotonic() - start) * 1000, 2)
        return envelope
