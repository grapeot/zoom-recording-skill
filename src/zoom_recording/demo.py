"""`--demo`: show what a meeting command is about to touch or has just read.

With demo on, each step draws a temporary outline around the element on the real page with a
caption of what the tool reads there (for example `checkbox · row "Waiting Room" · unchecked`),
narrates the same line on stderr, and pauses briefly so a viewer can follow. The outline is a plain
DOM element with `pointer-events: none`; it never receives clicks and is removed at the end.
Without demo nothing is drawn, nothing pauses, and only short progress lines go to stderr.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

SLOW_MO_MS = 350
HOLD_MS = 650

DRAW_JS = """(el, caption) => {
  const r = el.getBoundingClientRect();
  const make = (id) => {
    let node = document.getElementById(id);
    if (!node) { node = document.createElement('div'); node.id = id; document.body.appendChild(node); }
    return node;
  };
  const box = make('zoom-cli-demo-box');
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:3px solid #e8590c;' +
    'border-radius:6px;box-shadow:0 0 0 5px rgba(232,89,12,.22);transition:all .12s ease-out;' +
    `left:${r.left - 5}px;top:${r.top - 5}px;width:${r.width + 10}px;height:${r.height + 10}px`;
  const tag = make('zoom-cli-demo-tag');
  tag.textContent = caption;
  const below = r.bottom + 40 < window.innerHeight;
  tag.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;max-width:560px;' +
    'background:#1f2933;color:#fff;font:600 13px/1.35 -apple-system,BlinkMacSystemFont,sans-serif;' +
    'padding:5px 9px;border-radius:5px;box-shadow:0 2px 8px rgba(0,0,0,.25);white-space:nowrap;' +
    'overflow:hidden;text-overflow:ellipsis;' +
    `left:${Math.max(4, r.left - 5)}px;top:${below ? r.bottom + 9 : Math.max(4, r.top - 34)}px`;
}"""

CLEAR_JS = """() => {
  for (const id of ['zoom-cli-demo-box', 'zoom-cli-demo-tag']) {
    const node = document.getElementById(id);
    if (node) node.remove();
  }
}"""


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Demo:
    """Narration and on-page outlines. `enabled=False` makes every call a quiet no-op."""

    def __init__(self, enabled: bool, log: Callable[[str], None] = _stderr):
        self.enabled = enabled
        self.log = log

    @property
    def slow_mo(self) -> int:
        return SLOW_MO_MS if self.enabled else 0

    def say(self, line: str) -> None:
        self.log(("demo: " if self.enabled else "") + line)

    def show(self, locator: Any, caption: str, page: Any = None) -> None:
        """Outline `locator` (one element) with `caption`; narrate it. No-op without demo."""
        if not self.enabled:
            return
        self.log("demo: " + caption)
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            locator.evaluate(DRAW_JS, caption)
            if page is not None:
                page.wait_for_timeout(HOLD_MS)
        except Exception:  # noqa: BLE001 - the overlay is decoration; never fail a command over it
            pass

    def clear(self, page: Any) -> None:
        if not self.enabled:
            return
        try:
            page.evaluate(CLEAR_JS)
        except Exception:  # noqa: BLE001
            pass
