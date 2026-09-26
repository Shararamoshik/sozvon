"""Windows-проверка без оборудования/платной сети и без браузерных fixtures."""
import json
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
folder = ROOT / 'artifacts'
folder.mkdir(exist_ok=True)
run_id = str(time.time_ns())
report = folder / ('windows-tests-' + run_id + '.json')
basetemp = folder / ('windows-pytest-' + run_id)


class Results:
    def __init__(self):
        self.passed = 0
        self.skipped = 0
        self.failed = []

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.append({'test': report.nodeid, 'when': report.when,
                                'message': str(report.longrepr)})
        elif report.skipped:
            self.skipped += 1
        elif report.when == 'call' and report.passed:
            self.passed += 1


results = Results()
code = pytest.main(['tests', '--ignore-glob=tests/test_ui*.py',
                    '--ignore=tests/test_features_browser.py', '--basetemp=' + str(basetemp), '-q', '--tb=short'], plugins=[results])
report.write_text(json.dumps({'code': int(code), 'passed': results.passed,
                             'skipped': results.skipped, 'failures': results.failed,
                             'excluded': ['tests/test_ui*.py', 'tests/test_features_browser.py']},
                            ensure_ascii=False, indent=2), encoding='utf-8')
print('WINDOWS_TEST_REPORT=' + str(report))
raise SystemExit(code)
