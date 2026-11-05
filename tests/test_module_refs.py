"""Every `module.name` used in the app and the host jobs must exist.

The host jobs in tools/ run only at night, so a removed import they relied on (bud_store.open_book,
2026-11-05) breaks nothing in the web app and fails silently until 02:30.
"""
import ast
import importlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_module_attributes_exist():
    bad = []
    for f in sorted(ROOT.glob('*.py')) + sorted((ROOT / 'tools').glob('*.py')):
        tree = ast.parse(f.read_text())
        alias = {a.asname or a.name: a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias:
                mod = alias[n.value.id]
                if not (ROOT / (mod.replace('.', '/') + '.py')).exists():
                    continue        # third-party or standard library
                if not hasattr(importlib.import_module(mod), n.attr):
                    bad.append(f'{f.name}:{n.lineno} {n.value.id}.{n.attr}')
    assert not bad, bad
