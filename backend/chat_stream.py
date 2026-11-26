"""Ephemeral events for one chat request; never written to the session store."""
from contextvars import ContextVar

chat_events = ContextVar('chat_events', default=None)


def emit(event, **data):
    callback = chat_events.get()
    if callback:
        callback({'type': event, **data})
