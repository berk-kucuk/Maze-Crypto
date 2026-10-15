"""Generic rule editor: pick a condition type, fill its fields, write your own note."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QWidget,
)

from mazecrypto.core.models import Coin
from mazecrypto.core.rules import CONDITIONS, Condition, Rule
from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.widgets import Dialog, label

# field -> (widget kind, extra). "coin" fields also fill the condition's symbol.
SPEC: dict[str, list[tuple[str, str, tuple]]] = {
    "price": [("coin_id", "coin", ()), ("op", "choice", ("below", "above")), ("value", "money", ()),
              ("currency", "choice", ("fiat", "usd"))],
    "cost_change": [("coin_id", "coin", ()), ("pct", "float", (-99, 1000, 1))],
    "drift": [("pct", "float", (0.5, 100, 0.5)), ("coin_id", "coin_any", ())],
    "rsi": [("coin_id", "coin", ()), ("interval", "choice", ("1h", "4h", "1d", "1w")),
            ("op", "choice", ("below", "above")), ("value", "float", (1, 99, 1)), ("period", "int", (2, 100))],
    "sma_cross": [("coin_id", "coin", ()), ("interval", "choice", ("1h", "4h", "1d", "1w")),
                  ("fast", "int", (2, 400)), ("slow", "int", (3, 500)), ("direction", "choice", ("golden", "death"))],
    "move": [("coin_id", "coin", ()), ("hours", "int", (1, 168)), ("pct", "float", (0.5, 100, 0.5)),
             ("direction", "choice", ("any", "up", "down"))],
}


def describe(c: Condition, coins: dict[str, str], fiat: str) -> str:
    """One-line human description of a condition."""
    sym = getattr(c, "symbol", "") or coins.get(getattr(c, "coin_id", ""), "")
    d = c.to_dict()
    if c.kind == "price":
        cur = "USD" if d["currency"] == "usd" else fiat
        return t(f"rule.desc.price_{d['op']}", coin=sym, value=fmt.money(d["value"], cur))
    if c.kind == "cost_change":
        return t("rule.desc.cost_drop" if d["pct"] < 0 else "rule.desc.cost_gain", coin=sym,
                 pct=fmt.num(abs(d["pct"]), 1))
    if c.kind == "drift":
        target = coins.get(d["coin_id"], d["coin_id"]) if d["coin_id"] else t("rule.any_bucket")
        return t("rule.desc.drift", what=target, pct=fmt.num(d["pct"], 1))
    if c.kind == "rsi":
        return t(f"rule.desc.rsi_{d['op']}", coin=sym, iv=d["interval"].upper(), value=fmt.num(d["value"], 0),
                 period=d["period"])
    if c.kind == "sma_cross":
        return t(f"rule.desc.sma_{d['direction']}", coin=sym, iv=d["interval"].upper(), fast=d["fast"],
                 slow=d["slow"])
    if c.kind == "move":
        return t(f"rule.desc.move_{d['direction']}", coin=sym, pct=fmt.num(d["pct"], 1), hours=d["hours"])
    return c.kind


class RuleDialog(Dialog):
    def __init__(self, ctl: Controller, parent: QWidget | None, rule: Rule | None):
        super().__init__(parent, t("rule.edit") if rule else t("rule.new"))
        self.ctl = ctl
        self.rule = rule
        self.setMinimumWidth(540)
        self.coins: list[Coin] = ctl.tracked_coins()

        top = QFormLayout()
        top.setSpacing(10)
        self.name = QLineEdit(rule.name if rule else "")
        self.kind = QComboBox()
        # Basic mode offers the three conditions anyone understands at a glance.
        kinds = list(SPEC) if ctl.expert else ["price", "cost_change", "move"]
        if rule and rule.condition.kind not in kinds:
            kinds.append(rule.condition.kind)
        for kind in kinds:
            self.kind.addItem(t(f"rule.kind.{kind}"), kind)
        top.addRow(label(t("rule.name"), "CardKey"), self.name)
        top.addRow(label(t("rule.type"), "CardKey"), self.kind)
        self.content.addLayout(top)
        self.kind_help = label("", "Small", wrap=True)
        self.content.addWidget(self.kind_help)

        self.stack = QStackedWidget()
        self.fields: dict[str, dict[str, QWidget]] = {}
        for kind, spec in SPEC.items():
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(0, 6, 0, 6)
            form.setSpacing(10)
            self.fields[kind] = {}
            for name, wkind, extra in spec:
                w = self._widget(wkind, extra)
                self.fields[kind][name] = w
                form.addRow(label(t(f"rule.f.{name}"), "CardKey"), w)
            self.stack.addWidget(page)
        self.content.addWidget(self.stack)

        bottom = QFormLayout()
        bottom.setSpacing(10)
        self.message = QLineEdit(rule.message if rule else "")
        self.message.setPlaceholderText(t("rule.message_ph"))
        self.cooldown = QDoubleSpinBox()
        self.cooldown.setRange(0.25, 720)
        self.cooldown.setSuffix(" h")
        self.cooldown.setValue(rule.cooldown_hours if rule else 24)
        self.enabled = QCheckBox(t("rule.enabled"))
        self.enabled.setChecked(rule.enabled if rule else True)
        bottom.addRow(label(t("rule.message"), "CardKey"), self.message)
        bottom.addRow(label(t("rule.cooldown"), "CardKey"), self.cooldown)
        bottom.addRow("", self.enabled)
        self.content.addLayout(bottom)
        self.content.addWidget(label(t("rule.edge_note"), "Small", wrap=True))

        self.kind.currentIndexChanged.connect(self._kind_changed)
        if rule:
            self.kind.setCurrentIndex(self.kind.findData(rule.condition.kind))
            self._load(rule.condition)
        self._kind_changed(self.kind.currentIndex())

    def _widget(self, wkind: str, extra: tuple) -> QWidget:
        if wkind in ("coin", "coin_any"):
            cb = QComboBox()
            if wkind == "coin_any":
                cb.addItem(t("rule.any_bucket"), "")
            for c in self.coins:
                cb.addItem(f"{c.symbol}  ·  {c.name}", c.id)
            return cb
        if wkind == "choice":
            cb = QComboBox()
            for opt in extra:
                cb.addItem(t(f"rule.opt.{opt}"), opt)
            return cb
        if wkind == "int":
            sp = QSpinBox()
            sp.setRange(*extra)
            return sp
        if wkind == "float":
            sp = QDoubleSpinBox()
            sp.setRange(extra[0], extra[1])
            sp.setSingleStep(extra[2])
            sp.setDecimals(2)
            return sp
        return QLineEdit()          # money: free text, parsed with fmt.parse_number

    def _kind_changed(self, idx: int) -> None:
        kind = self.kind.itemData(idx)
        self.stack.setCurrentIndex(list(SPEC).index(kind))
        self.kind_help.setText(t(f"rule.help.{kind}"))
        if not self.rule:
            # Show the defaults of the condition class.
            self._load(CONDITIONS[kind](), keep_coin=True)

    def _load(self, c: Condition, keep_coin: bool = False) -> None:
        for name, w in self.fields[c.kind].items():
            val = getattr(c, name)
            if isinstance(w, QComboBox):
                if keep_coin and name == "coin_id" and not val:
                    continue
                i = w.findData(val)
                if i >= 0:
                    w.setCurrentIndex(i)
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(val)
            elif isinstance(w, QLineEdit):
                w.setText(fmt.num(val) if val else "")

    def validate(self) -> bool:
        kind = self.kind.currentData()
        kw: dict = {}
        try:
            for name, w in self.fields[kind].items():
                if isinstance(w, QComboBox):
                    kw[name] = w.currentData()
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    kw[name] = w.value()
                else:
                    kw[name] = fmt.parse_number(w.text())
        except ValueError:
            self.show_error(t("trade.err_number"))
            return False
        cls = CONDITIONS[kind]
        if "symbol" in cls.__dataclass_fields__:
            cid = kw.get("coin_id")
            if not cid:
                self.show_error(t("trade.err_coin"))
                return False
            kw["symbol"] = next((c.symbol for c in self.coins if c.id == cid), cid.upper())
        if kind == "sma_cross" and kw["fast"] >= kw["slow"]:
            self.show_error(t("rule.err_sma"))
            return False
        if kind == "cost_change" and kw["pct"] == 0:
            self.show_error(t("rule.err_zero"))
            return False
        cond = cls(**kw)
        coins = {c.id: c.symbol for c in self.coins}
        name = self.name.text().strip() or describe(cond, coins, self.ctl.settings.fiat)
        if self.rule:
            self.rule.name, self.rule.condition = name[:80], cond
            self.rule.message, self.rule.cooldown_hours = self.message.text().strip()[:200], self.cooldown.value()
            self.rule.enabled = self.enabled.isChecked()
        else:
            self.rule = Rule(name[:80], cond, self.message.text().strip()[:200], self.enabled.isChecked(),
                             self.cooldown.value())
        return True
