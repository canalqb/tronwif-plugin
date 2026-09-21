#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2026 The Electrum Developers
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""TRX toolbox plugin.

Derives Tron (TRX) wallet addresses from Bitcoin-style WIF / hex private
keys and looks up TRX + TRC20 balances via the TronGrid v1 API.

The Keccak-256 used by Tron is NOT the NIST SHA3 from hashlib.sha3_256,
so a self-contained (pre-NIST, Ethereum-compatible) Keccak implementation
is shipped here.
"""
import asyncio
import hashlib
import json
import ssl
import time
import urllib.parse
from typing import Any, Dict, Tuple

from electrum.plugin import BasePlugin

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

_SECP256K1_P = 2**256 - 2**32 - 977
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP256K1_G = (
    55066263022277343669578718895168534326250603453777594175500187360389116729240,
    32670510020758816978083085130507043184471273380659243275938904335757337482424,
)

_TRC20 = {
    'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t': ('USDT', 6),
    'TEkxiTehnzSmSe2XqrBj4w32RUN966rDz8': ('USDC', 6),
    'TPYmHEhy5n8TCEfYqTQbbgJDhCgZuW4e9': ('USDD', 18),
    'TUpMhErZL2fhh4sVNULAbNKLokS4GjC1F4': ('TUSD', 18),
}

DEFAULT_API_URL = 'https://api.trongrid.io'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1_2


def _http_get_json(url: str, timeout: int) -> dict:
    import urllib.request
    if not url.startswith('https://'):
        raise ValueError('insecure scheme rejected (https required): ' + url)
    request = urllib.request.Request(url, headers={'User-Agent': 'electrum-tronwif/1.0'})
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CTX) as response:
        return json.load(response)


def _b58decode(text):
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    pad = len(text) - len(text.lstrip('1'))
    return b'\x00' * pad + raw


def _b58encode(raw):
    n = int.from_bytes(raw, 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    for byte in raw:
        if byte:
            break
        out = '1' + out
    return out


def _dsha256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def b58encode_check(payload):
    return _b58encode(payload + _dsha256(payload)[:4])


def _secret_to_wif(secret: bytes, compressed: bool = False) -> str:
    payload = b'\x80' + secret
    if compressed:
        payload += b'\x01'
    return b58encode_check(payload)


def _wif_decode(wif):
    data = _b58decode(wif)
    if len(data) not in (37, 38) or _dsha256(data[:-4])[:4] != data[-4:]:
        raise ValueError('invalid WIF checksum')
    secret = data[1:33]
    suffix = data[33:-4]
    if len(secret) != 32:
        raise ValueError('invalid WIF secret length')
    if suffix == b'\x01':
        return secret, True
    if suffix:
        raise ValueError('invalid WIF compression flag')
    return secret, False


def _ec_add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1 = p
    x2, y2 = q
    if x1 == x2 and (y1 + y2) % _SECP256K1_P == 0:
        return None
    lam = ((y2 - y1) * pow(x2 - x1, _SECP256K1_P - 2, _SECP256K1_P)) % _SECP256K1_P
    x3 = (lam * lam - x1 - x2) % _SECP256K1_P
    return (x3, (lam * (x1 - x3) - y1) % _SECP256K1_P)


def _ec_double(p):
    x, y = p
    lam = (3 * x * x) * pow(2 * y, _SECP256K1_P - 2, _SECP256K1_P) % _SECP256K1_P
    x3 = (lam * lam - 2 * x) % _SECP256K1_P
    return (x3, (lam * (x - x3) - y) % _SECP256K1_P)


def _ec_scalar_mult(k, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _ec_add(result, addend)
        addend = _ec_double(addend)
        k >>= 1
    return result


def _privkey_to_int(secret):
    value = int.from_bytes(secret, 'big')
    if not 0 < value < _SECP256K1_N:
        raise ValueError('private key out of curve range')
    return value


def _public_key(secret):
    # caminho rapido: libsecp256k1 nativa (C) embutida no Electrum
    try:
        import electrum_ecc
        return electrum_ecc.ECPrivkey(secret).get_public_key_bytes(compressed=False)
    except Exception:
        pass
    # fallback: implementacao pura em Python
    x, y = _ec_scalar_mult(_privkey_to_int(secret), _SECP256K1_G)
    return b'\x04' + x.to_bytes(32, 'big') + y.to_bytes(32, 'big')


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF


_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)


def _keccak_f(state):
    for rc in _RC:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(state[x + 5 * y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ (
                    (~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        state[0] ^= rc


def _keccak256(data):
    rate = 136
    msg = bytearray(data)
    msg.append(0x01)
    while len(msg) % rate != rate - 1:
        msg.append(0x00)
    msg.append(0x80)
    state = [0] * 25
    for offset in range(0, len(msg), rate):
        block = msg[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], 'little')
        _keccak_f(state)
    return b''.join(state[i].to_bytes(8, 'little') for i in range(4))


def _parse_key(text: str) -> bytes:
    text = text.strip()
    if ':' in text:
        prefix = text.split(':')[0]
        if prefix in ('p2pkh', 'p2wpkh-p2sh', 'p2wpkh', 'p2sh', 'p2wsh'):
            text = text.split(':', 1)[1]
    if text.startswith('0x') or text.startswith('0X'):
        secret = bytes.fromhex(text[2:])
        if len(secret) != 32:
            raise ValueError('invalid hex private key length')
        return secret
    if len(text) == 64 and all(c in '0123456789abcdefABCDEF' for c in text):
        secret = bytes.fromhex(text)
        if len(secret) != 32:
            raise ValueError('invalid hex private key length')
        return secret
    if text.isdigit():
        value = int(text)
        if not 0 < value < _SECP256K1_N:
            raise ValueError('private key out of curve range')
        return value.to_bytes(32, 'big')
    return _wif_decode(text)[0]


def _tron_address(secret: bytes) -> Tuple[str, bytes]:
    pub = _public_key(secret)
    digest = _keccak256(pub[1:])
    raw = b'\x41' + digest[-20:]
    return b58encode_check(raw), raw


def _format_trc20(holdings) -> list:
    lines = []
    for maker in holdings:
        for contract, raw_amount in maker.items():
            if contract in _TRC20:
                symbol, decimals = _TRC20[contract]
                lines.append({'contract': contract, 'symbol': symbol, 'balance': int(raw_amount) / 10**decimals})
            else:
                lines.append({'contract': contract, 'symbol': None, 'balance': int(raw_amount)})
    return lines


def _hex_to_base58(hex_addr: str) -> str:
    if not hex_addr:
        return ''
    try:
        raw = bytes.fromhex(hex_addr)
    except (ValueError, TypeError):
        return hex_addr
    if len(raw) != 21:
        return hex_addr
    return b58encode_check(raw)


def is_tron_address(address: str) -> bool:
    try:
        data = _b58decode(address)
        if len(data) != 25:
            return False
        if data[0] != 0x41:
            return False
        return _dsha256(data[:-4])[:4] == data[-4:]
    except Exception:
        return False


class TronPlugin(BasePlugin):
    __slots__ = ('api_url', 'timeout')

    def __init__(self, parent, config, name):
        BasePlugin.__init__(self, parent, config, name)
        self.api_url = DEFAULT_API_URL
        try:
            self.timeout = int(self.config.get('plugins.tronwif.timeout', 30))
        except (TypeError, ValueError):
            self.timeout = 30
        if not self.api_url.startswith('https://'):
            raise ValueError('tronwif api_url must use https')

    def derive(self, key_text: str) -> Dict[str, Any]:
        secret = _parse_key(key_text)
        value = _privkey_to_int(secret)
        pubkey = _public_key(secret)
        address, address_raw = _tron_address(secret)
        return {
            'key': key_text.strip(),
            'private_hex': f'{value:064x}',
            'private_int': value,
            'public_key': pubkey.hex(),
            'address': address,
            'address_hex': address_raw.hex(),
        }

    def fetch_balances(self, address: str):
        url = f'{self.api_url}/v1/accounts/{urllib.parse.quote(address)}'
        data = _http_get_json(url, self.timeout)
        account = (data.get('data') or [{}])[0]
        return {
            'balance_sun': account.get('balance', 0),
            'trc20': _format_trc20(account.get('trc20') or []),
        }

    async def balances(self, address: str) -> Dict[str, Any]:
        result = await asyncio.to_thread(self.fetch_balances, address)
        result['address'] = address
        return result

    def get_stored_addresses(self, wallet) -> dict:
        storage = self.get_storage(wallet)
        return storage.setdefault('addresses', {})

    def add_address(self, wallet, address: str, *, label: str = '', public_hex: str = '') -> bool:
        addresses = self.get_stored_addresses(wallet)
        if address in addresses:
            return False
        addresses[address] = {
            'label': label,
            'public_hex': public_hex,
            'added': time.time(),
        }
        return True

    def remove_address(self, wallet, address: str) -> bool:
        addresses = self.get_stored_addresses(wallet)
        return addresses.pop(address, None) is not None

    def set_address_label(self, wallet, address: str, label: str) -> None:
        addresses = self.get_stored_addresses(wallet)
        if address in addresses:
            addresses[address]['label'] = label

    def get_balances_cache(self, wallet) -> dict:
        storage = self.get_storage(wallet)
        return storage.setdefault('balances', {})

    def get_cached_balance(self, wallet, address: str):
        cache = self.get_balances_cache(wallet)
        return cache.get(address)

    def save_balance(self, wallet, address: str, balance_sun: int):
        cache = self.get_balances_cache(wallet)
        cache[address] = {
            'balance_sun': balance_sun,
            'updated': time.time(),
        }

    def _fetch_json(self, url: str):
        return _http_get_json(url, self.timeout)

    def _fetch_trx_history(self, address: str) -> list:
        url = (f'{self.api_url}/v1/accounts/{urllib.parse.quote(address)}'
               f'/transactions?limit=200&order_by=block_timestamp,desc')
        data = self._fetch_json(url)
        items = []
        for tx in data.get('data') or []:
            raw = tx.get('raw_data') or {}
            contracts = raw.get('contract') or []
            ctype = ''
            value = {}
            owner_b58 = ''
            to_b58 = ''
            for contract in contracts:
                ctype = contract.get('type') or ''
                param = contract.get('parameter') or {}
                value = param.get('value') or {}
                owner_b58 = _hex_to_base58(value.get('owner_address') or '')
                to_b58 = _hex_to_base58(value.get('to_address') or value.get('to') or '')
                break
            ret = tx.get('contractRet') or tx.get('result') or 'SUCCESS'
            items.append({
                'txid': tx.get('txID') or '',
                'time': tx.get('blockTimeStamp') or tx.get('block_timestamp') or 0,
                'block': tx.get('blockNumber'),
                'contract_type': ctype,
                'status': 'success' if ret == 'SUCCESS' else 'failed',
                'amount': value.get('amount'),
                'from': owner_b58,
                'to': to_b58,
            })
        return items

    def _fetch_trc20_history(self, address: str) -> list:
        url = (f'{self.api_url}/v1/accounts/{urllib.parse.quote(address)}'
               f'/transactions/trc20?limit=200&order_by=block_timestamp,desc')
        data = self._fetch_json(url)
        items = []
        for tx in data.get('data') or []:
            token = tx.get('token_info') or {}
            raw_value = tx.get('value')
            try:
                amount = int(raw_value) if raw_value not in (None, '') else 0
            except (ValueError, TypeError):
                amount = 0
            items.append({
                'txid': tx.get('transaction_id') or '',
                'time': tx.get('block_timestamp') or 0,
                'symbol': token.get('symbol'),
                'token': token.get('address'),
                'decimals': token.get('decimals'),
                'type': tx.get('type') or '',
                'amount': amount,
                'from': tx.get('from') or '',
                'to': tx.get('to') or '',
                'status': 'success',
            })
        return items

    def fetch_history(self, address: str) -> Dict[str, Any]:
        result = {'address': address, 'trx': [], 'trc20': []}
        try:
            result['trx'] = self._fetch_trx_history(address)
        except Exception as e:
            result['trx_error'] = str(e)
        try:
            result['trc20'] = self._fetch_trc20_history(address)
        except Exception as e:
            result['trc20_error'] = str(e)
        return result


