"""Desktop notifications over org.freedesktop.Notifications (KDE, GNOME, ...)."""
from __future__ import annotations

from PyQt6.QtCore import QMetaType, QVariant
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusInterface

from mazecrypto import APP_ID, APP_NAME


class Notifier:
    def __init__(self) -> None:
        bus = QDBusConnection.sessionBus()
        self._iface = QDBusInterface("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                     "org.freedesktop.Notifications", bus) if bus.isConnected() else None

    @property
    def available(self) -> bool:
        return self._iface is not None and self._iface.isValid()

    def send(self, title: str, body: str) -> bool:
        if not self.available:
            return False
        # D-Bus is strictly typed: replaces_id is uint32 and actions is as.
        replaces = QDBusArgument()
        replaces.add(0, QMetaType.Type.UInt.value)
        actions = QDBusArgument()
        actions.add([], QMetaType.Type.QStringList.value)
        msg = self._iface.call("Notify", APP_NAME, replaces, APP_ID, title, body, actions,
                               {"desktop-entry": QVariant(APP_ID)}, -1)
        return msg.errorName() == ""
