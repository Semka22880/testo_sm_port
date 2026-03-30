# reports/management/commands/import_suites_csv.py
import csv
import os
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from reports.models import Build, TestRun, TestCaseRun


class Command(BaseCommand):
    help = "Импорт отчёта из Allure suites.csv"

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Путь к файлу suites.csv')
        parser.add_argument('--build', type=str, required=True, help='Номер билда, например SMK-MOB-T18-593')
        parser.add_argument('--arch', type=str, default='Astra 1.8', help='Архитектура (по умолчанию Astra 1.8)')

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options['csv_path']
        build_number = options['build']
        arch = options['arch']

        if not os.path.exists(csv_path):
            self.stdout.write(self.style.ERROR(f"Файл не найден: {csv_path}"))
            return

        # Создаём или получаем билд
        build_date = datetime.now().date()
        build, _ = Build.objects.get_or_create(
            build_number=build_number,
            defaults={'date': build_date}
        )

        # Один CSV = один TestRun (одна архитектура в одном билде)
        test_run = TestRun.objects.create(
            build=build,
            architecture=arch,
            os_version="1.8",  # можно парсить из названия, если нужно
            run_date=build_date,
            allure_report_url=f"http://10.177.140.34:8080/allure/reports/{build_number}/",  # подправь если нужно
            total_tests=0, passed_tests=0, failed_tests=0, skipped_tests=0
        )

        passed = failed = skipped = 0

        with open(csv_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = row['Status'].lower()
                test_name = row['Test Method'] or row['Name']
                full_name = row['Name']  # полное человекочитаемое название
                start_time_str = row['Start Time']

                # Парсим дату/время (пример: "Wed Nov 26 15:42:21 MSK 2025")
                try:
                    exec_date = datetime.strptime(start_time_str, "%a %b %d %H:%M:%S %Z %Y").date()
                except:
                    exec_date = build_date

                TestCaseRun.objects.create(
                    test_run=test_run,
                    test_name=test_name.strip(),
                    status=status,
                    execution_date=exec_date
                )

                if status == 'passed':
                    passed += 1
                elif status == 'failed':
                    failed += 1
                elif status == 'skipped':
                    skipped += 1

        # Обновляем счётчики в TestRun
        test_run.total_tests = passed + failed + skipped
        test_run.passed_tests = passed
        test_run.failed_tests = failed
        test_run.skipped_tests = skipped
        test_run.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Успешно импортирован билд {build_number} ({arch})\n"
                f"Всего тестов: {test_run.total_tests} | "
                f"Passed: {passed} | Failed: {failed} | Skipped: {skipped}"
            )
        )