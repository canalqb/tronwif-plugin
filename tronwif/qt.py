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
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QPlainTextEdit, QVBoxLayout)

from electrum.i18n import _
from electrum.plugin import hook
from electrum.gui.common_qt.util import TaskThread
from electrum.gui.qt.util import WindowModalDialog, Buttons, read_QIcon

from .tron import TronPlugin, is_tron_address, _parse_key, _tron_address
from .trx_address_list import TrxAddressList
from .trx_history_list import TrxHistoryList

if TYPE_CHECKING:
    from electrum.gui.qt.main_window import ElectrumWindow


class Plugin(TronPlugin):
    __slots__ = ('_wallet', '_tabs_data', '_bg_thread', '_last_address',
                 '_session_password', '_deriving', '_derive_attempted',
                 '_btc_count_scanned')

    def __init__(self, parent, config, name):
        TronPlugin.__init__(self, parent, config, name)
        self._wallet = None
        self._tabs_data = None
        self._bg_thread = None
        self._session_password = None
        self._deriving = False
        self._derive_attempted = set()
        self._btc_count_scanned = -1

    @hook
    def init_menubar(self, window: 'ElectrumWindow'):
        window.wallet_menu.addAction('TRX toolbox', lambda: self.setup_dialog(window))

    @hook
    def load_wallet(self, wallet, window: 'ElectrumWindow'):
        self.close_current_tabs()
        self._wallet = wallet

        addr_list = TrxAddressList(window, self)
        addr_tab = window.create_list_tab(addr_list)
        window.tabs.addTab(addr_tab, read_QIcon("tab_addresses.png"), _("Endereços TRX"))

        hist_list = TrxHistoryList(window, self)
        hist_tab = window.create_list_tab(hist_list)
        window.tabs.addTab(hist_tab, read_QIcon("tab_history.png"), _("Histórico TRX"))

        self._tabs_data = (window, wallet, addr_tab, hist_tab, addr_list, hist_list)
        self._auto_derive_trx(wallet, window, addr_list)
        self._add_btc_extract_button(window, wallet)

    def _add_btc_extract_button(self, window: 'ElectrumWindow', wallet):
        address_list = getattr(window, 'address_list', None)
        if address_list is None or getattr(address_list, '_tronwif_btc_btn', None) is not None:
            return
        btn = QPushButton(_("Extrair chaves BTC"))
        btn.setToolTip(_("Exporta endereços com saldo e pelo menos uma transação,\n"
                        "com WIF (comprimido/não-comprimido) e a chave privada em hex."))
        btn.clicked.connect(partial(self._show_btc_keys_dialog, window, wallet))
        address_list._tronwif_btc_btn = btn
        vbox = address_list.parentWidget().layout()
        toolbar = vbox.itemAt(0)
        if toolbar is None:
            return
        hbox = toolbar.layout().itemAt(1)
        if hbox is not None:
            hbox.layout().addWidget(btn)
            address_list.toolbar_buttons = tuple(address_list.toolbar_buttons) + (btn,)
        else:
            btn.deleteLater()
            address_list._tronwif_btc_btn = None

    def _show_btc_keys_dialog(self, window: 'ElectrumWindow', wallet):
        if not wallet.can_export():
            window.show_error(_("This wallet cannot export private keys (watch-only or hardware wallet)."))
            return
        password = None
        if wallet.has_password():
            from electrum.gui.qt.password_dialog import PasswordDialog
            d = PasswordDialog(window, _("Enter wallet password to extract BTC private keys"))
            password = d.run()
            if password is None:
                return
        from electrum import bitcoin
        rows = []
        for addr in wallet.get_addresses():
            if wallet.adb.get_address_history_len(addr) < 1:
                continue
            try:
                pk, _compressed = wallet.keystore.get_private_key(wallet.get_address_index(addr), password)
            except Exception:
                continue
            txin_type = wallet.get_txin_type(addr)
            private_hex = pk.hex()
            wif_compressed = bitcoin.serialize_privkey(pk, True, txin_type, internal_use=True)
            wif_uncompressed = bitcoin.serialize_privkey(pk, False, txin_type, internal_use=True)
            rows.append((addr, c + u + x, wif_compressed, wif_uncompressed, private_hex))
        if not rows:
            window.show_message(_("No funded address with at least one transaction found."))
            return
        dialog = WindowModalDialog(window, _("Extracted BTC keys"))
        dialog.setContentsMargins(11, 11, 1, 1)
        vbox = QVBoxLayout(dialog)
        result = QPlainTextEdit()
        result.setReadOnly(True)
        result.setFont(dialog.font())
        lines = [_("address,balance,WIF (compressed),WIF (uncompressed),private key hex")]
        for addr, balance, wif_c, wif_u, priv_hex in rows:
            lines.append(f"{addr},{balance},{wif_c},{wif_u},{priv_hex}")
        result.setPlainText('\n'.join(lines))
        vbox.addWidget(result)

        def on_copy():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText('\n'.join(lines[1:]))

        def on_save():
            from PyQt6.QtWidgets import QFileDialog
            filename, _filter = QFileDialog.getSaveFileName(
                dialog, _("Save extracted keys"), "btc_keys.csv", "*.csv")
            if not filename:
                return
            import csv
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["address", "balance", "wif_compressed", "wif_uncompressed", "private_key_hex"])
                for addr, balance, wif_c, wif_u, priv_hex in rows:
                    writer.writerow([addr, balance, wif_c, wif_u, priv_hex])
            window.show_message(_("Saved to ") + filename)

        btn_row = QHBoxLayout()
        copy_button = QPushButton(_("Copy"))
        copy_button.clicked.connect(on_copy)
        save_button = QPushButton(_("Save CSV"))
        save_button.clicked.connect(on_save)
        btn_row.addWidget(copy_button)
        btn_row.addWidget(save_button)
        btn_row.addStretch(1)
        vbox.addLayout(btn_row)
        vbox.addLayout(Buttons(QPushButton(_("Close"), dialog)))
        dialog.exec()

    def _auto_derive_trx(self, wallet, window, addr_list):
        password = None
        if wallet.has_password():
            from electrum.gui.qt.password_dialog import PasswordDialog
            d = PasswordDialog(window, _("Enter wallet password to derive TRX addresses"))
            password = d.run()
            if password is None:
                return
        self._session_password = password
        self._btc_count_scanned = len(wallet.get_addresses())
        if self._bg_thread is None:
            self._bg_thread = TaskThread(window)
        self._bg_thread.add(
            partial(self._auto_derive_worker, wallet, addr_list, password),
            lambda _: self._auto_derive_finished(addr_list),
            None,
            lambda exc: None,
        )

    def find_new_btc_addresses(self, wallet):
        if not self._tabs_data or wallet is not self._wallet:
            return []
        try:
            if not wallet.can_export():
                return []
            stored = self.get_stored_addresses(wallet)
            labels = {info.get('label', '') for info in stored.values()}
            return [a for a in wallet.get_addresses() if a not in labels]
        except Exception:
            return []

    def auto_derive_new(self, wallet, addr_list):
        """Deriva enderecos TRX para novos enderecos BTC (ex.: importados
        pelo menu Carteira -> Chaves privadas). Silencioso: se a senha da
        carteira ainda nao foi fornecida nesta sessao, nao faz nada.
        Cada endereco BTC so e processado UMA vez por sessao."""
        if self._deriving:
            return
        if wallet.has_password() and self._session_password is None:
            return
        btc_count = len(wallet.get_addresses())
        if btc_count == self._btc_count_scanned:
            return
        new_btc = [a for a in self.find_new_btc_addresses(wallet)
                   if a not in self._derive_attempted]
        self._btc_count_scanned = btc_count
        if not new_btc:
            return
        self._derive_attempted.update(new_btc)
        password = self._session_password if wallet.has_password() else None
        if self._bg_thread is None:
            self._bg_thread = TaskThread(addr_list)
        self._deriving = True
        self._bg_thread.add(
            partial(self._derive_worker, wallet, new_btc, password),
            lambda _: self._auto_derive_finished(addr_list),
            lambda: setattr(self, '_deriving', False),
            lambda exc: None,
        )

    def _derive_worker(self, wallet, btc_addresses, password):
        stored = self.get_stored_addresses(wallet)
        already = set(stored.keys())
        imported = 0
        for btc_addr in btc_addresses:
            try:
                wif = wallet.export_private_key(btc_addr, password)
                secret = _parse_key(wif)
                trx_addr, _raw = _tron_address(secret)
                if trx_addr not in already:
                    self.add_address(wallet, trx_addr, label=btc_addr, public_hex=secret.hex())
                    imported += 1
            except Exception:
                pass
        return imported

    def _auto_derive_worker(self, wallet, addr_list, password):
        btc_addresses = wallet.get_addresses()
        if not btc_addresses:
            return 0
        self._derive_attempted.update(btc_addresses)
        stored = self.get_stored_addresses(wallet)
        already = set(stored.keys())
        labels = {info.get('label', '') for info in stored.values()}
        imported = 0
        for btc_addr in btc_addresses:
            if btc_addr in labels:
                continue
            try:
                wif = wallet.export_private_key(btc_addr, password)
                secret = _parse_key(wif)
                trx_addr, _raw = _tron_address(secret)
                if trx_addr not in already:
                    self.add_address(wallet, trx_addr, label=btc_addr, public_hex=secret.hex())
                    imported += 1
            except Exception:
                pass
        return imported

    def _auto_derive_finished(self, addr_list):
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self._force_update_addr_list)

    def _force_update_addr_list(self):
        data = self._tabs_data
        if not data:
            return
        addr_list = data[4]
        addr_list._forced_update = True
        addr_list.update()
        addr_list._forced_update = False

    @hook
    def close_wallet(self, wallet, *args, **kwargs):
        self.close_current_tabs()

    def close_current_tabs(self):
        if not self._tabs_data:
            return
        window, _wallet, addr_tab, hist_tab, _addr_list, _hist_list = self._tabs_data
        for tab in (addr_tab, hist_tab):
            index = window.tabs.indexOf(tab)
            if index >= 0:
                window.tabs.removeTab(index)
            tab.deleteLater()
        self._tabs_data = None
        self._wallet = None
        self._session_password = None
        self._deriving = False
        self._derive_attempted.clear()

    def tronwif_show_history(self, address: str):
        data = self._tabs_data
        if not data:
            return
        window, _wallet, _addr_tab, hist_tab, _addr_list, hist_list = data
        if not is_tron_address(address):
            window.show_error(_("Invalid Tron address: ") + address)
            return
        hist_list.set_address(address)
        window.tabs.setCurrentWidget(hist_tab)

    def setup_dialog(self, main_window: 'ElectrumWindow'):
        dialog = WindowModalDialog(main_window, _("TRX toolbox"))
        dialog.setContentsMargins(11, 11, 1, 1)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.addWidget(QLabel(_("Private key (WIF / hex / decimal)")), 0, 0)
        key_edit = QLineEdit()
        grid.addWidget(key_edit, 0, 1, 1, 4)

        result = QPlainTextEdit()
        result.setReadOnly(True)
        result.setFont(dialog.font())
        grid.addWidget(result, 1, 0, 1, 5)

        save_check = QCheckBox(_("Add to TRX addresses tab"))
        save_check.setChecked(True)
        grid.addWidget(save_check, 2, 0, 1, 5)

        balance_button = QPushButton(_("Check balance"))
        balance_button.setEnabled(False)

        def save_address(data: dict):
            if not (save_check.isChecked() and self._wallet):
                return
            self.add_address(self._wallet, data['address'], label="", public_hex=data['public_key'])
            if self._tabs_data:
                self._tabs_data[4].update()

        def show_result(data: dict):
            result.setPlainText('\n'.join([
                f"TRX address : {data['address']}",
                f"  hex       : {data['address_hex']}",
                f"Private hex : {data['private_hex']}",
                f"Public key  : {data['public_key']}",
            ]))
            balance_button.setEnabled(True)
            self._last_address = data['address']
            save_address(data)

        def on_derive():
            try:
                result.setPlainText("")
                balance_button.setEnabled(False)
                self._last_address = None
                show_result(self.derive(key_edit.text()))
            except Exception as exc:
                result.setPlainText(f"{key_edit.text()}: {exc}")

        def on_balance():
            thread = TaskThread(dialog)
            thread.add(
                partial(self.fetch_balances, self._last_address),
                partial(show_balance, dialog),
                thread.stop,
                partial(show_balance_error, dialog),
            )

        def show_balance(dialog, data):
            lines = [
                f"{data['balance_sun'] / 1e6:,.6f} TRX ({data['balance_sun']:,} SUN)",
                "TRC20:",
            ]
            if data['trc20']:
                for entry in data['trc20']:
                    if entry['symbol']:
                        lines.append(f"    {entry['balance']:,.6f} {entry['symbol']}")
                    else:
                        lines.append(f"    {entry['balance']} raw TRC20 ({entry['contract']})")
            else:
                lines.append("    (none)")
            result.appendPlainText('\n' + '\n'.join(lines))
            save_address({'address': self._last_address, 'public_key': ''})

        def show_balance_error(dialog, exc_info):
            result.appendPlainText(f"\nbalance query failed: {exc_info[1]}")

        derive_button = QPushButton(_("Derive address"))
        derive_button.clicked.connect(on_derive)
        key_edit.returnPressed.connect(on_derive)
        balance_button.clicked.connect(on_balance)

        row = QHBoxLayout()
        row.addWidget(derive_button)
        row.addWidget(balance_button)
        row.addStretch(1)
        grid.addLayout(row, 3, 0, 1, 5)

        vbox = QVBoxLayout(dialog)
        vbox.addLayout(grid)
        vbox.addLayout(Buttons(QPushButton(_("Close"), dialog)))

        return bool(dialog.exec())
