import paramiko
import io
import json
import csv
from reports.models import Build, TestCaseRun
from reports.config import SSH_CONFIG


def normalize_name(name):
    if not name:
        return ""
    name = name.strip().lower()
    name = name.replace('-', ' ').replace('_', ' ')
    name = name.replace('(', '').replace(')', '').replace('/', ' ')
    name = ''.join(c for c in name if c.isalnum() or c == ' ')
    return ' '.join(name.split())


def update_allure_report_ssh(
    build_id,
    allure_base_path='/home/admin/allure/reports'
):
    build = Build.objects.get(id=build_id)
    uuid = build.uuid
    if not uuid:
        print("Нет UUID")
        return

    print(f"Обновление отчёта для билда {build_id}, UUID: {uuid}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        SSH_CONFIG['host'],
        username=SSH_CONFIG['username'],
        password=SSH_CONFIG['password']
    )
    sftp = client.open_sftp()

    base_dir = f"{allure_base_path}/{uuid}"
    data_dir = f"{base_dir}/data"
    test_cases_dir = f"{data_dir}/test-cases"

    updated_uids = []       # UID тестов, которые реально изменились
    updated_files = []      # файлы, которые были обновлены

    # Берём все тесты этого билда
    test_cases = TestCaseRun.objects.filter(test_run__build=build)

    print(f"Найдено тестов в БД: {test_cases.count()}")

    for tc in test_cases:
        db_status = tc.status.lower()
        found = False

        try:
            for filename in sftp.listdir(test_cases_dir):
                if not filename.endswith('.json'):
                    continue
                uid = filename.replace('.json', '')
                file_path = f"{test_cases_dir}/{filename}"

                local_file = io.BytesIO()
                sftp.getfo(file_path, local_file)
                local_file.seek(0)
                data = json.load(local_file)

                # 1. Имя из statusTrace (самое точное)
                status_trace = data.get('statusTrace', '')
                test_name_trace = ''
                if status_trace:
                    for line in status_trace.split('\n'):
                        if 'TEST NAME = ' in line:
                            test_name_trace = line.split('TEST NAME = ')[1].strip()
                            break

                # 2. Основное имя
                test_name = data.get('name') or data.get('fullName') or test_name_trace or ''

                # Сравниваем (гибко, но достаточно строго)
                if (tc.test_name == test_name or
                    tc.test_name == test_name_trace or
                    tc.test_name in test_name or
                    test_name in tc.test_name or
                    test_name_trace == tc.test_name):

                    old_status = data.get('status', '').lower()
                    if old_status != db_status:
                        data['status'] = db_status
                        new_buffer = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
                        new_buffer.seek(0)
                        sftp.putfo(new_buffer, file_path)
                        updated_uids.append(uid)
                        print(f" → ОБНОВЛЁН: '{tc.test_name}' → {db_status} (uid: {uid}) | было: {old_status}")
                    else:
                        print(f" → статус совпадает: '{tc.test_name}' ({old_status})")
                    found = True
                    break

            if not found:
                print(f" → НЕ НАЙДЕН JSON для '{tc.test_name}'")
        except Exception as e:
            print(f"Ошибка обработки '{tc.test_name}': {e}")

    if not updated_uids:
        print("Нет изменений — ничего не обновлено")
        sftp.close()
        client.close()
        return

    print(f"Обновлено тестов в Allure: {len(updated_uids)}")

    # Обновляем suites.csv
    try:
        csv_path = f"{data_dir}/suites.csv"
        local_csv = io.BytesIO()
        sftp.getfo(csv_path, local_csv)
        local_csv.seek(0)
        lines = local_csv.read().decode('utf-8').splitlines()
        reader = csv.DictReader(lines)
        new_rows = []
        changed = False

        for row in reader:
            name = row.get('Name') or row.get('Test Name') or ''
            norm = normalize_name(name)
            for tc in test_cases:
                if normalize_name(tc.test_name) == norm:
                    new_status = tc.status.upper()
                    old = row.get('Status', '').lower()
                    if old != tc.status.lower():
                        row['Status'] = new_status
                        changed = True
                        print(f"suites.csv: '{name}' → {new_status}")
                    break
            new_rows.append(row)

        if changed:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(new_rows)
            new_buffer = io.BytesIO(output.getvalue().encode('utf-8'))
            new_buffer.seek(0)
            sftp.putfo(new_buffer, csv_path)
            updated_files.append("suites.csv")
            print("suites.csv обновлён")
    except Exception as e:
        print(f"Ошибка suites.csv: {e}")

    # Обновляем suites.json
    try:
        suites_json_path = f"{data_dir}/suites.json"
        local_suites = io.BytesIO()
        sftp.getfo(suites_json_path, local_suites)
        local_suites.seek(0)
        suites_data = json.load(local_suites)

        for suite in suites_data.get('children', []):
            for test in suite.get('children', []):
                uid = test.get('uid')
                if uid in updated_uids:
                    try:
                        tc = TestCaseRun.objects.get(allure_uid=uid)
                        test['status'] = tc.status
                        print(f"suites.json: обновлён статус для '{tc.test_name}' (uid: {uid})")
                    except TestCaseRun.DoesNotExist:
                        print(f"suites.json: НЕ НАЙДЕН TestCaseRun с UID {uid} — статус не обновлён")
                        continue

        new_buffer = io.BytesIO(json.dumps(suites_data, ensure_ascii=False, indent=2).encode('utf-8'))
        new_buffer.seek(0)
        sftp.putfo(new_buffer, suites_json_path)
        updated_files.append("suites.json")
        print("suites.json обновлён")
    except Exception as e:
        print(f"Ошибка suites.json: {e}")

    # Обновляем categories.json
    try:
        categories_path = f"{data_dir}/categories.json"
        local_categories = io.BytesIO()
        sftp.getfo(categories_path, local_categories)
        local_categories.seek(0)
        categories_data = json.load(local_categories)

        changes = 0

        def find_and_remove_by_uid(node, target_uid):
            nonlocal changes
            if not isinstance(node, dict) or 'children' not in node:
                return None
            for i in range(len(node['children'])):
                child = node['children'][i]
                if isinstance(child, dict) and child.get('uid') == target_uid:
                    removed = node['children'].pop(i)
                    try:
                        tc = TestCaseRun.objects.get(allure_uid=target_uid)
                        removed['status'] = tc.status
                        changes += 1
                        print(f"Тест uid '{target_uid}' удалён из '{node.get('name')}' (статус обновлён)")
                    except TestCaseRun.DoesNotExist:
                        print(f"Тест uid '{target_uid}' удалён из '{node.get('name')}', но НЕ НАЙДЕН в БД — статус внутри НЕ обновлён")
                    return removed
                removed = find_and_remove_by_uid(child, target_uid)
                if removed:
                    return removed
            return None

        def add_to_subcategory(node, target_name, test_obj):
            nonlocal changes
            if not isinstance(node, dict):
                return False
            if node.get('name') == target_name and 'children' in node:
                if not any(c.get('uid') == test_obj.get('uid') for c in node['children']):
                    node['children'].append(test_obj)
                    changes += 1
                    print(f"Тест uid '{test_obj.get('uid')}' добавлен в '{target_name}'")
                return True
            if 'children' in node:
                for child in node['children']:
                    if add_to_subcategory(child, target_name, test_obj):
                        return True
            return False

        print("\n=== Перенос в categories.json ===")
        for uid in updated_uids:
            removed = None
            for root in categories_data if isinstance(categories_data, list) else [categories_data]:
                removed = find_and_remove_by_uid(root, uid)
                if removed:
                    break
            if removed:
                try:
                    tc = TestCaseRun.objects.get(allure_uid=uid)
                    target = {'failed': 'Failed tests', 'passed': 'Passed tests', 'skipped': 'Skipped tests'}.get(tc.status.lower())
                    if target:
                        for root in categories_data if isinstance(categories_data, list) else [categories_data]:
                            add_to_subcategory(root, target, removed)
                except TestCaseRun.DoesNotExist:
                    print(f"Не удалось обновить категорию для UID {uid} — тест не найден в БД")

        if changes > 0:
            new_buffer = io.BytesIO(json.dumps(categories_data, ensure_ascii=False, indent=2).encode('utf-8'))
            new_buffer.seek(0)
            sftp.putfo(new_buffer, categories_path)
            updated_files.append("categories.json")
            print("categories.json обновлён")
        else:
            print("categories.json не требует изменений")
    except Exception as e:
        print(f"Ошибка categories.json: {e}")



       # Обновляем виджеты: suites.json, categories.json, summary.json

    widgets_dir = f"{base_dir}/widgets"

    try:
        # 1. Обновляем suites.json (папки/группы тестов)
        suites_widget_path = f"{widgets_dir}/suites.json"
        try:
            local_suites = io.BytesIO()
            sftp.getfo(suites_widget_path, local_suites)
            local_suites.seek(0)
            suites_widget = json.load(local_suites)

            # Пересчитываем статистику по всем suite
            for suite in suites_widget.get('children', []):
                suite_stat = suite.get('statistic', {})
                old_passed = suite_stat.get('passed', 0)
                old_failed = suite_stat.get('failed', 0)
                old_skipped = suite_stat.get('skipped', 0)

                # Собираем UID тестов в этой suite
                suite_uids = []
                for test in suite.get('children', []):
                    uid = test.get('uid')
                    if uid:
                        suite_uids.append(uid)

                # Считаем актуальные статусы по БД
                passed = TestCaseRun.objects.filter(
                    test_run__build=build,
                    allure_uid__in=suite_uids,
                    status='passed'
                ).count()
                failed = TestCaseRun.objects.filter(
                    test_run__build=build,
                    allure_uid__in=suite_uids,
                    status='failed'
                ).count()
                skipped = TestCaseRun.objects.filter(
                    test_run__build=build,
                    allure_uid__in=suite_uids,
                    status='skipped'
                ).count()
                total = passed + failed + skipped

                if (suite_stat.get('passed') != passed or
                    suite_stat.get('failed') != failed or
                    suite_stat.get('skipped') != skipped or
                    suite_stat.get('total') != total):
                    suite_stat['passed'] = passed
                    suite_stat['failed'] = failed
                    suite_stat['skipped'] = skipped
                    suite_stat['broken'] = 0
                    suite_stat['total'] = total
                    print(f"suites.json: обновлена статистика группы '{suite.get('name')}': "
                          f"p={passed}, f={failed}, s={skipped}, t={total}")

            new_buffer = io.BytesIO(json.dumps(suites_widget, ensure_ascii=False, indent=2).encode('utf-8'))
            new_buffer.seek(0)
            sftp.putfo(new_buffer, suites_widget_path)
            updated_files.append("widgets/suites.json")
            print("widgets/suites.json обновлён")
        except FileNotFoundError:
            print("widgets/suites.json не найден — пропускаем")
        except Exception as e:
            print(f"Ошибка widgets/suites.json: {e}")

        # 2. Обновляем categories.json (Failed/Passed/Skipped)
        categories_path = f"{widgets_dir}/categories.json"
        try:
            local_categories = io.BytesIO()
            sftp.getfo(categories_path, local_categories)
            local_categories.seek(0)
            categories_data = json.load(local_categories)

            # Пересчитываем количество по БД
            passed_count = TestCaseRun.objects.filter(test_run__build=build, status='passed').count()
            failed_count = TestCaseRun.objects.filter(test_run__build=build, status='failed').count()
            skipped_count = TestCaseRun.objects.filter(test_run__build=build, status='skipped').count()

            for item in categories_data.get('items', []):
                name = item.get('name', '')
                if 'Passed' in name:
                    if item['statistic']['passed'] != passed_count:
                        item['statistic']['passed'] = passed_count
                        item['statistic']['total'] = passed_count
                        print(f"categories.json: Passed → {passed_count}")
                elif 'Failed' in name:
                    if item['statistic']['failed'] != failed_count:
                        item['statistic']['failed'] = failed_count
                        item['statistic']['total'] = failed_count
                        print(f"categories.json: Failed → {failed_count}")
                elif 'Skipped' in name:
                    if item['statistic']['skipped'] != skipped_count:
                        item['statistic']['skipped'] = skipped_count
                        item['statistic']['total'] = skipped_count
                        print(f"categories.json: Skipped → {skipped_count}")

            new_buffer = io.BytesIO(json.dumps(categories_data, ensure_ascii=False, indent=2).encode('utf-8'))
            new_buffer.seek(0)
            sftp.putfo(new_buffer, categories_path)
            updated_files.append("widgets/categories.json")
            print("widgets/categories.json обновлён")
        except FileNotFoundError:
            print("widgets/categories.json не найден — пропускаем")
        except Exception as e:
            print(f"Ошибка widgets/categories.json: {e}")

        # 3. Обновляем summary.json (общая статистика)
        summary_path = f"{widgets_dir}/summary.json"
        try:
            local_summary = io.BytesIO()
            sftp.getfo(summary_path, local_summary)
            local_summary.seek(0)
            summary_data = json.load(local_summary)

            # Пересчитываем по БД
            passed = TestCaseRun.objects.filter(test_run__build=build, status='passed').count()
            failed = TestCaseRun.objects.filter(test_run__build=build, status='failed').count()
            skipped = TestCaseRun.objects.filter(test_run__build=build, status='skipped').count()
            total = passed + failed + skipped

            stat = summary_data.get('statistic', {})
            if (stat.get('passed') != passed or
                stat.get('failed') != failed or
                stat.get('skipped') != skipped or
                stat.get('total') != total):
                stat['passed'] = passed
                stat['failed'] = failed
                stat['skipped'] = skipped
                stat['broken'] = 0
                stat['unknown'] = 0
                stat['total'] = total
                print(f"summary.json обновлён: p={passed}, f={failed}, s={skipped}, t={total}")

                new_buffer = io.BytesIO(json.dumps(summary_data, ensure_ascii=False, indent=2).encode('utf-8'))
                new_buffer.seek(0)
                sftp.putfo(new_buffer, summary_path)
                updated_files.append("widgets/summary.json")
                print("widgets/summary.json обновлён")
            else:
                print("summary.json не требует изменений")

        except FileNotFoundError:
            print("widgets/summary.json не найден — пропускаем")
        except Exception as e:
            print(f"Ошибка widgets/summary.json: {e}")

    except Exception as e:
        print(f"Общая ошибка при обновлении виджетов: {e}")







    sftp.close()
    client.close()
    print(f"Синхронизация завершена. Обновлено файлов: {len(updated_files)} ({', '.join(updated_files)})")