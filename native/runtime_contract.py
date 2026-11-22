"""Minimal installed Python IPC adapter. Parsing, HTTP, ZIP, and catalog logic is native."""
from urllib.parse import urlencode

DS = 'https://doujinstyle.com/'
UA = 'DoujinCatalogPrototype/0.1 (interactive catalog lookup)'
HTML_ACCEPT = 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.1'

# An installed worker must not implement an alternate network stack.
class Http:
    pass

class NavigationRedirect:
    pass


def ds_url(**params):
    return DS + '?' + urlencode(params)


def navigation_referrer(*_args):
    raise OSError('Python HTTP referrer handling is source-only; use the native transport')


def catalog_hint(*_args, **_kwargs):
    raise OSError('Python filename catalog parsing retired; native offer resolver owns it')

# A policy stub, not a second provider implementation: any fallback to it must fail closed.
ARCHIVE_PROVIDERS = {}

def can_inspect(*_args):
    raise OSError('Python archive-provider classification retired; use native capability verification')
