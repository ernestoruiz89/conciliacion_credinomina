"""App registration and native v16 navigation keep existing access boundaries."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from credinomina_reconciliation import desktop, hooks


ROOT = Path(__file__).resolve().parents[1] / 'credinomina_reconciliation'


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


@pytest.mark.parametrize('user,kind,roles,can_read,expected', [
    ('Guest', 'Website User', [], True, False),
    ('Administrator', 'System User', [], True, True),
    ('operator', 'System User', ['Operador Credinomina'], True, True),
    ('supervisor', 'System User', ['Supervisor Credinomina'], True, True),
    ('manager', 'System User', ['System Manager'], True, True),
    ('operator', 'System User', ['Operador Credinomina'], False, False),
    ('unrelated', 'System User', ['Desk User'], True, False),
    ('portal', 'Website User', ['Operador Credinomina'], True, False),
])
def test_app_tile_uses_operational_roles_and_read_permission(user, kind, roles, can_read, expected):
    with patch.object(desktop.frappe, 'session', {'user': user}):
        # Frappe's session is attribute-addressable.
        desktop.frappe.session = desktop.frappe._dict(user=user)
        with patch.object(desktop.frappe, 'get_cached_value', return_value=kind), patch.object(
                desktop.frappe, 'get_roles', return_value=roles), patch.object(
                desktop.frappe, 'has_permission', return_value=can_read):
            assert desktop.check_app_permission() is expected


def test_apps_screen_and_desktop_grid_share_one_owned_route_and_logo():
    entry = hooks.add_to_apps_screen[0]
    icon = read('desktop_icon/conciliación_credinómina.json')
    assert entry['name'] == hooks.app_name == icon['app']
    assert entry['route'] == hooks.app_home == icon['link']
    assert entry['logo'] == hooks.app_logo_url == icon['logo_url']
    assert (ROOT / 'public/images/credinomina-logo.svg').is_file()
    assert entry['has_permission'] == 'credinomina_reconciliation.desktop.check_app_permission'
    assert hooks.required_apps == []
    assert icon['icon_type'] == 'App' and not icon['hidden']


def test_v16_sidebars_have_same_workflow_and_valid_owned_destinations():
    modern = read('conciliacion_credinomina/sidebar/conciliacion_credinomina/conciliacion_credinomina.json')
    legacy = read('workspace_sidebar/conciliacion_credinomina.json')
    assert modern['doctype'] == 'Sidebar'
    assert legacy['doctype'] == 'Workspace Sidebar'
    assert modern['items'] == legacy['items']
    assert modern['module'] == modern['title'] == modern['name'] == 'Conciliacion Credinomina'
    assert modern['app'] == legacy['app'] == hooks.app_name
    definitions = {kind: {json.loads(file.read_text(encoding='utf-8'))['name']
                         for file in (ROOT / 'conciliacion_credinomina' / folder).rglob('*.json')}
                   for kind, folder in [('DocType', 'doctype'), ('Page', 'page'), ('Report', 'report'), ('Workspace', 'workspace')]}
    sections = [item['label'] for item in modern['items'] if item['type'] == 'Section Break']
    assert sections == ['Conciliación', 'Seguimiento', 'Reportes', 'Catálogos', 'Administración de cargas']
    has_section = False
    destinations = []
    for item in modern['items']:
        assert item['icon']
        if item['type'] == 'Section Break':
            has_section = True
            assert item['collapsible']
            assert item['keep_closed'] == (item['label'] not in {'Conciliación', 'Seguimiento'})
            assert not item['indent']  # Native rendering hides child icons under indented sections.
        else:
            assert not item['child'] or has_section
            assert item['link_to'] in definitions[item['link_type']]
            destinations.append((item['link_type'], item['link_to']))
    assert len(destinations) == len(set(destinations))
    assert destinations[:2] == [('Workspace', 'Conciliacion Credinomina'), ('Page', 'control-credinomina')]
    assert destinations[-2:] == [('DocType', 'CN Accounting Batch'), ('DocType', 'CN Accounting Batch Block')]


def test_native_dock_opens_the_module_sidebar_with_its_own_icon():
    dock = read('dock/credinomina_reconciliation/credinomina_reconciliation.json')
    assert dock['app'] == hooks.app_name and dock['standard'] == 1
    assert len(dock['items']) == 1
    entry = dock['items'][0]
    assert (entry['link_type'], entry['link_to']) == ('Sidebar', 'Conciliacion Credinomina')
    assert entry['icon'] and entry['title'] == 'Credinómina' and not entry['hidden']


def test_navigation_records_survive_standard_orphan_cleanup():
    import frappe
    # The original v16.5 and current v16 use the same app-level path contract.
    for doctype, relative in [('Desktop Icon', 'desktop_icon/conciliación_credinómina.json'),
                              ('Workspace Sidebar', 'workspace_sidebar/conciliacion_credinomina.json')]:
        record = read(relative)
        assert (ROOT / frappe.scrub(doctype) / (frappe.scrub(record['name']) + '.json')).is_file()
