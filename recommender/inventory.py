"""Rebuild what a player is holding by replaying their timeline item events

Match-V5 timelines record item events, so inventory at time t has to be reconstructed
Rules:
  - ITEM_PURCHASED: item added. Buying a recipe first emits ITEM_DESTROYED for each
    component, at the same millisecond, before the purchase
  - ITEM_DESTROYED: item removed (components consumed into a recipe, potions drunk...)
  - ITEM_SOLD: item removed
  - ITEM_UNDO (beforeId=X, afterId=0): undo buying X; the components destroyed at
    X's purchase time come back
  - ITEM_UNDO (beforeId=0, afterId=X): undo selling X; X comes back
  - ITEM_UNDO (0, 0): Didn't record which item; it undoes the most recent purchase
  - Automatic upgrades (tier-3 boots from the mid lane quest, stacking transforms like
    Manamune -> Muramana) emit only ITEM_DESTROYED for the base item, so a destroyed base
    item becomes its upgrade (`upgrades` mapping)
  - If the upgrade is already earned when the base item is bought (mid laner buys tier-2
    boots after finishing the quest), the DESTROYED comes before the PURCHASED, at the
    same millisecond. That purchase is then already accounted for by the upgrade
"""
from collections import defaultdict


class Inventory:
    def __init__(self, upgrades: dict[int, int] | None = None):
        self.upgrades = upgrades or {}  # item destroyed -> item it silently turns into
        self.items: list[int] = []
        self._purchases: list[tuple[int, int, int]] = [] # (timestamp, item bought, item held as a result (upgraded)))
        self._destroyed_at: dict[int, list[int]] = defaultdict(list)  # timestamp -> items destroyed then
        self._upgraded_early: set[tuple[int, int]] = set()  # (timestamp, base item) upgraded before its purchase
        self.anomalies = 0  # events that didn't fit the rules

    def _remove(self, item: int) -> None:
        try:
            self.items.remove(item)
        except ValueError:
            self.anomalies += 1

    def apply(self, event_type: str, timestamp: int, item_id: int = 0, before_id: int = 0, after_id: int = 0) -> None:
        if event_type == "ITEM_PURCHASED":
            if (timestamp, item_id) in self._upgraded_early:
                self._upgraded_early.discard((timestamp, item_id))
                self._purchases.append((timestamp, item_id, self.upgrades[item_id]))
                return
            self.items.append(item_id)
            self._purchases.append((timestamp, item_id, item_id))
        elif event_type == "ITEM_DESTROYED":
            if item_id in self.upgrades:
                if item_id in self.items:
                    self.items.remove(item_id)
                else:
                    self._upgraded_early.add((timestamp, item_id))
                self.items.append(self.upgrades[item_id])
            else:
                self._remove(item_id)
                self._destroyed_at[timestamp].append(item_id)
        elif event_type == "ITEM_SOLD":
            self._remove(item_id)
        elif event_type == "ITEM_UNDO":
            if after_id and not before_id:
                self.items.append(after_id)  # undo a sale
            else:
                self._undo_purchase(before_id)

    @property
    def purchases(self) -> list[tuple[int, int]]:
        """(timestamp, item bought) in order"""
        return [(timestamp, bought) for timestamp, bought, _ in self._purchases]

    def _undo_purchase(self, item_id: int) -> None:
        for i in range(len(self._purchases) - 1, -1, -1):
            if item_id == 0 or self._purchases[i][1] == item_id:
                timestamp, _, held = self._purchases.pop(i)
                self._remove(held)
                self.items.extend(self._destroyed_at.pop(timestamp, []))
                return
        self.anomalies += 1
