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
from typing import TYPE_CHECKING

from electrum.commands import plugin_command

if TYPE_CHECKING:
    from .tron import TronPlugin
    from electrum.commands import Commands

plugin_name = "tronwif"


@plugin_command('', plugin_name)
async def tron_address(self: 'Commands', key: str, plugin: 'TronPlugin' = None) -> dict:
    """Derive a Tron address from a WIF or hex private key.

    arg:str:key:private key in WIF, hex or decimal form
    """
    return plugin.derive(key)


@plugin_command('', plugin_name)
async def tron_balance(self: 'Commands', address: str, plugin: 'TronPlugin' = None) -> dict:
    """Query the balance of a Tron address.

    arg:str:address:Tron base58 address
    """
    return await plugin.balances(address)