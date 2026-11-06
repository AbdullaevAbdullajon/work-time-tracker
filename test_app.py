from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from app import create_app
from tracker import REPORT_ZONE


class AppTest(TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.db = Path(folder) / 'test.sqlite3'
        self.now = int(datetime(2026, 11, 6, 12, tzinfo=REPORT_ZONE).timestamp())
        self.restart()

    def restart(self):
        self.app = validator(create_app(self.db, lambda: self.now))

    def request(self, path='/', data=None, query=''):
        environ = {}
        setup_testing_defaults(environ)
        environ['PATH_INFO'] = path
        environ['QUERY_STRING'] = query
        if data is not None:
            body = urlencode(data).encode()
            environ.update(REQUEST_METHOD='POST', CONTENT_LENGTH=str(len(body)), **{'wsgi.input': BytesIO(body)})
        response = []

        def start_response(status, headers, exc_info=None):
            response.extend([int(status.split()[0]), dict(headers)])

        result = self.app(environ, start_response)
        try:
            html = b''.join(result).decode()
        finally:
            result.close()
        status, headers = response
        return status, headers, html

    def employee(self, name='Анна Ветрова'):
        status, headers, _ = self.request('/employees', {'name': name})
        self.assertEqual(status, 303)
        return headers['Location']

    def start(self, task='Тестовый отчёт', employee=1):
        return self.request('/start', {'employee': employee, 'task': task})

    def stop(self, timer=1, employee=1):
        return self.request('/stop', {'employee': employee, 'timer': timer})

    def test_end_to_end_and_restart(self):
        self.employee()
        self.assertEqual(self.start()[0], 303)
        self.now += 90
        self.restart()
        self.assertIn('00:01:30', self.request()[2])
        self.assertEqual(self.stop()[0], 303)
        self.restart()
        html = self.request()[2]
        self.assertIn('Анна Ветрова', html)
        self.assertIn('Тестовый отчёт', html)
        self.assertIn('class="total">00:01:30', html)
        self.assertNotIn('data-elapsed', html)

    def test_duplicate_start_then_new_timer(self):
        self.employee()
        self.assertEqual(self.start('Первая задача')[0], 303)
        status, _, html = self.start('Дубликат')
        self.assertEqual(status, 400)
        self.assertIn('уже запущен таймер', html)
        self.assertNotIn('Дубликат', html)
        self.assertEqual(self.stop()[0], 303)
        self.assertEqual(self.start('Вторая задача')[0], 303)
        self.assertEqual(self.stop()[0], 400)
        self.assertIn('Вторая задача', self.request()[2])
        self.assertEqual(self.stop(timer=2)[0], 303)

    def test_empty_report(self):
        self.assertIn('Пока нет сотрудников', self.request()[2])
        self.employee()
        self.start()
        self.now += 10
        self.stop()
        status, _, html = self.request(query='from=2026-11-01&to=2026-11-01')
        self.assertEqual(status, 200)
        self.assertIn('За выбранный период записей нет', html)
        self.assertIn('class="total">00:00:00', html)
        self.assertNotIn('Тестовый отчёт', html)

    def test_period_clips_at_midnight(self):
        self.employee()
        midnight = int(datetime(2026, 11, 7, tzinfo=REPORT_ZONE).timestamp())
        self.now = midnight - 60
        self.start()
        self.now = midnight + 120
        self.stop()
        for day, expected in [(6, '00:01:00'), (7, '00:02:00')]:
            with self.subTest(day=day):
                value = f'2026-11-{day:02}'
                html = self.request(query=urlencode({'from': value, 'to': value}))[2]
                self.assertIn(f'class="total">{expected}', html)

    def test_immediate_stop_at_period_start_is_visible(self):
        self.employee()
        self.now = int(datetime(2026, 11, 7, tzinfo=REPORT_ZONE).timestamp())
        self.start('Задача в полночь')
        self.stop()
        html = self.request(query='from=2026-11-07&to=2026-11-07')[2]
        self.assertIn('Задача в полночь', html)
        self.assertIn('class="total">00:00:00', html)

    def test_employee_totals_are_separate(self):
        self.employee()
        self.employee('Борис Леснов')
        self.start('Задача Анны')
        self.start('Задача Бориса', employee=2)
        self.now += 11
        self.stop()
        self.now += 22
        self.stop(timer=2, employee=2)
        html = self.request()[2]
        self.assertIn('Анна Ветрова <span class="total">00:00:11', html)
        self.assertIn('Борис Леснов <span class="total">00:00:33', html)

    def test_validation_and_html_escaping(self):
        self.assertEqual(self.request('/employees', {'name': '  '})[0], 400)
        self.assertEqual(self.start(employee=999)[0], 400)
        self.employee('<b>Вера</b>')
        self.assertEqual(self.start('  ')[0], 400)
        status, _, invalid_report = self.request(query='from=2026-11-07&to=2026-11-06')
        self.assertEqual(status, 400)
        self.assertIn('Укажите корректный период', invalid_report)
        self.assertNotIn('За выбранный период записей нет', invalid_report)
        html = self.request()[2]
        self.assertIn('&lt;b&gt;Вера&lt;/b&gt;', html)
        self.assertNotIn('<b>Вера</b>', html)


if __name__ == '__main__':
    main()
