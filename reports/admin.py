from django.contrib import admin
from django.urls import path
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
import zipfile
import io
import csv
import json
import requests
from datetime import datetime, timezone
from .models import Build, TestRun, TestCaseRun


class TestCaseRunInline(admin.TabularInline):
    model = TestCaseRun
    extra = 0
    fields = ('test_name', 'status', 'execution_date', 'architecture')
    readonly_fields = ('test_name', 'execution_date', 'architecture')
    list_editable = ('status',)  # статус меняется прямо в таблице
    can_delete = True
    show_change_link = False
    ordering = ('test_name',)


class TestRunInline(admin.TabularInline):
    model = TestRun
    extra = 0
    fields = ('architecture', 'run_date', 'allure_report_url',
              'total_tests', 'passed_tests', 'failed_tests', 'skipped_tests')
    inlines = [TestCaseRunInline]
    show_change_link = False
    classes = ['collapse']


@admin.register(Build)
class BuildAdmin(admin.ModelAdmin):
    list_display = ('build_number', 'date', 'build_type', 'release_version', 'build_version')
    list_filter = ('build_type', 'release_version', 'date')
    search_fields = ('build_number', 'release_version', 'build_version', 'uuid')
    readonly_fields = ('uuid',)

    fieldsets = (
        ('Основная информация', {
            'fields': ('build_number', 'date', 'build_type', 'uuid')
        }),
        ('Версии', {
            'fields': ('release_version', 'build_version')
        }),
    )

    inlines = [TestRunInline]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:build_id>/edit-tests/', self.admin_site.admin_view(self.edit_tests_view), name='reports_build_edit_tests'),
            path('<int:build_id>/sync-allure/', self.admin_site.admin_view(self.sync_allure_view), name='reports_build_sync_allure'),
        ]
        return custom_urls + urls

    def edit_tests_view(self, request, build_id):
        build = get_object_or_404(Build, id=build_id)
        tests = TestCaseRun.objects.filter(test_run__build=build).order_by('test_name')

        if request.method == 'POST':
            for test in tests:
                status_key = f'status_{test.id}'
                if status_key in request.POST:
                    test.status = request.POST[status_key]
                    test.save()

            self.message_user(request, "Статусы тестов успешно обновлены.")
            return redirect('admin:reports_build_change', build_id)

        context = dict(
            self.admin_site.each_context(request),
            title=f"Редактирование тестов билда {build.build_number}",
            build=build,
            tests=tests,
        )
        return render(request, 'admin/reports/build/edit_tests.html', context)

    def sync_allure_view(self, request, build_id):
        from .allure_auto_edit import update_allure_report_ssh

        try:
            update_allure_report_ssh(build_id)
            self.message_user(request, "Отчёт успешно обновлён на сервере")
        except Exception as e:
            self.message_user(request, f"Ошибка: {str(e)}", level='error')

        return redirect('admin:reports_build_change', build_id)