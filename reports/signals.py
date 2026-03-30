# reports/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import TestCaseRun, TestRun


@receiver(post_save, sender=TestCaseRun)
def update_run_counts(sender, instance, **kwargs):
    """Пересчитываем счётчики в TestRun при изменении любого TestCaseRun"""
    run = instance.test_run

    # Пересчёт
    run.total_tests = run.cases.count()
    run.passed_tests = run.cases.filter(status='passed').count()
    run.failed_tests = run.cases.filter(status='failed').count()
    run.skipped_tests = run.cases.filter(status='skipped').count()

    # Сохраняем без вызова сигналов (чтобы не зациклиться)
    TestRun.objects.filter(id=run.id).update(
        total_tests=run.total_tests,
        passed_tests=run.passed_tests,
        failed_tests=run.failed_tests,
        skipped_tests=run.skipped_tests,
    )