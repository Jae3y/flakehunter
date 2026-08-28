"""Test that never terminates. Proves the timeout kills it rather than blocking."""

import threading
import time


def test_hangs_forever() -> None:
    # A background thread as well as the main sleep, so that killing only the
    # direct child would leave something behind. The process-group kill is
    # what makes this terminate cleanly.
    threading.Thread(target=time.sleep, args=(600,), daemon=False).start()
    time.sleep(600)
