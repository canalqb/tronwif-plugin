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
import datetime
import enum
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QFont
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton)

from electrum.i18n import _
from electrum.gui.common_qt.util import TaskThread

from electrum.gui.qt.my_treeview import MyTreeView, MySortModel
from electrum.gui.qt.util import MONOSPACE_FONT, webopen
from .tron import TronPlugin

if TYPE_CHECKING:
    from electrum.gui.qt.main_window import ElectrumWindow

TRONSCAN_TX_URL = 'https://tronscan.org/#/transaction/{}'

_MONO_FONT = QFont(MONOSPACE_FONT)


def _format_time(ms):
    if not ms:
        return ''
    try:
        dt = datetime.datetime.fromtimestamp(ms / 1000)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (OverflowError, OSError, ValueError):
        return str(ms)


class TrxHistoryList(MyTreeView):

    class Columns(MyTreeView.BaseColumnsEnum):
        TIME = enum.auto()
        DESCRIPTION = enum.auto()
        AMOUNT = enum.auto()
        STATUS = enum.auto()
        TXID = enum.auto()

    filter_columns = [Columns.DESCRIPTION, Columns.TXID]

    ROLE_SORT_ORDER = Qt.ItemDataRole.UserRole + 1000
    ROLE_TXID = Qt.ItemDataRole.UserRole + 1001
    key_role = ROLE_TXID

    def __init__(self, main_window: 'ElectrumWindow', plugin: 'TronPlugin'):
        super().__init__(
            main_window=main_window,
            stretch_column=self.Columns.DESCRIPTION,
            editable_columns=[],
        )
        self.plugin = plugin
        self.wallet = self.main_window.wallet
        self.setSortingEnabled(True)
        self.std_model = QStandardItemModel(self)
        self.proxy = MySortModel(self, sort_role=self.ROLE_SORT_ORDER)
        self.proxy.setSourceModel(self.std_model)
        self.setModel(self.proxy)
        self._thread = None
        self._fetching = False
        self.sortByColumn(self.Columns.TIME, Qt.SortOrder.DescendingOrder)
        self.refresh_headers()

    def current_address(self) -> str:
        return self.address_edit.text().strip()

    def set_address(self, address: str):
        if not address:
            return
        self.address_edit.setText(address)
        self.fetch()

    def create_toolbar(self, config):
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(_("Address:")))
        self.address_edit = QLineEdit()
        self.address_edit.setFont(_MONO_FONT)
        self.address_edit.setMinimumWidth(320)
        self.address_edit.returnPressed.connect(self.fetch)
        toolbar.addWidget(self.address_edit)
        self.fetch_button = QPushButton(_("Fetch history"))
        self.fetch_button.clicked.connect(self.fetch)
        toolbar.addWidget(self.fetch_button)
        toolbar.addStretch(1)
        return toolbar

    def refresh_headers(self):
        headers = {
            self.Columns.TIME: _('Date'),
            self.Columns.DESCRIPTION: _('Description'),
            self.Columns.AMOUNT: _('Amount'),
            self.Columns.STATUS: _('Status'),
            self.Columns.TXID: _('TXID'),
        }
        self.update_headers(headers)

    def fetch(self):
        address = self.current_address()
        if not address or self._fetching:
            return
        self._fetching = True
        self.fetch_button.setEnabled(False)
        self.std_model.clear()
        self.filter()
        if self._thread is None:
            self._thread = TaskThread(self)
        self._thread.add(
            partial(self.plugin.fetch_history, address),
            partial(self.on_history_fetched),
            lambda: (setattr(self, '_fetching', False), self.fetch_button.setEnabled(True)),
            partial(self.on_fetch_error),
        )

    def on_history_fetched(self, data: dict):
        rows = []
        for entry in data.get('trx') or []:
            rows.append(self._make_row_trx(entry))
        for entry in data.get('trc20') or []:
            rows.append(self._make_row_trc20(entry))
        rows.sort(key=lambda r: r['time'])

        self.proxy.setDynamicSortFilter(False)
        for row in rows:
            self._insert_row(row)
        self.proxy.setDynamicSortFilter(True)
        self.filter()

        errors = []
        if data.get('trx_error'):
            errors.append(f"TRX: {data['trx_error']}")
        if data.get('trc20_error'):
            errors.append(f"TRC20: {data['trc20_error']}")
        if errors or not rows:
            msg = "\n".join(errors) if errors else _("No transactions found.")
            item = QStandardItem(msg)
            item.setEditable(False)
            self.std_model.appendRow([item,
                                      QStandardItem(), QStandardItem(), QStandardItem(), QStandardItem()])

    def on_fetch_error(self, exc_info):
        self.std_model.clear()
        item = QStandardItem(f"{_('history query failed:')} {exc_info[1]}")
        item.setEditable(False)
        self.std_model.appendRow([item, QStandardItem(), QStandardItem(), QStandardItem(), QStandardItem()])
        self.filter()

    def _make_row_trx(self, entry):
        amount = entry.get('amount')
        if amount is not None:
            amount_text = f"{amount / 1e6:,.6f} TRX"
        else:
            amount_text = ''
        desc = self._describe(entry.get('from'), entry.get('to'))
        return {
            'time': entry.get('time') or 0,
            'txid': entry.get('txid') or '',
            'text': [_format_time(entry.get('time')),
                     desc if desc else (entry.get('contract_type') or 'TRX'),
                     amount_text,
                     entry.get('status') or '',
                     entry.get('txid') or ''],
        }

    def _make_row_trc20(self, entry):
        decimals = entry.get('decimals') or 0
        symbol = entry.get('symbol') or (entry.get('token') or 'TRC20')
        value = entry.get('amount') or 0
        amount_text = f"{value / 10**decimals:,.{min(decimals, 6)}f} {symbol}"
        desc = self._describe(entry.get('from'), entry.get('to'))
        return {
            'time': entry.get('time') or 0,
            'txid': entry.get('txid') or '',
            'text': [_format_time(entry.get('time')),
                     desc if desc else (entry.get('type') or symbol),
                     amount_text,
                     entry.get('status') or '',
                     entry.get('txid') or ''],
        }

    @staticmethod
    def _describe(from_addr, to_addr):
        if from_addr and to_addr:
            if from_addr == to_addr:
                return f"{from_addr} → self"
            return f"{from_addr} → {to_addr}"
        return (from_addr or to_addr) or ''

    def _insert_row(self, row_data):
        time_col = self.Columns.TIME
        desc_col = self.Columns.DESCRIPTION
        role_time = self.ROLE_SORT_ORDER
        role_txid = self.ROLE_TXID
        txid = row_data['txid']
        items = [QStandardItem(text) for text in row_data['text']]
        for i, item in enumerate(items):
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
            if i != desc_col:
                item.setFont(_MONO_FONT)
            item.setEditable(False)
        items[time_col].setData(row_data['time'], role_time)
        for item in items:
            item.setData(txid, role_txid)
        self.std_model.insertRow(0, items)

    def on_double_click(self, idx):
        txid = self.get_role_data_for_current_item(col=0, role=self.ROLE_TXID)
        if txid:
            webopen(TRONSCAN_TX_URL.format(txid))

    def create_menu(self, position):
        idx = self.indexAt(position)
        if not idx.isValid():
            return
        item = self.item_from_index(idx)
        if not item:
            return
        txid = item.data(self.ROLE_TXID)
        menu = QMenu()
        if txid:
            menu.addAction(_("Copy TXID"), lambda: self.place_text_on_clipboard(str(txid), title=_("TXID")))
            menu.addAction(_("View on TronScan"), lambda: webopen(TRONSCAN_TX_URL.format(txid)))
        desc_item = self.item_from_index(idx.siblingAtColumn(self.Columns.DESCRIPTION))
        if desc_item:
            desc_text = desc_item.text()
            if desc_text:
                menu.addAction(_("Copy Label"), lambda: self.place_text_on_clipboard(desc_text, title=_("Label")))
        self.open_menu(menu, position)
