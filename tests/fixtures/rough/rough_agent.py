import os
import sys
import time

from agents.basic_agent import BasicAgent


def _meta(name):
    return {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}


class Crash(BasicAgent):
    def __init__(self):
        super().__init__(name="Crash", metadata=_meta("Crash"))

    def perform(self, **kwargs):
        os._exit(9)


class Hang(BasicAgent):
    def __init__(self):
        super().__init__(name="Hang", metadata=_meta("Hang"))

    def perform(self, **kwargs):
        time.sleep(120)
        return "too late"


class Chatty(BasicAgent):
    def __init__(self):
        super().__init__(name="Chatty", metadata=_meta("Chatty"))

    def perform(self, **kwargs):
        print('{"jsonrpc": "2.0", "method": "noise"}')
        os.write(1, b"raw noise on fd 1\n")
        sys.stdout.flush()
        return "clean answer"


class Fine(BasicAgent):
    def __init__(self):
        super().__init__(name="Fine", metadata=_meta("Fine"))

    def perform(self, **kwargs):
        return "still here"
