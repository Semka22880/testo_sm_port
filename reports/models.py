from django.db import models

class Build(models.Model):
    build_number = models.CharField(max_length=50)  # Номер билда, e.g., 'SMK-MOB-T17-593'
    date = models.DateField(null = True, blank = True)  # Дата билда
    created_at = models.DateTimeField(auto_now_add=True)
    uuid = models.CharField(max_length=50, unique = True, blank=True, null = True)
    build_type=models.CharField(max_length=59, default='Other', db_index=True)
    release_version = models.CharField(max_length=50, blank=True, default='')
    build_version   = models.CharField(max_length=50, blank=True, default='')

    def __str__(self):
        return f"Build {self.build_number} ({self.date})"

class TestRun(models.Model):
    build = models.ForeignKey(
        Build,
        on_delete=models.CASCADE,
        related_name='test_runs'
    )
    architecture = models.CharField(max_length=50)
    run_date = models.DateField(null =True, blank = True)
    allure_report_url = models.URLField()
    total_tests = models.IntegerField(default=0)
    passed_tests = models.IntegerField(default=0)
    failed_tests = models.IntegerField(default=0)
    skipped_tests = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.build.build_number} — {self.architecture}"

class TestCaseRun(models.Model):
    test_run = models.ForeignKey(
        TestRun,
        on_delete=models.CASCADE,
        related_name='cases'
    )
    test_name = models.CharField(max_length=500)
    status = models.CharField(max_length=20)
    execution_date = models.DateField()
    architecture = models.CharField(max_length=50, default='Other')
    allure_uid = models.CharField(max_length=50, null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.test_name} — {self.status}"


class TestStepLog(models.Model):
    test_case = models.ForeignKey('TestCaseRun', on_delete=models.CASCADE, related_name='steps')
    step_name = models.TextField()
    status = models.CharField(max_length=50)
    start_time = models.DateTimeField(null=True, blank=True)
    stop_time = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(null=True, blank=True)
    order = models.IntegerField(default=0)

    screenshot = models.CharField(max_length=255, blank=True, null=True)
    log_file = models.CharField(max_length=255, blank=True, null=True)
    log_content = models.TextField(blank=True, null=True)   # ← новое поле

    has_failure = models.BooleanField(default=False)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.test_case.test_name} → {self.step_name[:80]}"