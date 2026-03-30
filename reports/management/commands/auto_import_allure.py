import csv
import io
import requests
import paramiko
import json
from datetime import datetime
from dateutil import parser as dateutil_parser
from django.core.management.base import BaseCommand
from django.db import transaction
from reports.models import Build, TestRun, TestCaseRun
from django.utils import timezone
from reports.config import SSH_CONFIG


BASE_URL = "http://10.177.140.34:8080"
API_REPORT_URL = f"{BASE_URL}/api/report"


def parse_test_case_json(test_case, json_data):
    """Парсит шаги + правильно помечает FAILED на шаге падения и предыдущем шаге + читает содержимое лога"""
    from reports.models import TestStepLog
    from datetime import datetime
    from django.utils import timezone
    import requests

    test_case.steps.all().delete()

    test_stage = json_data.get('testStage', {})
    steps = test_stage.get('steps', [])

    # Находим индекс первого failed шага
    failed_index = -1
    for i, step in enumerate(steps):
        if step.get('status') == 'failed':
            failed_index = i
            break

    base_url = f"http://10.177.140.34:8080/allure/reports/{test_case.test_run.build.uuid}/data/attachments/"

    for i, step in enumerate(steps):
        step_name = step.get('name', 'Без названия шага').strip()
        original_status = step.get('status', 'unknown')
        time_info = step.get('time', {})

        start = time_info.get('start')
        stop = time_info.get('stop')
        duration = time_info.get('duration')

        start_time = datetime.fromtimestamp(start / 1000, tz=timezone.get_current_timezone()) if start else None
        stop_time = datetime.fromtimestamp(stop / 1000, tz=timezone.get_current_timezone()) if stop else None

        screenshot = None
        log_file = None
        log_content = ""

        # Обработка attachments
        for att in step.get('attachments', []):
            name = att.get('name', '')
            source = att.get('source', '')
            att_type = att.get('type', '')

            if att_type == 'image/png' or name.lower().endswith(('.png', '.jpg', '.jpeg')):
                screenshot = source

            elif att_type == 'text/plain' or name.lower().endswith('.txt'):
                log_file = source
                # Загружаем содержимое txt-файла с правильной кодировкой
                if log_file:
                    try:
                        txt_url = base_url + log_file
                        r = requests.get(txt_url, timeout=15)
                        if r.status_code == 200:
                            # Важно: указываем utf-8
                            log_content = r.text.encode('latin1').decode('utf-8')
                        else:
                            log_content = "Не удалось загрузить лог-файл"
                    except Exception as e:
                        log_content = f"Ошибка чтения лога: {e}"

        # === Логика статуса ===
        final_status = original_status

        # 1. Шаг "Saved screenshot" — всегда FAILED
        if "Saved screenshot" in step_name or "screenshot" in step_name.lower():
            final_status = "failed"

        # 2. Предыдущий шаг перед падением — тоже FAILED
        if failed_index != -1 and i == failed_index - 1:
            final_status = "failed"

        # 3. Если сам шаг уже failed — оставляем
        if original_status == "failed":
            final_status = "failed"

        TestStepLog.objects.create(
            test_case=test_case,
            step_name=step_name,
            status=final_status,
            start_time=start_time,
            stop_time=stop_time,
            duration=duration,
            order=i,
            screenshot=screenshot,
            log_file=log_file,
            log_content=log_content,
            has_failure=(final_status == "failed")
        )

def normalize_name(name):
    if not name:
        return ""
    name = name.strip().lower()
    name = name.replace('-', ' ').replace('_', ' ')
    name = name.replace('(', '').replace(')', '').replace('/', ' ')
    name = ''.join(c for c in name if c.isalnum() or c == ' ')
    return ' '.join(name.split())


