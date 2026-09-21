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
import enum
import time
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
                             QMenu, QMessageBox, QPushButton, QVBoxLayout)

from electrum.i18n import _
from electrum.gui.common_qt.util import TaskThread
from electrum.util import profiler

from electrum.gui.qt.my_treeview import MyTreeView, MySortModel
from electrum.gui.qt.util import MONOSPACE_FONT, WindowModalDialog, webopen
from .tron import (TronPlugin, is_tron_address, _parse_key, _tron_address,
                   _secret_to_wif)

if TYPE_CHECKING:
    from electrum.gui.qt.main_window import ElectrumWindow

TRONSCAN_URL = 'https://tronscan.org/#/address/{}'

_MONO_FONT = QFont(MONOSPACE_FONT)


class TrxAddressList(MyTreeView):
    balance_ready = pyqtSignal(str, int)

    class Columns(MyTreeView.BaseColumnsEnum):
        ADDRESS = enum.auto()
        LABEL = enum.auto()
        TRX = enum.auto()

    filter_columns = [Columns.ADDRESS, Columns.LABEL]

    ROLE_SORT_ORDER = Qt.ItemDataRole.UserRole + 1000
    ROLE_ADDRESS_STR = Qt.ItemDataRole.UserRole + 1001
    key_role = ROLE_ADDRESS_STR

    def __init__(self, main_window: 'ElectrumWindow', plugin: 'TronPlugin'):
        super().__init__(
            main_window=main_window,
            stretch_column=self.Columns.LABEL,
            editable_columns=[self.Columns.LABEL],
        )
        self.plugin = plugin
        self.wallet = self.main_window.wallet
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.std_model = QStandardItemModel(self)
        self.proxy = MySortModel(self, sort_role=self.ROLE_SORT_ORDER)
        self.proxy.setSourceModel(self.std_model)
        self.setModel(self.proxy)
        self._addresses = {}
        self._thread = None
        self._refreshing = False
        self._failed_addresses = set()
        self._attempted = set()
        self._row_by_addr = {}
        self._fetch_total = 0
        self._fetch_done = 0
        self.num_addr_label = None
        self.balance_ready.connect(self._update_row_balance)
        self.update()

    def get_addresses(self) -> dict:
        return self.plugin.get_stored_addresses(self.wallet)

    def on_double_click(self, idx):
        address = self.get_role_data_for_current_item(col=0, role=self.ROLE_ADDRESS_STR)
        if address:
            self.plugin.tronwif_show_history(address)

    def create_toolbar(self, config):
        toolbar, menu = self.create_toolbar_with_menu('')
        self.num_addr_label = toolbar.itemAt(0).widget()
        self._toolbar_checkbox = menu.addToggle(_("Show Filter"), lambda: self.toggle_toolbar())
        toolbar.insertLayout(1, self.create_toolbar_buttons())
        if self.config:
            self.configvar_show_toolbar = self.config.cv.GUI_QT_ADDRESSES_TAB_SHOW_TOOLBAR
            menu.addConfig(
                self.configvar_show_toolbar,
                short_desc=_("Show toolbuttons"),
                checked=True,
            )
        return toolbar

    def get_toolbar_buttons(self):
        refresh_button = QPushButton(_("Atualizar saldos"))
        refresh_button.setToolTip(_("Buscar saldos TRX de todos os enderecos via TronGrid"))
        refresh_button.clicked.connect(self._force_refresh)
        add_button = QPushButton(_("Adicionar"))
        add_button.clicked.connect(self.add_address_dialog)
        import_button = QPushButton(_("Importar da carteira"))
        import_button.clicked.connect(self.import_from_wallet_dialog)
        export_bal_button = QPushButton(_("Exportar com saldo"))
        export_bal_button.clicked.connect(self._export_with_balance)
        export_tx_button = QPushButton(_("Exportar com TX"))
        export_tx_button.clicked.connect(self._export_with_tx)
        remove_button = QPushButton(_("Remover"))
        remove_button.clicked.connect(self.remove_selected)
        return refresh_button, add_button, import_button, export_bal_button, export_tx_button, remove_button

    def on_hide_toolbar(self):
        pass

    def refresh_headers(self):
        headers = {
            self.Columns.ADDRESS: _('Endereço TRX'),
            self.Columns.LABEL: _('Rótulo'),
            self.Columns.TRX: 'TRX',
        }
        self.update_headers(headers)

    @profiler
    def update(self):
        if self.maybe_defer_update():
            return
        self._addresses = dict(self.get_addresses())
        self.proxy.setDynamicSortFilter(False)
        self.std_model.clear()
        self.refresh_headers()

        uncached = []
        self._row_by_addr = {}
        addr_col = self.Columns.ADDRESS
        label_col = self.Columns.LABEL
        trx_col = self.Columns.TRX
        role_sort = self.ROLE_SORT_ORDER
        role_addr = self.ROLE_ADDRESS_STR

        for row, (address, info) in enumerate(self._addresses.items()):
            items = [QStandardItem('') for _ in self.Columns]
            items[addr_col].setText(address)
            items[label_col].setText(info.get('public_hex', ''))
            for i, item in enumerate(items):
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                if i != label_col:
                    item.setFont(_MONO_FONT)
            self.set_editability(items)
            items[addr_col].setData(address, role_addr)
            items[addr_col].setData(address.lower(), role_sort)
            cached = self.plugin.get_cached_balance(self.wallet, address)
            if cached is not None:
                value = cached['balance_sun'] / 1e6
                items[trx_col].setText(f"{value:,.6f}")
                items[trx_col].setData(value, role_sort)
            else:
                items[trx_col].setData(-1, role_sort)
                uncached.append(address)
            self.std_model.insertRow(row, items)
            self._row_by_addr[address] = row

        self.proxy.setDynamicSortFilter(True)
        self.filter()
        if self.num_addr_label:
            label = _("{} endereços TRX").format(len(self._addresses))
            updated = self.plugin.get_balances_cache(self.wallet).get('updated')
            if updated:
                label += " · " + _("atualizado às {}").format(time.strftime('%H:%M', time.localtime(updated)))
            self.num_addr_label.setText(label)
        uncached = [a for a in uncached if a not in self._attempted]
        if uncached:
            self._fetch_uncached(uncached)
        self.plugin.auto_derive_new(self.wallet, self)

    def _fetch_uncached(self, addresses):
        if self._refreshing or not addresses:
            return
        self._refreshing = True
        self._attempted.update(addresses)
        self._fetch_total = len(addresses)
        self._fetch_done = 0
        if self.num_addr_label:
            self.num_addr_label.setText(_("Consultando saldos: 0/{}").format(self._fetch_total))
        if self._thread is None:
            self._thread = TaskThread(self)
        self._thread.add(
            partial(self._fetch_balances_loop, addresses, retry=0),
            lambda result: self._on_all_done(result),
            lambda: setattr(self, '_refreshing', False),
            partial(self.on_refresh_error),
        )

    def _fetch_balances_loop(self, addresses, retry=0):
        import time as _time
        failed = []
        for address in addresses:
            try:
                data = self.plugin.fetch_balances(address)
                balance_sun = data.get('balance_sun', 0)
                self.plugin.save_balance(self.wallet, address, balance_sun)
                self.balance_ready.emit(address, balance_sun)
            except Exception:
                failed.append(address)
            _time.sleep(0.1)
        if failed and retry < 3:
            _time.sleep(1)
            return self._fetch_balances_loop(failed, retry + 1)
        return failed

    def _update_row_balance(self, address, balance_sun):
        self._failed_addresses.discard(address)
        self._fetch_done += 1
        if self.num_addr_label and self._fetch_total:
            self.num_addr_label.setText(_("Consultando saldos: {}/{}").format(self._fetch_done, self._fetch_total))
        row = self._row_by_addr.get(address)
        if row is None:
            return
        cell = self.std_model.item(row, self.Columns.TRX)
        if cell is None:
            return
        value = balance_sun / 1e6
        cell.setText(f"{value:,.6f}")
        cell.setData(value, self.ROLE_SORT_ORDER)

    def _on_all_done(self, failed):
        self._refreshing = False
        self._failed_addresses = set(failed) if failed else set()
        cache = self.plugin.get_balances_cache(self.wallet)
        cache['updated'] = time.time()
        if self.num_addr_label:
            label = _("{} endereços TRX").format(len(self._addresses))
            label += " · " + _("atualizado às {}").format(time.strftime('%H:%M'))
            if failed:
                label += " · {} {}".format(len(failed), _("falha(s)"))
            self.num_addr_label.setText(label)

    def _force_refresh(self):
        self._attempted.clear()
        if self._failed_addresses:
            self._fetch_uncached(list(self._failed_addresses))
        else:
            self._fetch_uncached(list(self._addresses.keys()))

    def on_refresh_error(self, exc_info):
        self.logger.info(f"TRX balance refresh failed: {exc_info[1]}")

    def add_address_dialog(self):
        dialog = WindowModalDialog(self.main_window, _("Add TRX address"))
        dialog.setMinimumWidth(430)
        vbox = QVBoxLayout(dialog)
        vbox.addWidget(QLabel(_("Tron address or private key (WIF / hex / decimal):")))
        line = QLineEdit()
        line.setPlaceholderText("T... or WIF or hex key")
        vbox.addWidget(line)

        def on_ok():
            text = line.text().strip()
            if not text:
                return
            if is_tron_address(text):
                self.plugin.add_address(self.wallet, text)
                dialog.accept()
                return
            try:
                secret = _parse_key(text)
                address, _raw = _tron_address(secret)
                self.plugin.add_address(self.wallet, address, public_hex=secret.hex())
                dialog.accept()
            except Exception as e:
                QMessageBox.warning(dialog, _("Error"), _("Invalid input: {}").format(str(e)))

        ok = QPushButton(_("OK"))
        ok.clicked.connect(on_ok)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(dialog.reject)
        hbox = QHBoxLayout()
        hbox.addStretch(1)
        hbox.addWidget(ok)
        hbox.addWidget(cancel)
        vbox.addLayout(hbox)
        dialog.exec()
        self.update()

    def import_from_wallet_dialog(self):
        wallet = self.wallet
        if not wallet.can_export():
            QMessageBox.warning(self.main_window, _("Error"),
                                _("This wallet type does not support exporting private keys."))
            return
        password = None
        if wallet.has_password():
            from electrum.gui.qt.password_dialog import PasswordDialog
            d = PasswordDialog(self.main_window, _("Enter wallet password to import TRX addresses"))
            password = d.run()
            if password is None:
                return
        addresses = wallet.get_addresses()
        if not addresses:
            QMessageBox.information(self.main_window, _("Import"), _("No addresses found in wallet."))
            return

        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtWidgets import QApplication
        progress = QProgressDialog(_("Importing TRX addresses..."), _("Cancel"), 0, len(addresses), self.main_window)
        progress.setWindowTitle(_("Import from wallet"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setMinimumDuration(0)
        progress.show()

        imported = 0
        errors = []

        def do_import():
            nonlocal imported, errors
            for i, btc_address in enumerate(addresses):
                if progress.wasCanceled():
                    return
                try:
                    wif = wallet.export_private_key(btc_address, password)
                    secret = _parse_key(wif)
                    trx_address, _raw = _tron_address(secret)
                    if self.plugin.add_address(self.wallet, trx_address, label=btc_address,
                                               public_hex=secret.hex()):
                        imported += 1
                except Exception as e:
                    errors.append(f"{btc_address}: {e}")
                progress.setValue(i + 1)
                QApplication.processEvents()
            return imported, errors

        def on_done(result):
            progress.close()
            if result is None:
                self.update()
                return
            imported, errors = result
            self.update()
            msg = _("Imported {} TRX address(es).").format(imported)
            if errors:
                msg += "\n\n" + _("Errors:") + "\n" + "\n".join(errors[:10])
            QMessageBox.information(self.main_window, _("Import from wallet"), msg)

        self._thread = TaskThread(self)
        self._thread.add(do_import, on_done, lambda: progress.cancel())
        progress.show()

    def remove_selected(self):
        selected = self.selected_in_column(self.Columns.ADDRESS)
        if not selected:
            return
        addresses = [self.get_role_data_from_coordinate(row, 0, role=self.ROLE_ADDRESS_STR)
                      for item in selected
                      for row in [item.row()]]
        if not self.main_window.question(
                _("Remove {} TRX address(es) from this tab?").format(len(addresses))):
            return
        for address in addresses:
            self.plugin.remove_address(self.wallet, address)
        self.update()

    def get_edit_key_from_coordinate(self, row, col):
        if col != self.Columns.LABEL:
            return None
        return self.get_role_data_from_coordinate(row, 0, role=self.ROLE_ADDRESS_STR)

    def on_edited(self, idx, edit_key, *, text):
        self.plugin.set_address_label(self.wallet, edit_key, text)
        self.update()

    def create_menu(self, position):
        selected = self.selected_in_column(self.Columns.ADDRESS)
        if not selected:
            return
        addresses = [self.get_role_data_from_coordinate(row, 0, role=self.ROLE_ADDRESS_STR)
                      for item in selected
                      for row in [item.row()]]
        if len(addresses) > 1:
            menu = QMenu()
            menu.addAction(
                _("Remover {} endereços da aba").format(len(addresses)),
                lambda: self.remove_many(addresses))
            self.open_menu(menu, position)
            return
        idx = self.indexAt(position)
        if not idx.isValid():
            return
        item = self.item_from_index(idx)
        if not item:
            return
        address = self.get_role_data_from_coordinate(idx.row(), 0, role=self.ROLE_ADDRESS_STR)
        menu = QMenu()
        self.add_copy_menu(menu, idx)
        menu.addAction(_("Ver histórico"), lambda: self.plugin.tronwif_show_history(address))
        menu.addAction(_("Ver no TronScan"), lambda: webopen(TRONSCAN_URL.format(address)))
        menu.addAction(_("Remover da aba"), lambda: (self.plugin.remove_address(self.wallet, address), self.update()))
        self.open_menu(menu, position)

    def remove_many(self, addresses):
        for address in addresses:
            self.plugin.remove_address(self.wallet, address)
        self.update()

    def _export_csv(self, addresses, title, default_name):
        from PyQt6.QtWidgets import QFileDialog
        import csv
        if not addresses:
            QMessageBox.information(self.main_window, _("Export"), _("Nenhum endereco para exportar."))
            return
        path, _filter = QFileDialog.getSaveFileName(
            self.main_window, title, default_name, "CSV (*.csv);;All (*)")
        if not path:
            return
        stored = self.plugin.get_stored_addresses(self.wallet)
        rows = []
        for address in addresses:
            info = stored.get(address, {})
            secret_hex = info.get('public_hex', '')
            wif = ''
            if secret_hex and len(secret_hex) == 64:
                try:
                    secret = bytes.fromhex(secret_hex)
                    wif = _secret_to_wif(secret)
                except Exception:
                    wif = ''
            balance = info.get('balance_sun', 0)
            rows.append([address, secret_hex, wif, info.get('label', ''),
                         f"{balance / 1e6:.6f}"])
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['endereco_trx', 'chave_privada_hex', 'wif', 'label', 'saldo_trx'])
            writer.writerows(rows)
        QMessageBox.information(self.main_window, _("Export"),
                                _("Exportado(s) {} endereco(es) em:\n{}").format(len(rows), path))

    def _export_with_balance(self):
        stored = self.plugin.get_stored_addresses(self.wallet)
        if not stored:
            QMessageBox.information(self.main_window, _("Export"), _("Nenhum endereco para exportar."))
            return
        has_balance = []
        no_cached = []
        for address, info in stored.items():
            cache = self.plugin.get_cached_balance(self.wallet, address)
            if cache is not None:
                if cache.get('balance_sun', 0) > 0:
                    has_balance.append(address)
            else:
                no_cached.append(address)
        if no_cached:
            reply = QMessageBox.question(
                self.main_window, _("Export com saldo"),
                _("{} endereco(s) sem saldo em cache. Consultar saldo na rede?").format(len(no_cached)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                for address in no_cached:
                    try:
                        data = self.plugin.fetch_balances(address)
                        balance_sun = data.get('balance_sun', 0)
                        self.plugin.save_balance(self.wallet, address, balance_sun)
                        if balance_sun > 0:
                            has_balance.append(address)
                    except Exception:
                        pass
        self._export_csv(has_balance, _("Export com saldo"), "trx_com_saldo.csv")

    def _export_with_tx(self):
        stored = self.plugin.get_stored_addresses(self.wallet)
        if not stored:
            QMessageBox.information(self.main_window, _("Export"), _("Nenhum endereco para exportar."))
            return
        addresses = list(stored.keys())
        reply = QMessageBox.question(
            self.main_window, _("Export com TX"),
            _("Consultar transacoes na rede para {} endereco(s)? Isso pode demorar.").format(len(addresses)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        from PyQt6.QtWidgets import QProgressDialog
        progress = QProgressDialog(_("Consultando transacoes..."), None, 0, len(addresses), self.main_window)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        has_tx = []
        for i, address in enumerate(addresses):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"{i + 1}/{len(addresses)} - {address[:12]}...")
            try:
                result = self.plugin.fetch_history(address)
                trx_list = result.get('trx') or []
                trc20_list = result.get('trc20') or []
                if trx_list or trc20_list:
                    has_tx.append(address)
            except Exception:
                pass
        progress.setValue(len(addresses))
        self._export_csv(has_tx, _("Export com TX"), "trx_com_tx.csv")
