# reports/views.py
from django.db.models import Count, Prefetch
from .models import Build, TestRun, TestCaseRun
from packaging import version
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages


def build_list(request):
    selected_type = request.GET.get('type', 'all')
    selected_release = request.GET.get('release', 'all')

    builds = Build.objects.all().order_by('-date')

    # Фильтр по типу (если выбран)
    if selected_type != 'all':
        builds = builds.filter(build_type=selected_type)

    # Фильтр по версии ОС (только если выбран конкретный тип и версия)
    if selected_type != 'all' and selected_release != 'all':
        builds = builds.filter(release_version=selected_release)

    # Подсчёт количества по типам (всегда показываем только Run17/Run18)
    type_counts = dict(
        Build.objects.values('build_type')
                     .annotate(count=Count('id'))
                     .values_list('build_type', 'count')
    )

    # Список версий ОС — ТОЛЬКО для выбранного типа
    release_list = []
    if selected_type != 'all':
        releases = Build.objects.filter(build_type=selected_type)\
                                .values_list('release_version', flat=True)\
                                .exclude(release_version='')\
                                .distinct()
        # Сортировка по последнему числу (1.7.8 → 1.7.9 → 1.7.10)
        def get_last_number(v):
            if not v or '.' not in v:
                return -1
            try:
                return int(v.split('.')[-1])
            except:
                return -1

        release_list = sorted(releases, key=get_last_number)  # по возрастанию
        # release_list = sorted(releases, key=get_last_number, reverse=True)  # по убыванию

    context = {
        'builds': builds,
        'selected_type': selected_type,
        'selected_release': selected_release,
        'type_counts': type_counts,
        'release_list': release_list,
        'total_builds': Build.objects.count(),
        'show_release_filter': selected_type != 'all',
    }
    return render(request, 'reports/build_list.html', context)


def build_detail(request, build_id):
    """Детальная страница билда — со всеми тестами"""
    build = get_object_or_404(Build, id=build_id)
    
    # Подгружаем все тесты сразу — без N+1 запросов
    test_runs = build.test_runs.all().prefetch_related(
        Prefetch('cases', queryset=TestCaseRun.objects.all())
    )
    
    return render(request, 'reports/build_detail.html', {
        'build': build,
        'test_runs': test_runs,
    })


def test_history(request, test_name, architecture):
    """История конкретного теста — только по выбранной архитектуре"""
    runs = TestCaseRun.objects.filter(
        test_name=test_name,
        architecture=architecture
    ).select_related('test_run__build').order_by('-execution_date')
    
    versions = runs.values(
        'test_run__build__release_version',
        'test_run__build__build_version'
    ).distinct()

    context = {
        'test_name': test_name,
        'architecture': architecture,
        'runs': runs,
        'total_runs': runs.count(),
        'passed_count': runs.filter(status='passed').count(),
        'failed_count': runs.filter(status='failed').count(),
        'skipped_count': runs.filter(status='skipped').count(),
        'versions': versions,
    }
    return render(request, 'reports/test_history.html', context)





def edit_tests(request, build_id):
    build = get_object_or_404(Build, id=build_id)

    if request.method == 'POST':
        # Пример обработки формы (можно расширить)
        for key, value in request.POST.items():
            if key.startswith('status_'):
                test_id = key.split('_')[1]
                try:
                    test = TestCaseRun.objects.get(id=test_id, test_run__build=build)
                    test.status = value
                    test.save()
                except TestCaseRun.DoesNotExist:
                    pass
        messages.success(request, "Статусы тестов обновлены")
        return redirect('admin:reports_build_change', build.pk)

    test_cases = TestCaseRun.objects.filter(test_run__build=build)

    context = {
        'build': build,
        'test_cases': test_cases,
        'opts': build._meta,
        'original': build,
    }
    return render(request, 'admin/reports/build/edit_tests.html', context)


def test_detail(request, test_case_id):
    test_case = get_object_or_404(TestCaseRun, id=test_case_id)
    steps = test_case.steps.all()   # related_name='steps'

    context = {
        'test_case': test_case,
        'steps': steps,
        'build': test_case.test_run.build,
    }
    return render(request, 'reports/test_detail.html', context)