class Command(BaseCommand):
    help = "Универсальный импорт Allure: поддерживает suites.csv и data_suites.csv + заполнение allure_uid"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=0)

    def get_environment_info(self, uuid):
        url = f"{BASE_URL}/allure/reports/{uuid}/widgets/environment.json"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            env = {'release_version': '', 'build_version': ''}
            for item in data:
                name = item.get('name', '').strip().upper()
                values = item.get('values', [])
                if values:
                    value = str(values[0]).strip()
                    if 'RELEASE_VERSION' in name:
                        env['release_version'] = value
                    elif 'BUILD_VERSION' in name:
                        env['build_version'] = value
            return env
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Не удалось скачать environment.json для {uuid[:8]}...: {e}"))
            return {'release_version': '', 'build_version': ''}

    def download_csv(self, uuid):
        urls = [
            f"{BASE_URL}/allure/reports/{uuid}/data/suites.csv",
            f"{BASE_URL}/allure/reports/{uuid}/data_suites.csv",
        ]
        for url in urls:
            try:
                r = requests.get(url, timeout=40)
                if r.status_code == 200:
                    source = url.split('/')[-1]
                    return r.text, source
            except:
                continue
        return None, None

    def get_build_date_from_csv(self, csv_content, source):
        if not csv_content:
            return None
        reader = csv.DictReader(io.StringIO(csv_content))
        dates = []
        for row in reader:
            stop = row.get('Stop Time') or row.get('STOP TIME') or row.get('Start Time') or row.get('START TIME')
            if stop and stop.strip() and stop.lower() not in ('null', ''):
                try:
                    dt = dateutil_parser.parse(stop, tzinfos={"MSK": 3*3600})
                    dates.append(dt.date())
                except:
                    continue
        return max(dates) if dates else None

    def get_type_and_arch(self, name):
        n = name.upper()
        if n.startswith('SMK-RUN18-'): return 'Astra 1.8', 'Astra 1.8'
        if n.startswith('SMK-RUN17-'): return 'Astra 1.7', 'Astra 1.7'
        if n.startswith('SMK-RUN47-'): return 'Astra 4.7', 'Astra 4.7'
        if n.startswith('SMK-RUN48-'): return 'Astra 4.8', 'Astra 4.8'
        if n.startswith('SMK-RUN38-'): return 'Astra 3.8', 'Astra 3.8'
        if n.startswith('SMK-HOTFIX18-'): return 'HOT-FIX Astra 1.8', 'HOT-FIX Astra 1.8'
        if n.startswith('SMK-HOTFIX48-'): return 'HOT-FIX Astra 4.8', 'HOT-FIX Astra 4.8'
        if n.startswith('SMK-HOTFIX17-'): return 'HOT-FIX Astra 1.7', 'HOT-FIX Astra 1.7'
        if n.startswith('SMK-HOTFIX47-'): return 'HOT-FIX Astra 4.7', 'HOT-FIX Astra 4.7'
        if n.startswith('SMK-HOTFIX38-'): return 'HOT-FIX Astra 3.8', 'HOT-FIX Astra 3.8'

        return 'Other', 'Other'

    def fetch_allure_test_uids(self, uuid):
        allure_map = {}  # имя → UID
        norm_map = {}    # нормализованное имя → UID

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                SSH_CONFIG['host'],
                username=SSH_CONFIG['username'],
                password=SSH_CONFIG['password']
            )  
            sftp = client.open_sftp()

            test_cases_dir = f"/home/admin/allure/reports/{uuid}/data/test-cases"

            files = sftp.listdir(test_cases_dir)
            self.stdout.write(f"  Найдено файлов в test-cases: {len(files)}")

            for filename in files:
                if not filename.endswith('.json'):
                    continue
                uid = filename.replace('.json', '')
                file_path = f"{test_cases_dir}/{filename}"

                try:
                    local_file = io.BytesIO()
                    sftp.getfo(file_path, local_file)
                    local_file.seek(0)
                    data = json.load(local_file)
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  Ошибка чтения {filename}: {e}"))
                    continue

                status_trace = data.get('statusTrace', '')
                test_name_trace = ''
                if status_trace:
                    for line in status_trace.split('\n'):
                        if 'TEST NAME = ' in line:
                            test_name_trace = line.split('TEST NAME = ')[1].strip()
                            break

                test_name = data.get('name') or data.get('fullName') or test_name_trace or ''
                if not test_name:
                    continue

                allure_map[test_name] = uid
                norm = normalize_name(test_name)
                norm_map[norm] = uid

                if test_name_trace:
                    allure_map[test_name_trace] = uid
                    norm_trace = normalize_name(test_name_trace)
                    norm_map[norm_trace] = uid

            sftp.close()
            client.close()
            self.stdout.write(self.style.SUCCESS(f"  Прочитано {len(allure_map)} уникальных имён"))
            return allure_map, norm_map
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка чтения Allure test-cases: {e}"))
            return {}, {}

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        self.stdout.write("Получаем список отчётов...")
        resp = requests.get(API_REPORT_URL, timeout=30)
        resp.raise_for_status()
        reports = resp.json() if isinstance(resp.json(), list) else resp.json().get('items', [])
        self.stdout.write(self.style.SUCCESS(f"Найдено отчётов: {len(reports)}"))

        imported = skipped = 0

        for idx, rep in enumerate(reports[:limit or None]):
            uuid = rep.get('uuid')
            build_number = rep.get('path') or rep.get('name') or ''
            if not (build_number.startswith('SMK-RUN17-') or build_number.startswith('SMK-RUN18-') or 
                    build_number.startswith('SMK-RUN48-') or build_number.startswith('SMK-RUN38-') or 
                    build_number.startswith('SMK-RUN47-') or build_number.startswith('SMK-HOTFIX18-')):
                continue
            if not uuid or not build_number:
                continue

            if Build.objects.filter(uuid=uuid).exists():
                skipped += 1
                continue

            if dry_run:
                imported += 1
                continue

            try:
                with transaction.atomic():
                    csv_content, csv_source = self.download_csv(uuid)

                    build_date = self.get_build_date_from_csv(csv_content, csv_source)
                    build_type, architecture = self.get_type_and_arch(build_number)
                    env_info = self.get_environment_info(uuid)
                    release_version = env_info.get('release_version', '')
                    build_version = env_info.get('build_version', '')

                    build = Build.objects.create(
                        build_number=str(build_number)[:200],
                        date=build_date,
                        build_type=build_type,
                        uuid=uuid,
                        release_version=release_version,
                        build_version=build_version
                    )

                    test_run = TestRun.objects.create(
                        build=build,
                        architecture=architecture,
                        run_date=build_date,
                        allure_report_url=f"{BASE_URL}/allure/reports/{uuid}/",
                        total_tests=0, passed_tests=0, failed_tests=0, skipped_tests=0,
                    )

                    allure_uid_map, norm_uid_map = self.fetch_allure_test_uids(uuid)

                    p = f = s = 0

                    if csv_content:
                        reader = csv.DictReader(io.StringIO(csv_content))
                        for row in reader:
                            status_raw = row.get('Status') or row.get('STATUS') or 'skipped'
                            status = status_raw.lower()
                            name = (
                                row.get('Test Method') or
                                row.get('Name') or
                                row.get('TEST METHOD') or
                                row.get('NAME') or
                                'unknown'
                            ).strip()[:500]

                            if not name:
                                name = 'empty_name'

                            if status in ['passed', 'pass']:
                                status = 'passed'
                                p += 1
                            elif status in ['failed', 'failure', 'broken']:
                                status = 'failed'
                                f += 1
                            else:
                                status = 'skipped'
                                s += 1

                            test_case = TestCaseRun.objects.create(
                                test_run=test_run,
                                test_name=name,
                                status=status,
                                execution_date=build_date,
                                architecture=architecture,
                            )

                            # Заполняем allure_uid
                            uid_found = False
                            if name in allure_uid_map:
                                test_case.allure_uid = allure_uid_map[name]
                                uid_found = True
                            elif normalize_name(name) in norm_uid_map:
                                test_case.allure_uid = norm_uid_map[normalize_name(name)]
                                uid_found = True

                            if uid_found:
                                test_case.save()   # ← Важно! Сохраняем после присвоения UID
                                self.stdout.write(self.style.SUCCESS(f"  UID {test_case.allure_uid} для '{name}'"))
                            else:
                                self.stdout.write(self.style.WARNING(f"  UID не найден для '{name}'"))

                            # === Парсинг шагов из Allure ===
                            if test_case.allure_uid:
                                try:
                                    json_url = f"http://10.177.140.34:8080/allure/reports/{build.uuid}/data/test-cases/{test_case.allure_uid}.json"
                                    r = requests.get(json_url, timeout=30)
                                    if r.status_code == 200:
                                        json_data = r.json()
                                        parse_test_case_json(test_case, json_data)
                                        self.stdout.write(self.style.SUCCESS(f"  Загружено {test_case.steps.count()} шагов для '{name}'"))
                                    else:
                                        self.stdout.write(self.style.WARNING(f"  Не удалось скачать test-case.json для '{name}' (статус {r.status_code})"))
                                except Exception as e:
                                    self.stdout.write(self.style.WARNING(f"  Ошибка парсинга шагов для '{name}': {e}"))
                            else:
                                self.stdout.write(self.style.WARNING(f"  Нет allure_uid для '{name}' — шаги не загружены"))
                    test_run.total_tests = p + f + s
                    test_run.passed_tests = p
                    test_run.failed_tests = f
                    test_run.skipped_tests = s
                    test_run.save()

                    imported += 1
                    self.stdout.write(self.style.SUCCESS(f" Импортирован! {p}p/{f}f/{s}s"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f" ОШИБКА при сохранении {build_number}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\nГОТОВО! Импортировано: {imported} | Пропущено: {skipped}"))