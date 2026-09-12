"""Allowlisted workstation actions. Playwright is optional; the live UI can apply the same actions in-page.

The language model never receives a page dump, accessibility tree, or patient identifiers.
"""
from __future__ import annotations

from typing import Any

STUDIO_ACTIONS = (
    'none',
    'pause',
    'end',
    'open_patients',
    'open_settings',
    'open_home',
    'open_history',
    'confirm_tracking',
    'start_session',
)

# Playwright-style selectors used by the in-page executor (and by an optional headless runner).
ACTION_SELECTORS = {
    'open_patients': {'hash': '#/patients'},
    'open_settings': {'hash': '#/settings'},
    'open_home': {'hash': '#/app/home'},
    'open_history': {'hash': '#/app/home', 'scroll': '#dash-history'},
    'confirm_tracking': {'click': '#confirm'},
    'start_session': {'click': '#go'},
}

ACTION_PHRASES = {
    'open_patients': ('open patients', 'show patients', 'patient list', 'go to patients', 'patients page'),
    'open_settings': ('open settings', 'open setting', 'go to settings'),
    'open_home': ('open home', 'open dashboard', 'my dashboard', 'show dashboard', 'go home'),
    'open_history': ('show history', 'past sessions', 'my sessions', 'session history', 'previous sessions'),
    'confirm_tracking': ('confirm tracking', 'start tracking', 'tracking looks good', 'confirm and start'),
    'start_session': ('start session', 'start rehab', 'begin session', 'start the session', 'record session'),
}


def normalize_action(action: Any) -> str:
    name = str(action or 'none').strip()
    return name if name in STUDIO_ACTIONS else 'none'


def match_action(text: str) -> str:
    lowered = ' '.join((text or '').lower().split())
    for action, phrases in ACTION_PHRASES.items():
        if any(phrase in lowered for phrase in phrases):
            return action
    return 'none'


def playwright_status() -> dict[str, Any]:
    try:
        import playwright  # noqa: F401
        available = True
    except ImportError:
        available = False
    return {
        'available': available,
        'engine': 'playwright' if available else 'in_page',
        'mode': 'allowlisted_localhost_no_page_dump',
        'actions': [name for name in STUDIO_ACTIONS if name not in ('none', 'pause', 'end')],
        'privacy': 'no_dom_or_patient_identifiers_to_llm',
    }
