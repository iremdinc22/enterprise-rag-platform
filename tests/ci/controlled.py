"""Run the unchanged 18-scenario suite; emit only an allowlisted result summary."""
import contextlib
import io
import json
import re
import unittest


class SanitizedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def record(self, test, status, category=None):
        name = test.id().split('.')[-1]
        if re.fullmatch(r'test_[A-Za-z0-9_]+', name) is None:
            name = 'setup-or-infrastructure'
        self.cases.append({'name': name, 'status': status,
                           **({'category': category} if category else {})})

    def addSuccess(self, test):
        super().addSuccess(test); self.record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err); self.record(test, 'failed', type(err[1]).__name__)

    def addError(self, test, err):
        super().addError(test, err); self.record(test, 'error', type(err[1]).__name__)

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self.record(test, 'skipped')


def main():
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        from tests.e2e.bootstrap import install
        install()
        suite = unittest.defaultTestLoader.loadTestsFromName('tests.e2e.suite')
        result = unittest.TextTestRunner(stream=captured, verbosity=0, resultclass=SanitizedResult).run(suite)
    summary = {'suite': 'controlled-provider-docker', 'cases': result.cases, 'run': result.testsRun,
               'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'passed': result.testsRun - len(result.failures)
               - len(result.errors) - len(result.skipped)}
    print('ATLAS_TEST_RESULT=' + json.dumps(summary))
    return 0 if result.wasSuccessful() and result.testsRun == 18 else 1


if __name__ == '__main__':
    raise SystemExit(main())
