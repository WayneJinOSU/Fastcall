import asyncio
from dataclasses import dataclass


class SessionSlotError(RuntimeError):
    pass


@dataclass
class SessionSlot:
    call_id: str
    generation: int
    active: bool = True


class SessionSlotRegistry:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._slots = {}
        self._highest_generation = {}

    async def attach(self, call_id, generation):
        async with self._lock:
            highest = self._highest_generation.get(call_id, 0)
            slot = self._slots.get(call_id)
            if generation <= highest or generation < 1 or (slot and slot.active):
                raise SessionSlotError("Session generation is stale, consumed, or still active")
            slot = SessionSlot(call_id, generation)
            self._slots[call_id] = slot
            self._highest_generation[call_id] = generation
            return slot

    async def release(self, slot):
        async with self._lock:
            current = self._slots.get(slot.call_id)
            if current is slot:
                slot.active = False
                del self._slots[slot.call_id]

    async def has_slot(self, call_id):
        async with self._lock:
            return call_id in self._slots
