"""Read-only discovery of public pipelines published in the Nextflow Registry.

Uses registry's public, paginated HTML listing rather than relying on an
undocumented JSON API. The parser is intentionally small and testable.
"""
from __future__ import annotations

import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

REGISTRY = 'https://registry.nextflow.io'
MAX_PAGES = 50
CACHE_SECONDS = 1800
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}/[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$')
_cache: dict = {'at': 0.0, 'items': []}
_lock = threading.Lock()


class RegistryTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.in_row = False
        self.in_cell = False
        self.cell_level = 0
        self.cells = []
        self.cell_text = ''
        self.row_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.in_row = True
            self.cells = []
            self.row_link = None
        elif tag == 'td' and self.in_row:
            self.in_cell = True
            self.cell_text = ''
        elif tag == 'a' and self.in_cell:
            href = attrs.get('href', '')
            if '/pipelines/' in href and not self.row_link:
                self.row_link = href

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text += data

    def handle_endtag(self, tag):
        if tag == 'td' and self.in_cell:
            self.cells.append(' '.join(self.cell_text.split()))
            self.in_cell = False
        elif tag == 'tr' and self.in_row:
            if self.row_link and self.cells:
                self.rows.append((self.row_link, self.cells))
            self.in_row = False


def parse_registry_page(html: str) -> list[dict]:
    parser = RegistryTableParser()
    parser.feed(html)
    items = []
    for href, cols in parser.rows:
        # Example href: /pipelines/nf-core/rnaseq@3.27.0
        path = unquote(urlparse(href).path).split('/pipelines/', 1)[-1]
        slug, _, version = path.partition('@')
        if not IDENT.fullmatch(slug):
            continue
        name = slug.split('/')[-1]
        items.append({'id': slug, 'name': name, 'version': version or '',
                      'keywords': cols[1] if len(cols) > 1 else '',
                      'description': cols[2] if len(cols) > 2 else '',
                      'url': REGISTRY + '/pipelines/' + slug,
                      'repository': 'https://github.com/' + slug})
    return items


def list_registry_pipelines(*, force=False, search='') -> dict:
    with _lock:
        if force or not _cache['items'] or time.monotonic() - _cache['at'] > CACHE_SECONDS:
            listing = {}
            # The public listing has 20 items per page, and its server handles page numbers.
            for page in range(1, MAX_PAGES + 1):
                url = f'{REGISTRY}/pipelines?page={page}&size=20'
                req = Request(url, headers={'User-Agent': 'BioCompute/0.2 (pipeline-catalog)', 'Accept': 'text/html'})
                with urlopen(req, timeout=12) as response:
                    page_html = response.read(2_000_001)
                if len(page_html) > 2_000_000:
                    raise ValueError('Registry page exceeds maximum size')
                page_items = parse_registry_page(page_html.decode('utf-8', errors='replace'))
                if not page_items:
                    if page == 1:
                        raise ValueError('Nextflow Registry listing format not recognized')
                    break
                before = len(listing)
                for item in page_items:
                    listing[item['id']] = item
                if len(listing) == before or len(page_items) < 20:
                    break
            _cache.update({'items': sorted(listing.values(), key=lambda item: item['id']), 'at': time.monotonic()})
        result = _cache['items']
        if search:
            query = search.lower().strip()
            result = [i for i in result if query in f"{i['id']} {i['keywords']} {i['description']}".lower()]
        return {'pipelines': result, 'total': len(_cache['items']), 'filtered': len(result),
                'source': REGISTRY + '/pipelines', 'cached': not force}
